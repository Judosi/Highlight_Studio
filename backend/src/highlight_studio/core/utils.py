from __future__ import annotations

import ast
import errno
import json
import math
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any


def safe_name(text: str, fallback: str = "file") -> str:
    text = (text or "").strip() or fallback
    text = re.sub(r'[\\/:*?"<>|]+', "_", text)
    text = re.sub(r"\s+", "_", text)
    text = re.sub(r"[^0-9A-Za-zА-Яа-яЁё._-]+", "_", text)
    return text[:100].strip("._-") or fallback


def tc(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:05.2f}"


def _repair_json_text(candidate: str) -> str:
    """Best-effort repair for common malformed JSON returned by local LLMs.

    This intentionally stays conservative: it fixes typical Ollama/Qwen mistakes
    such as missing commas between object fields/items, trailing commas, Python
    booleans and markdown/code fences.  It does not try to invent content.
    """
    r = (candidate or "").strip()
    r = re.sub(r"^```(?:json)?\s*", "", r, flags=re.I)
    r = re.sub(r"\s*```$", "", r)
    r = r.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")
    r = re.sub(r"\bTrue\b", "true", r)
    r = re.sub(r"\bFalse\b", "false", r)
    r = re.sub(r"\bNone\b", "null", r)
    r = re.sub(r",\s*([}\]])", r"\1", r)
    # Missing comma between objects in an array: {...}{...}
    r = re.sub(r"}\s*{", "},{", r)
    # Missing comma between value and next known key: "score": 8 "decision": ...
    known_keys = (
        "id|score|decision|title|reason|hook_potential|context_before_seconds|"
        "context_after_seconds|moment_type|standalone_clarity|keep|extra_start_seconds|"
        "extra_end_seconds|blocks|clips|items|results|segments"
    )
    r = re.sub(rf"(?<=[0-9\"}}\]])\s+(?=\"(?:{known_keys})\"\s*:)", ", ", r)
    # Missing comma after a closing array/object before next key.
    r = re.sub(rf"(?<=[}}\]])\s+(?=\"(?:{known_keys})\"\s*:)", ", ", r)
    return r


def parse_json_loose(text: str) -> dict[str, Any]:
    """Parse model JSON with repair attempts for local LLM/VL outputs."""
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text)
    candidates: list[str] = [text]

    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        candidates.append(text[start : end + 1])

    lb = text.find("[")
    rb = text.rfind("]")
    if lb >= 0 and rb > lb:
        candidates.append(text[lb : rb + 1])

    repaired = [_repair_json_text(c) for c in candidates]

    last_exc = None
    for c in candidates + repaired:
        try:
            parsed = json.loads(c)
            if isinstance(parsed, list):
                return {"items": parsed}
            return parsed
        except Exception as exc:
            last_exc = exc

    # Last fallback: parse a top-level list after repair.
    if lb >= 0 and rb > lb:
        try:
            return {"items": json.loads(_repair_json_text(text[lb : rb + 1]))}
        except Exception as exc:
            last_exc = exc

    # Qwen occasionally emits a Python-literal-like object (single quotes,
    # True/False/None). ast.literal_eval is deterministic and does not execute
    # arbitrary code, so it is a safe local repair before spending another
    # expensive model inference on the same semantic request.
    for c in candidates + repaired:
        try:
            parsed = ast.literal_eval(c)
            if isinstance(parsed, list):
                return {"items": parsed}
            if isinstance(parsed, dict):
                return parsed
        except Exception as exc:
            last_exc = exc

    raise ValueError("Cannot parse JSON from model response" + (f": {last_exc}" if last_exc else ""))


CRITICAL_JSON_FILENAMES = {
    "project.json",
    "status.json",
    "segments.json",
    "candidates.json",
    "twitch_import.json",
    "cache_manifest.json",
    "quality_report.json",
    "pre_render_check.json",
    "youtube_metadata.json",
}


def _json_backup_path(path: Path) -> Path:
    return path.with_name(path.name + ".bak")


def _json_tmp_path(path: Path) -> Path:
    tid = threading.get_ident()
    return path.with_name(f".{path.name}.{os.getpid()}.{tid}.tmp")


_JSON_LOCKS_GUARD = threading.Lock()
_JSON_LOCKS: dict[str, threading.RLock] = {}
_TRANSIENT_WINDOWS_FILE_ERRORS = {5, 32, 33}
_TRANSIENT_FILE_ERRNOS = {errno.EACCES, errno.EPERM, errno.EBUSY}


def _json_write_lock(path: Path) -> threading.RLock:
    """Return a process-local lock for one JSON destination.

    Progress updates can arrive from downloader/output monitor threads at the
    same time.  On Windows, one thread copying ``status.json`` to ``.bak`` can
    temporarily prevent another thread from replacing the destination.
    Serialising the complete backup+replace transaction removes that race.
    """
    try:
        key = os.path.normcase(str(path.resolve(strict=False)))
    except Exception:
        key = os.path.normcase(str(path.absolute()))
    with _JSON_LOCKS_GUARD:
        return _JSON_LOCKS.setdefault(key, threading.RLock())


def is_transient_file_lock_error(exc: BaseException) -> bool:
    """Whether *exc* looks like a temporary Windows/AV file lock.

    WinError 5/32/33 are commonly raised while antivirus, an indexer or another
    app thread briefly has the destination open.  EACCES/EPERM/EBUSY cover the
    portable equivalents used by tests and non-Windows filesystems.
    """
    if not isinstance(exc, OSError):
        return False
    return (
        isinstance(exc, PermissionError)
        or getattr(exc, "errno", None) in _TRANSIENT_FILE_ERRNOS
        or getattr(exc, "winerror", None) in _TRANSIENT_WINDOWS_FILE_ERRORS
    )


def _replace_with_retry(src: Path, dst: Path, *, attempts: int = 12) -> None:
    """Atomically replace *dst*, retrying short-lived Windows locks.

    The total wait is bounded to a few seconds.  Non-lock errors are raised
    immediately so real permission or disk failures are never hidden.
    """
    last_exc: OSError | None = None
    for attempt in range(max(1, attempts)):
        try:
            os.replace(src, dst)
            return
        except OSError as exc:
            if not is_transient_file_lock_error(exc):
                raise
            last_exc = exc
            if attempt >= attempts - 1:
                break
            time.sleep(min(0.5, 0.015 * (2**attempt)))
    assert last_exc is not None
    raise last_exc


def replace_file_atomic(src: Path, dst: Path, *, attempts: int = 12) -> None:
    """Public atomic replacement helper for validated media/artifacts."""
    _replace_with_retry(Path(src), Path(dst), attempts=attempts)


def json_safe_value(value: Any) -> Any:
    """Return a recursively JSON-safe value.

    Python's json module accepts NaN/Infinity by default even though they are
    invalid JSON and Starlette intentionally refuses to serialize them. One
    corrupt score or timestamp used to make several diagnostics endpoints fail
    with HTTP 500. Non-finite numbers are represented as ``null`` instead.
    """
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {key: json_safe_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe_value(item) for item in value]
    return value


def _load_json_file(path: Path) -> Any:
    return json_safe_value(json.loads(path.read_text(encoding="utf-8")))


def read_json(path: Path, default: Any = None) -> Any:
    """Read JSON safely, with automatic fallback to a .bak copy.

    Long VOD jobs can run for hours. A forced shutdown during a write should not
    make the whole project unusable.  `write_json()` stores important JSON files
    atomically and keeps a small `.bak`; this reader returns that backup if the
    main file is missing/corrupted.
    """
    try:
        return _load_json_file(path)
    except Exception:
        backup = _json_backup_path(path)
        if backup.exists():
            try:
                return _load_json_file(backup)
            except Exception:
                pass
        return default


def write_json(path: Path, data: Any) -> None:
    """Atomic JSON write with backup, serialisation and Windows retries.

    This prevents half-written project files and avoids aborting a long Twitch
    job when Windows Defender, Explorer or another progress thread briefly has
    ``status.json`` open.  Persistent/non-lock failures still propagate.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(json_safe_value(data), ensure_ascii=False, indent=2, allow_nan=False)
    lock = _json_write_lock(path)
    with lock:
        tmp = _json_tmp_path(path)
        try:
            with tmp.open("w", encoding="utf-8") as f:
                f.write(text)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except Exception:
                    pass
            if path.exists() and path.name in CRITICAL_JSON_FILENAMES:
                backup_tmp = _json_tmp_path(_json_backup_path(path))
                try:
                    # Never replace the last good backup with a corrupt main
                    # file. Stage the backup too so a partial copy is harmless.
                    _load_json_file(path)
                    shutil.copy2(path, backup_tmp)
                    _replace_with_retry(backup_tmp, _json_backup_path(path))
                except Exception:
                    pass
                finally:
                    backup_tmp.unlink(missing_ok=True)
            _replace_with_retry(tmp, path)
        finally:
            try:
                if tmp.exists():
                    tmp.unlink()
            except Exception:
                pass


def json_file_health(path: Path) -> dict[str, Any]:
    """Return a small integrity report for a JSON file and its backup."""
    backup = _json_backup_path(path)
    report: dict[str, Any] = {
        "path": str(path),
        "name": path.name,
        "exists": path.exists(),
        "backup_exists": backup.exists(),
        "valid": False,
        "backup_valid": False,
        "recoverable": False,
        "size_bytes": path.stat().st_size if path.exists() else 0,
        "backup_size_bytes": backup.stat().st_size if backup.exists() else 0,
        "error": "",
        "backup_error": "",
    }
    if path.exists():
        try:
            _load_json_file(path)
            report["valid"] = True
        except Exception as exc:
            report["error"] = str(exc)[:500]
    if backup.exists():
        try:
            _load_json_file(backup)
            report["backup_valid"] = True
        except Exception as exc:
            report["backup_error"] = str(exc)[:500]
    report["recoverable"] = (not report["valid"]) and bool(report["backup_valid"])
    return report


def repair_json_from_backup(path: Path) -> dict[str, Any]:
    """Restore a corrupted/missing JSON file from its .bak copy when possible."""
    health = json_file_health(path)
    if health.get("valid"):
        return {"ok": True, "changed": False, "message": f"{path.name} уже валиден.", "health": health}
    if not health.get("backup_valid"):
        return {"ok": False, "changed": False, "message": f"Нет валидного backup для {path.name}.", "health": health}
    backup = _json_backup_path(path)
    if path.exists():
        stamp = int(time.time())
        corrupt = path.with_name(f"{path.name}.corrupt.{stamp}")
        try:
            os.replace(path, corrupt)
        except Exception:
            shutil.copy2(path, corrupt)
    shutil.copy2(backup, path)
    return {"ok": True, "changed": True, "message": f"{path.name} восстановлен из backup.", "health": json_file_health(path)}


_PROCESS_LOCK = threading.Lock()
_RUNNING_PROCESSES: dict[str, list[subprocess.Popen[str]]] = {}


class OperationCancelled(RuntimeError):
    """Raised when a user-requested cancellation stops a long operation."""


def _process_registry_path(project_dir: Path | str | None) -> Path | None:
    return None if project_dir is None else Path(project_dir) / "running_processes.json"


def _read_process_command(pid: int) -> str:
    if pid <= 0:
        return ""
    if os.name == "nt":
        ps = ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", f"$p=Get-CimInstance Win32_Process -Filter 'ProcessId={pid}' -ErrorAction SilentlyContinue; if($p){{$p.CommandLine}}"]
        try:
            r=subprocess.run(ps,capture_output=True,text=True,timeout=8,encoding="utf-8",errors="replace")
            return (r.stdout or "").strip()
        except Exception:
            return ""
    try:
        return Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\x00",b" ").decode("utf-8","replace").strip()
    except Exception:
        return ""


def _read_process_start_marker(pid: int) -> str:
    """Return an OS-level process creation marker to defend against PID reuse.

    The marker is advisory: a missing marker never authorizes termination by
    itself.  Reconciliation still requires the recorded project path in both
    the persisted command and the live command line.
    """
    if pid <= 0:
        return ""
    if os.name == "nt":
        command = [
            "powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
            f"$p=Get-Process -Id {pid} -ErrorAction SilentlyContinue; if($p){{$p.StartTime.ToUniversalTime().Ticks}}",
        ]
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=8, encoding="utf-8", errors="replace")
            return (result.stdout or "").strip()
        except Exception:
            return ""
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
        # Field 2 (comm) is parenthesized and may contain spaces.  Fields after
        # the final ')' start at field 3; starttime is field 22 => index 19.
        rest = raw.rsplit(")", 1)[1].strip().split()
        return rest[19] if len(rest) > 19 else ""
    except Exception:
        return ""


def _persist_process_registry(project_dir: Path | str | None, proc: subprocess.Popen[str], cmd: list[str], add: bool) -> None:
    path=_process_registry_path(project_dir)
    if path is None:
        return
    try:
        items=read_json(path,[]) or []
        items=items if isinstance(items,list) else []
        items=[x for x in items if int(x.get("pid",-1)) != int(proc.pid)]
        if add:
            items.append({
                "pid": int(proc.pid),
                "created_at": time.time(),
                "process_start_marker": _read_process_start_marker(int(proc.pid)),
                "command": [str(x) for x in cmd],
                "project_dir": str(Path(project_dir).resolve()),
            })
        if items:
            write_json(path,items)
        else:
            path.unlink(missing_ok=True)
    except Exception:
        pass


def register_project_process(project_dir: Path | str | None, proc: subprocess.Popen[str], cmd: list[str]) -> None:
    key=_project_key(project_dir)
    if key:
        with _PROCESS_LOCK:
            _RUNNING_PROCESSES.setdefault(key,[]).append(proc)
    _persist_process_registry(project_dir,proc,cmd,True)


def unregister_project_process(project_dir: Path | str | None, proc: subprocess.Popen[str], cmd: list[str]) -> None:
    key=_project_key(project_dir)
    if key:
        with _PROCESS_LOCK:
            current=_RUNNING_PROCESSES.get(key,[])
            if proc in current:
                current.remove(proc)
            if not current:
                _RUNNING_PROCESSES.pop(key,None)
    _persist_process_registry(project_dir,proc,cmd,False)


def reconcile_orphaned_processes(project_dir: Path | str) -> int:
    """Terminate only recorded children whose live command still belongs to this project."""
    path=_process_registry_path(project_dir)
    if path is None:
        return 0
    items=read_json(path,[]) or []
    project_text=os.path.normcase(str(Path(project_dir).resolve()))
    killed=0
    for item in items if isinstance(items,list) else []:
        try:
            pid=int(item.get("pid") or 0)
            if pid<=0 or pid==os.getpid():
                continue
            cmdline = _read_process_command(pid)
            expected = " ".join(str(x) for x in item.get("command") or [])
            recorded_marker = str(item.get("process_start_marker") or "").strip()
            live_marker = _read_process_start_marker(pid) if recorded_marker else ""
            if recorded_marker and live_marker != recorded_marker:
                # PID was reused or the process cannot be proven to be the one
                # we spawned. Never terminate it.
                continue
            if not cmdline or project_text not in os.path.normcase(cmdline) or project_text not in os.path.normcase(expected):
                continue
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=10)
            else:
                os.kill(pid, 15)
            killed += 1
        except (ProcessLookupError,FileNotFoundError):
            pass
        except Exception:
            pass
    try:
        path.unlink(missing_ok=True)
    except Exception:
        pass
    return killed


def _project_key(project_dir: Path | str | None) -> str | None:
    if project_dir is None:
        return None
    try:
        return str(Path(project_dir).resolve())
    except Exception:
        return str(project_dir)


def cancel_running_processes(project_dir: Path | str | None) -> int:
    """Terminate FFmpeg/ffprobe/Tesseract subprocesses started for a project."""
    key = _project_key(project_dir)
    if not key:
        return 0
    killed = 0
    with _PROCESS_LOCK:
        procs = list(_RUNNING_PROCESSES.get(key, []))
    for proc in procs:
        try:
            if proc.poll() is None:
                proc.terminate()
                killed += 1
        except Exception:
            pass
    # Give graceful termination a moment, then force.
    deadline = time.time() + 2
    for proc in procs:
        try:
            while proc.poll() is None and time.time() < deadline:
                time.sleep(0.05)
            if proc.poll() is None:
                proc.kill()
        except Exception:
            pass
    return killed


def run_cmd(
    cmd: list[str], timeout: int | None = None, project_dir: Path | str | None = None, cancel_file: Path | None = None
) -> subprocess.CompletedProcess[str]:
    """Run a child process with project ownership, timeout and cancellation semantics."""
    key=_project_key(project_dir)
    proc=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding="utf-8",errors="replace")
    if key:
        with _PROCESS_LOCK:
            _RUNNING_PROCESSES.setdefault(key,[]).append(proc)
    _persist_process_registry(project_dir,proc,cmd,True)
    cancelled=False
    try:
        started=time.time()
        out_parts=[]
        while True:
            if cancel_file is not None and cancel_file.exists() and proc.poll() is None:
                cancelled=True
                try:
                    proc.terminate()
                    time.sleep(0.2)
                    if proc.poll() is None:
                        proc.kill()
                except Exception:
                    pass
            try:
                out,_=proc.communicate(timeout=0.2)
                if out:
                    out_parts.append(out)
                break
            except subprocess.TimeoutExpired:
                if timeout is not None and time.time()-started>timeout:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                    out,_=proc.communicate()
                    if out:
                        out_parts.append(out)
                    raise subprocess.TimeoutExpired(cmd,timeout,output="".join(out_parts))
        if cancelled or (cancel_file is not None and cancel_file.exists()):
            raise OperationCancelled("Операция остановлена пользователем")
        return subprocess.CompletedProcess(cmd,proc.returncode or 0,"".join(out_parts),None)
    finally:
        _persist_process_registry(project_dir,proc,cmd,False)
        if key:
            with _PROCESS_LOCK:
                try:
                    _RUNNING_PROCESSES.get(key,[]).remove(proc)
                except ValueError:
                    pass


def which(name: str) -> str | None:
    return shutil.which(name)


def video_duration(video: Path) -> float:
    ffprobe = which("ffprobe") or "ffprobe"
    p = run_cmd([ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(video)], timeout=60)
    if p.returncode != 0:
        raise RuntimeError(p.stdout)
    duration = float(p.stdout.strip())
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("Медиафайл имеет некорректную длительность")
    return duration


def video_info(video: Path) -> dict[str, Any]:
    """Return compact ffprobe video metadata used by exports and readiness checks.

    The previous timeline exporter assumed 25 FPS for every source. That is
    unsafe for Premiere/DaVinci round-trips. This helper reads the real frame
    rate when available and falls back safely only if ffprobe cannot report it.
    """
    ffprobe = which("ffprobe") or "ffprobe"
    p = run_cmd(
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,r_frame_rate,avg_frame_rate,duration",
            "-of",
            "json",
            str(video),
        ]
    )
    if p.returncode != 0:
        return {"fps": 25.0, "fps_num": 25, "fps_den": 1, "width": 1920, "height": 1080, "error": p.stdout}
    try:
        streams = json.loads(p.stdout).get("streams") or []
        if not streams:
            return {"width": 0, "height": 0, "fps": 0, "error": "Видеопоток не найден"}
        stream = streams[0]
    except (ValueError, TypeError, AttributeError):
        return {"width": 0, "height": 0, "fps": 0, "error": "Некорректный ответ FFprobe"}

    def parse_rate(value: str | None) -> tuple[int, int, float]:
        value = value or "25/1"
        if "/" in value:
            a, b = value.split("/", 1)
            try:
                num = max(1, int(a))
                den = max(1, int(b))
                return num, den, num / den
            except Exception:
                pass
        try:
            fps = float(value)
            return int(round(fps * 1000)), 1000, fps
        except Exception:
            return 25, 1, 25.0

    num, den, fps = parse_rate(stream.get("avg_frame_rate") or stream.get("r_frame_rate"))
    if fps <= 0.01:
        num, den, fps = parse_rate(stream.get("r_frame_rate"))
    return {
        "fps": round(float(fps), 4),
        "fps_num": int(num),
        "fps_den": int(den),
        "width": int(stream.get("width") or 1920),
        "height": int(stream.get("height") or 1080),
        "duration": float(stream.get("duration") or 0) if str(stream.get("duration") or "").replace(".", "", 1).isdigit() else 0,
    }


def _visual_extract_command(
    ffmpeg: str,
    video: Path,
    pattern: Path,
    *,
    interval: float,
    width: int,
    max_samples: int,
    decode_mode: str = "cpu",
    duration_limit: float | None = None,
) -> list[str]:
    """Build a quality-equivalent visual sampling command.

    CUDA mode uses hardware decoding but deliberately keeps the existing CPU
    fps/scale filter graph. Sparse sampling filters do not safely stay in CUDA
    memory on all FFmpeg builds, and forcing hwupload/hwdownload can be slower
    than CPU on older GPUs. A real-source micro benchmark decides whether this
    decode path is beneficial before the full quality-equivalent scan.
    """
    effective_interval = max(0.001, float(interval))
    mode = str(decode_mode or "cpu").strip().lower()
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
    if mode == "cuda":
        cmd += ["-hwaccel", "cuda"]
    cmd += ["-i", str(video)]
    if duration_limit is not None and float(duration_limit) > 0:
        cmd += ["-t", f"{float(duration_limit):.3f}"]
    vf = f"fps=1/{effective_interval:.6f},scale={int(width)}:-2"
    cmd += [
        "-an", "-sn", "-dn",
        "-vf", vf,
        "-frames:v", str(max(1, int(max_samples))),
        "-q:v", "4",
        str(pattern),
    ]
    return cmd


def benchmark_visual_decode(
    video: Path,
    project_dir: Path,
    *,
    interval: float = 5.0,
    width: int = 360,
    cancel_file: Path | None = None,
) -> dict[str, Any]:
    """Benchmark CPU vs CUDA decode on a tiny source slice without reducing coverage.

    The benchmark is deliberately conservative: CUDA is selected only when the
    complete hardware-decode + unchanged CPU-filter + JPEG path succeeds on the
    actual source and is measurably faster. Unsupported codecs/drivers/builds
    fall back to the identical CPU sampling path.
    """
    ffmpeg = which("ffmpeg") or "ffmpeg"
    report: dict[str, Any] = {"selected": "cpu", "cpu_seconds": None, "cuda_seconds": None, "cuda_ok": False, "reason": "cpu_default"}
    # Avoid a doomed benchmark when this exact FFmpeg build does not advertise
    # CUDA hwaccel. Codec support is still validated by the real-source command.
    try:
        hwaccels = run_cmd([ffmpeg, "-hide_banner", "-hwaccels"], timeout=12)
        if hwaccels.returncode != 0 or "cuda" not in (hwaccels.stdout or "").lower():
            report["reason"] = "cuda_hwaccel_unavailable"
            return report
    except Exception as exc:
        report["reason"] = f"hwaccel_probe_failed:{exc}"
        return report

    bench_dir = project_dir / "visual_scan_benchmark"
    try:
        if bench_dir.exists():
            shutil.rmtree(bench_dir, ignore_errors=True)
        bench_dir.mkdir(parents=True, exist_ok=True)
        # 30 seconds / 6 frames is enough to expose decoder startup/throughput
        # while adding negligible work relative to a 1-6 hour VOD.
        bench_interval = max(3.0, min(8.0, float(interval or 5.0)))
        for mode in ("cpu", "cuda"):
            mode_dir = bench_dir / mode
            mode_dir.mkdir(parents=True, exist_ok=True)
            pattern = mode_dir / "bench_%03d.jpg"
            cmd = _visual_extract_command(
                ffmpeg,
                video,
                pattern,
                interval=bench_interval,
                width=width,
                max_samples=6,
                decode_mode=mode,
                duration_limit=30.0,
            )
            started = time.perf_counter()
            try:
                r = run_cmd(cmd, timeout=60, project_dir=project_dir, cancel_file=cancel_file)
                elapsed = max(0.001, time.perf_counter() - started)
                files = list(mode_dir.glob("bench_*.jpg"))
                ok = r.returncode == 0 and len(files) >= 2
            except OperationCancelled:
                raise
            except Exception:
                elapsed = max(0.001, time.perf_counter() - started)
                ok = False
            report[f"{mode}_seconds"] = round(elapsed, 3)
            if mode == "cuda":
                report["cuda_ok"] = bool(ok)
            if mode == "cpu" and not ok:
                report["reason"] = "cpu_benchmark_failed"
                return report
        cpu_s = float(report.get("cpu_seconds") or 0)
        cuda_s = float(report.get("cuda_seconds") or 0)
        if report.get("cuda_ok") and cpu_s > 0 and cuda_s > 0 and cuda_s <= cpu_s * 0.95:
            report["selected"] = "cuda"
            report["reason"] = "cuda_measurably_faster"
        else:
            report["reason"] = "cpu_faster_or_cuda_not_beneficial" if report.get("cuda_ok") else "cuda_runtime_failed"
        return report
    finally:
        shutil.rmtree(bench_dir, ignore_errors=True)


def extract_frames_bulk(
    video: Path,
    out_dir: Path,
    interval: float,
    width: int = 360,
    max_samples: int = 1200,
    project_dir: Path | str | None = None,
    cancel_file: Path | None = None,
    decode_mode: str = "cpu",
) -> tuple[bool, str]:
    """Extract scan frames with one FFmpeg process instead of thousands.

    ``decode_mode='cuda'`` is opt-in and is only used after the runtime benchmark
    proves it faster. The timestamps, width, frame count and JPEG quality remain
    the same, so performance tuning does not reduce visual analysis coverage.
    """
    ffmpeg = which("ffmpeg") or "ffmpeg"
    out_dir.mkdir(parents=True, exist_ok=True)
    pattern = out_dir / "scan_%05d.jpg"
    cmd = _visual_extract_command(
        ffmpeg,
        video,
        pattern,
        interval=interval,
        width=width,
        max_samples=max_samples,
        decode_mode=decode_mode,
    )
    r = run_cmd(cmd, timeout=None, project_dir=project_dir, cancel_file=cancel_file)
    return r.returncode == 0, r.stdout or ""


def audio_streams(video: Path) -> list[dict[str, Any]]:
    ffprobe = which("ffprobe") or "ffprobe"
    p = run_cmd(
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "a",
            "-show_entries",
            "stream=index,codec_name,channels:stream_tags=language,title",
            "-of",
            "json",
            str(video),
        ], timeout=60
    )
    if p.returncode != 0:
        return []
    try:
        return json.loads(p.stdout).get("streams", [])
    except Exception:
        return []


def extract_frame(video: Path, at: float, out_path: Path, width: int = 448) -> bool:
    ffmpeg = which("ffmpeg") or "ffmpeg"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    p = run_cmd(
        [ffmpeg, "-y", "-ss", f"{at:.3f}", "-i", str(video), "-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "4", str(out_path)],
        timeout=60,
    )
    return p.returncode == 0 and out_path.exists() and out_path.stat().st_size > 0


def overlaps(a1: float, a2: float, b1: float, b2: float) -> bool:
    return max(a1, b1) < min(a2, b2)
