from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import sys
import time
import shutil
import threading
import subprocess
import queue
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ...core.utils import read_json, run_cmd, safe_name, which, write_json, OperationCancelled, register_project_process, unregister_project_process
from ...core.settings import BUNDLED_TWITCHDOWNLOADERCLI_DIR, BUNDLED_ARIA2_DIR
from ...core.artifacts import portable_source_fields, validate_media_file
from ...core.revisions import mark_source_changed
from ...infrastructure.project_locks import project_metadata_lock

TWITCH_VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov", ".webm", ".m4v", ".avi", ".ts"}
TWITCH_DOWNLOAD_MANIFEST_VERSION = 1

# v10.14.4/v10.14.7 release archives accidentally contained truncated copies
# of the portable Turbo tools. A truncated PE file still exists on disk, so a
# simple path check advertises it as available and Auto Turbo immediately falls
# back to the slower yt-dlp route. Keep a conservative release-integrity guard
# so broken bundles are never selected as the fast path.
BUNDLED_TDCLI_MIN_BYTES = 60_000_000
BUNDLED_ARIA2_MIN_BYTES = 5_000_000


def parse_time_to_seconds(value: str | int | float | None) -> float | None:
    """Parse 01:02:03, 12:34, 90, 1h2m3s into seconds.

    Empty values mean "not set". Raises ValueError for invalid user input so the
    API can return a clear message before a long Twitch import starts.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        seconds = float(value)
        if seconds < 0:
            raise ValueError("time must be >= 0")
        return seconds
    text = str(value).strip()
    if not text:
        return None
    if re.fullmatch(r"\d+(?:\.\d+)?", text):
        return float(text)
    hms = re.fullmatch(r"(?:(\d+):)?(\d{1,2}):(\d{1,2})(?:\.(\d+))?", text)
    if hms:
        h = int(hms.group(1) or 0)
        m = int(hms.group(2))
        s = int(hms.group(3))
        frac = float("0." + hms.group(4)) if hms.group(4) else 0.0
        if m >= 60 or s >= 60:
            raise ValueError("minutes/seconds must be < 60 in HH:MM:SS")
        return h * 3600 + m * 60 + s + frac
    compact = re.fullmatch(r"(?:(\d+(?:\.\d+)?)h)?(?:(\d+(?:\.\d+)?)m)?(?:(\d+(?:\.\d+)?)s)?", text, flags=re.I)
    if compact and any(compact.groups()):
        h = float(compact.group(1) or 0)
        m = float(compact.group(2) or 0)
        s = float(compact.group(3) or 0)
        return h * 3600 + m * 60 + s
    raise ValueError("time must be seconds, MM:SS, HH:MM:SS or 1h2m3s")


def format_hhmmss(seconds: float | None) -> str:
    seconds = max(0.0, float(seconds or 0))
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds - h * 3600 - m * 60
    if abs(s - round(s)) < 0.001:
        return f"{h:02d}:{m:02d}:{int(round(s)):02d}"
    return f"{h:02d}:{m:02d}:{s:05.2f}"


def classify_twitch_url(url: str, source_kind: str = "auto") -> dict[str, Any]:
    url = (url or "").strip()
    parsed = urlparse(url if "://" in url else f"https://{url}")
    scheme = (parsed.scheme or "https").lower()
    host = (parsed.hostname or "").lower().rstrip(".")
    if scheme not in {"http", "https"}:
        raise ValueError("Поддерживаются только http/https Twitch-ссылки")
    if not (host == "twitch.tv" or host.endswith(".twitch.tv")):
        raise ValueError("Поддерживаются ссылки twitch.tv/videos/... или twitch.tv/<channel>")
    path = parsed.path.strip("/")
    vod_match = re.match(r"^(?:videos/)?(\d{6,})$", path)
    channel_match = re.match(r"^([A-Za-z0-9_]{3,25})(?:/)?$", path)
    detected = "vod" if vod_match else "live" if channel_match else "unknown"
    if source_kind not in {"auto", "vod", "live"}:
        raise ValueError("source_kind must be auto, vod or live")
    kind = detected if source_kind == "auto" else source_kind
    if kind == "vod" and not vod_match:
        raise ValueError("Для VOD нужна ссылка вида https://www.twitch.tv/videos/1234567890")
    if kind == "live" and not channel_match:
        raise ValueError("Для Live нужна ссылка вида https://www.twitch.tv/channelname")
    return {
        "kind": kind,
        "url": parsed.geturl(),
        "vod_id": vod_match.group(1) if vod_match else None,
        "channel": channel_match.group(1) if channel_match else None,
    }


def _module_or_cmd_available(module_name: str, command_name: str) -> bool:
    return importlib.util.find_spec(module_name) is not None or which(command_name) is not None


def _python_module_cmd(module_name: str, command_name: str) -> list[str]:
    if importlib.util.find_spec(module_name) is not None:
        if getattr(sys, "frozen", False):
            # The desktop engine is a frozen executable, not a Python
            # interpreter. Route CLI modules back through desktop_entry.
            return [sys.executable, "--run-module", module_name]
        return [sys.executable, "-m", module_name]
    cmd = which(command_name)
    if cmd:
        return [cmd]
    # Keep a useful command in the error message if it still fails.
    return [sys.executable, "-m", module_name]


def _clamp_threads(value: Any) -> int:
    try:
        threads = int(value)
    except Exception:
        threads = 16
    return max(1, min(64, threads))


def _safe_format_selector(value: Any) -> str:
    fmt = str(value or "best").strip() or "best"
    # yt-dlp format selectors may contain useful symbols like /,+,[],=, but not
    # shell metacharacters. We pass argv as a list, still keep it conservative.
    if re.search(r"[\r\n;`|&]", fmt):
        return "best"
    return fmt[:120]


def _cookies_browser_arg(value: Any) -> str | None:
    browser = str(value or "none").strip().lower()
    if browser in {"", "none", "off", "false", "no"}:
        return None
    aliases = {"msedge": "edge", "microsoft edge": "edge", "google chrome": "chrome", "brave browser": "brave"}
    browser = aliases.get(browser, browser)
    allowed = {"firefox", "chrome", "edge", "brave", "opera", "vivaldi", "safari"}
    return browser if browser in allowed else None


def _cache_size_bytes(cache_dir: Path) -> int:
    total = 0
    try:
        for p in cache_dir.rglob("*"):
            try:
                if p.is_file():
                    total += p.stat().st_size
            except Exception:
                pass
    except Exception:
        pass
    return total


def _start_cache_progress_monitor(
    cache_dir: Path,
    logger,
    stage: str,
    label: str,
    *,
    global_start: float = 2.0,
    global_end: float = 45.0,
    target_seconds: float | None = None,
):
    """Best-effort progress for long Twitch downloads.

    yt-dlp/streamlink output is provider-dependent, so the app tracks the local
    cache folder size.  It cannot always know the final percent, but it can show
    that bytes are still arriving, speed and elapsed time instead of looking
    frozen for 20-60 minutes on a 3-hour VOD.
    """
    stop = threading.Event()
    started = time.time()
    last_t = started
    last_b = _cache_size_bytes(cache_dir)

    def worker():
        nonlocal last_t, last_b
        while not stop.wait(2.0):
            now = time.time()
            size = _cache_size_bytes(cache_dir)
            delta_b = max(0, size - last_b)
            delta_t = max(0.1, now - last_t)
            speed_bps = delta_b / delta_t
            elapsed = max(0.1, now - started)
            if target_seconds and target_seconds > 0:
                ratio = max(0.0, min(0.995, elapsed / target_seconds))
                progress = global_start + (global_end - global_start) * ratio
                message = (
                    f"{label}: {format_hhmmss(elapsed)} / {format_hhmmss(target_seconds)} · "
                    f"cache {size / 1024 / 1024:.1f} MB · скорость {speed_bps / 1024 / 1024:.2f} MB/s"
                )
                stage_percent = round(min(99.5, ratio * 100), 1)
            else:
                # For VOD downloads the final size is unknown, so keep the bar alive
                # without pretending that cache bytes map to an exact percent.
                soft_ratio = min(0.92, elapsed / max(900.0, elapsed + 120.0))
                progress = global_start + (global_end - global_start) * soft_ratio
                message = f"{label}: cache {size / 1024 / 1024:.1f} MB · скорость {speed_bps / 1024 / 1024:.2f} MB/s"
                stage_percent = None
            logger.set_status(
                "running",
                progress,
                message,
                stage=stage,
                progress_source="twitch_cache",
                twitch_cache_size_mb=round(size / 1024 / 1024, 1),
                twitch_speed_mbps=round(speed_bps / 1024 / 1024, 2),
                twitch_elapsed_seconds=round(elapsed, 1),
                stage_progress_percent=stage_percent,
                allow_progress_backwards=True,
            )
            last_t = now
            last_b = size

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    return stop


def _extract_twitch_percent(line: str) -> float | None:
    """Best-effort percent parser for yt-dlp / TDCLI output."""
    text = line or ""
    matches = re.findall(r"(?<!\d)(100(?:\.0+)?|\d{1,2}(?:\.\d+)?)\s*%", text)
    if not matches:
        return None
    try:
        value = float(matches[-1])
        if 0 <= value <= 100:
            return value
    except Exception:
        return None
    return None


def _extract_twitch_speed(line: str) -> float | None:
    """Return speed in MB/s when a downloader prints it."""
    text = line or ""
    m = re.search(r"(\d+(?:\.\d+)?)\s*([KMG]i?B/s|[KMG]B/s)", text, flags=re.I)
    if not m:
        return None
    try:
        value = float(m.group(1))
    except Exception:
        return None
    unit = m.group(2).lower()
    if unit.startswith("k"):
        return value / 1024
    if unit.startswith("g"):
        return value * 1024
    return value


def _tail_line(text: str, limit: int = 220) -> str:
    text = (text or "").strip().replace("\r", " ")
    if not text:
        return ""
    return text[-limit:]


def _run_twitch_cmd_streamed(
    cmd: list[str],
    *,
    timeout: int,
    project_dir: Path,
    cancel_file: Path,
    logger,
    cache_dir: Path,
    stage: str,
    label: str,
    global_start: float = 3.0,
    global_end: float = 88.0,
) -> subprocess.CompletedProcess[str]:
    """Run Twitch downloader with live UI/log feedback.

    The old path used communicate(), so TDCLI/yt-dlp output appeared only after
    the process finished or failed. Users saw a download happening in Windows
    but no line progress and almost no logs in Highlight Studio. This runner
    streams stdout/stderr into logs.txt and status.json while keeping cancel and
    timeout behavior.
    """
    logger.log("[twitch] command started: " + " ".join(str(x) for x in cmd[:8]) + (" ..." if len(cmd) > 8 else ""))
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    register_project_process(project_dir, proc, cmd)
    cancelled = False
    cancel_started_at: float | None = None
    q: queue.Queue[str | None] = queue.Queue()
    out_parts: list[str] = []
    started = time.time()
    last_status = 0.0
    last_size = _cache_size_bytes(cache_dir)
    last_size_t = started

    def reader():
        try:
            assert proc.stdout is not None
            for raw in proc.stdout:
                q.put(raw.rstrip("\n"))
        except Exception as exc:
            q.put(f"[reader-error] {exc}")
        finally:
            q.put(None)

    threading.Thread(target=reader, daemon=True).start()
    reader_done = False
    try:
        while True:
            now = time.time()
            if cancel_file.exists() and proc.poll() is None:
                if not cancelled:
                    cancelled = True
                    cancel_started_at = now
                    logger.log("[twitch] cancel flag detected, stopping downloader...")
                    try:
                        proc.terminate()
                    except Exception:
                        pass
                elif cancel_started_at is not None and now - cancel_started_at >= 2.0:
                    # Some Windows downloader/FFmpeg trees ignore terminate().
                    # Force-stop only the child process that this attempt owns.
                    try:
                        proc.kill()
                    except Exception:
                        pass
            if timeout is not None and now - started > timeout and proc.poll() is None:
                try:
                    proc.kill()
                except Exception:
                    pass
                raise subprocess.TimeoutExpired(cmd, timeout, output="\n".join(out_parts))

            try:
                item = q.get(timeout=0.4)
                if item is None:
                    reader_done = True
                else:
                    line = item.strip()
                    if line:
                        out_parts.append(line)
                        if len(out_parts) > 2500:
                            out_parts = out_parts[-2500:]
                        logger.log("[twitch] " + line)
                        pct = _extract_twitch_percent(line)
                        speed = _extract_twitch_speed(line)
                        if pct is not None:
                            global_progress = global_start + (global_end - global_start) * max(0.0, min(100.0, pct)) / 100.0
                            logger.set_status(
                                "running",
                                global_progress,
                                f"{label}: {pct:.1f}% - {_tail_line(line)}",
                                stage=stage,
                                progress_source="twitch_downloader_output",
                                stage_progress_percent=round(pct, 2),
                                twitch_output_line=_tail_line(line, 500),
                                twitch_speed_mbps=round(speed, 2) if speed is not None else None,
                                twitch_cache_size_mb=round(_cache_size_bytes(cache_dir) / 1024 / 1024, 1),
                                allow_progress_backwards=True,
                            )
                            last_status = now
            except queue.Empty:
                pass

            if now - last_status >= 2.0 and proc.poll() is None:
                size = _cache_size_bytes(cache_dir)
                delta_t = max(0.1, now - last_size_t)
                speed_bps = max(0, size - last_size) / delta_t
                elapsed = max(0.1, now - started)
                soft_ratio = min(0.92, elapsed / max(900.0, elapsed + 120.0))
                progress = global_start + (global_end - global_start) * soft_ratio
                logger.set_status(
                    "running",
                    progress,
                    f"{label}: downloading - cache {size / 1024 / 1024:.1f} MB - {speed_bps / 1024 / 1024:.2f} MB/s",
                    stage=stage,
                    progress_source="twitch_downloader_heartbeat",
                    twitch_cache_size_mb=round(size / 1024 / 1024, 1),
                    twitch_speed_mbps=round(speed_bps / 1024 / 1024, 2),
                    twitch_elapsed_seconds=round(elapsed, 1),
                    allow_progress_backwards=True,
                )
                last_status = now
                last_size = size
                last_size_t = now

            if proc.poll() is not None and reader_done:
                break
        if cancelled or cancel_file.exists():
            raise OperationCancelled("Twitch download отменён пользователем")
        return subprocess.CompletedProcess(cmd, proc.returncode or 0, "\n".join(out_parts), None)
    finally:
        try:
            if proc.poll() is None:
                proc.kill()
        except Exception:
            pass
        unregister_project_process(project_dir, proc, cmd)


def _find_downloaded_video(cache_dir: Path) -> Path:
    files = [p for p in cache_dir.rglob("*") if p.is_file() and p.suffix.lower() in TWITCH_VIDEO_EXTENSIONS]
    files = [p for p in files if p.stat().st_size > 1024]
    if not files:
        raise RuntimeError("Twitch import завершился, но видеофайл не найден в cache.")
    return sorted(files, key=lambda p: p.stat().st_size, reverse=True)[0].resolve()


def _update_project_source(project_dir: Path, video_path: Path, twitch: dict[str, Any], status: str = "ready") -> dict[str, Any]:
    # project.json is shared with settings and other metadata writers. Atomic
    # replace alone is not enough for read-modify-write; serialize the whole RMW.
    with project_metadata_lock(project_dir):
        project = read_json(project_dir / "project.json", {}) or {}
        project.update(portable_source_fields(project_dir, video_path))
        project["original_filename"] = video_path.name
        project["storage_mode"] = "twitch_cache"
        project["source_type"] = "twitch"
        try:
            st = video_path.stat()
            project["source_video_size_bytes"] = st.st_size
            project["source_video_size_gb"] = round(st.st_size / 1024 / 1024 / 1024, 3)
            project["source_video_mtime"] = st.st_mtime
        except Exception:
            pass
        project["twitch"] = {
            **(project.get("twitch") or {}),
            **twitch,
            "status": status,
            "cached_video_path": str(video_path.resolve()),
            "updated_at": time.time(),
        }
        project["updated_at"] = time.time()
        write_json(project_dir / "project.json", project)
        write_json(project_dir / "twitch_import.json", project["twitch"])
        mark_source_changed(project_dir, project.get("settings") or {})
        return project


def _configured_executable(value: Any, candidates: tuple[str, ...] = ()) -> str | None:
    text = str(value or "").strip().strip('"').strip("'")
    if not text:
        return None
    path = Path(text).expanduser()
    if path.exists() and path.is_file():
        return str(path.resolve())
    if path.exists() and path.is_dir():
        for name in candidates:
            p = path / name
            if p.exists() and p.is_file():
                return str(p.resolve())
    return which(text)


def _bundled_tool(path: Path, *, min_size_bytes: int = 1) -> str | None:
    # Bundled tools are Windows executables; do not advertise them on Linux/macOS.
    if os.name != "nt":
        return None
    try:
        if path.exists() and path.is_file() and path.stat().st_size >= max(1, int(min_size_bytes or 1)):
            return str(path.resolve())
    except Exception:
        pass
    return None


def bundled_turbo_integrity() -> dict[str, Any]:
    """Report whether the portable Turbo executables look complete.

    This is intentionally a size/integrity sanity check rather than a runtime
    execution probe, so /api/twitch/tools can explain a damaged release before
    the user starts a multi-hour VOD download.
    """
    tdcli_path = BUNDLED_TWITCHDOWNLOADERCLI_DIR / "TwitchDownloaderCLI.exe"
    aria2_path = BUNDLED_ARIA2_DIR / "aria2c.exe"

    def item(path: Path, minimum: int) -> dict[str, Any]:
        try:
            size = path.stat().st_size if path.is_file() else 0
        except Exception:
            size = 0
        return {
            "path": str(path),
            "size_bytes": int(size),
            "minimum_bytes": int(minimum),
            "ok": bool(size >= minimum),
        }

    return {
        "twitchdownloadercli": item(tdcli_path, BUNDLED_TDCLI_MIN_BYTES),
        "aria2c": item(aria2_path, BUNDLED_ARIA2_MIN_BYTES),
    }


def _twitch_downloader_cli_cmd(settings: dict[str, Any] | None = None) -> list[str] | None:
    settings = settings or {}
    configured = _configured_executable(
        settings.get("twitch_downloader_cli_path"),
        ("TwitchDownloaderCLI.exe", "TwitchDownloaderCLI"),
    )
    if configured:
        return [configured]
    bundled = _bundled_tool(BUNDLED_TWITCHDOWNLOADERCLI_DIR / "TwitchDownloaderCLI.exe", min_size_bytes=BUNDLED_TDCLI_MIN_BYTES)
    if bundled:
        return [bundled]
    for name in ("TwitchDownloaderCLI", "TwitchDownloaderCLI.exe", "twitchdownloadercli", "twitchdownloadercli.exe"):
        found = which(name)
        if found:
            return [found]
    # Dotnet global tool / DLL installs are intentionally not guessed here: a wrong
    # command creates confusing errors. The UI shows a clear install/path hint.
    return None


def _aria2_cmd() -> str | None:
    bundled = _bundled_tool(BUNDLED_ARIA2_DIR / "aria2c.exe", min_size_bytes=BUNDLED_ARIA2_MIN_BYTES)
    if bundled:
        return bundled
    return which("aria2c") or which("aria2c.exe")


def _aria2_cmd_available() -> bool:
    return bool(_aria2_cmd())


def twitch_tool_status(settings: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return downloader availability for the UI and preflight.

    The app keeps yt-dlp/streamlink as built-in reliable defaults, but adds a
    faster VOD path via TwitchDownloaderCLI when the user installs it.  TwitchLink
    itself is treated as manual/external because it is a GUI tool; the backend can
    import files it creates but cannot safely automate its UI.
    """
    settings = settings or {}
    tdcli_cmd = _twitch_downloader_cli_cmd(settings)
    ytdlp_ok = _module_or_cmd_available("yt_dlp", "yt-dlp")
    streamlink_ok = _module_or_cmd_available("streamlink", "streamlink")
    aria2_ok = _aria2_cmd_available()
    engines = [
        {
            "id": "auto",
            "label": "Auto Turbo",
            "ok": bool(tdcli_cmd or ytdlp_ok or streamlink_ok),
            "recommended": True,
            "description": "Сам выбирает быстрый VOD-движок: TwitchDownloaderCLI → yt-dlp aria2c → yt-dlp.",
        },
        {
            "id": "twitchdownloadercli",
            "label": "TwitchDownloaderCLI",
            "ok": bool(tdcli_cmd),
            "path": tdcli_cmd[0] if tdcli_cmd else "",
            "description": "Самый быстрый CLI-кандидат для VOD. Хороший вариант, если TwitchLink качает быстрее yt-dlp.",
            "install_hint": "TwitchDownloaderCLI уже встроен в этот ZIP. Если статус всё равно “нет”, запусти проект через START_HERE.bat или положи exe в vendor/twitchdownloadercli.",
        },
        {
            "id": "yt-dlp-aria2c",
            "label": "yt-dlp + aria2c",
            "ok": bool(ytdlp_ok and aria2_ok),
            "description": "Параллельная загрузка фрагментов через aria2c. Быстрее обычного yt-dlp, если aria2c установлен.",
        },
        {
            "id": "yt-dlp",
            "label": "yt-dlp Turbo",
            "ok": bool(ytdlp_ok),
            "description": "Надёжный fallback. Использует -N concurrent fragments, retries и докачку.",
        },
        {
            "id": "streamlink",
            "label": "streamlink Live",
            "ok": bool(streamlink_ok),
            "description": "Лучше для записи live-эфиров. Для VOD используется только как резервный ручной путь.",
        },
        {
            "id": "manual-twitchlink",
            "label": "TwitchLink manual import",
            "ok": True,
            "description": "Открываешь TwitchLink, скачиваешь быстро, затем импортируешь готовый mp4 через локальный файл.",
        },
    ]
    bundle_integrity = bundled_turbo_integrity()
    return {
        "ok": bool(tdcli_cmd or ytdlp_ok or streamlink_ok),
        "bundle_integrity": bundle_integrity,
        "recommended_engine": "twitchdownloadercli"
        if tdcli_cmd
        else "yt-dlp-aria2c"
        if ytdlp_ok and aria2_ok
        else "yt-dlp"
        if ytdlp_ok
        else "streamlink"
        if streamlink_ok
        else "manual-twitchlink",
        "tools": {
            "twitchdownloadercli": {
                "ok": bool(tdcli_cmd),
                "cmd": tdcli_cmd or [],
                "bundled": bool(
                    tdcli_cmd and str(tdcli_cmd[0]).lower().endswith("twitchdownloadercli.exe") and "vendor" in str(tdcli_cmd[0]).lower()
                ),
            },
            "yt_dlp": {"ok": bool(ytdlp_ok)},
            "aria2c": {
                "ok": bool(aria2_ok),
                "cmd": [_aria2_cmd()] if _aria2_cmd() else [],
                "bundled": bool(_aria2_cmd() and "vendor" in (_aria2_cmd() or "").lower()),
            },
            "streamlink": {"ok": bool(streamlink_ok)},
        },
        "engines": engines,
    }


def _select_vod_engine(twitch: dict[str, Any], settings: dict[str, Any]) -> str:
    requested = str(twitch.get("download_engine") or settings.get("twitch_download_engine") or "auto").strip().lower()
    if requested in {"tdcli", "twitchdownloader", "twitchdownloadercli.exe"}:
        requested = "twitchdownloadercli"
    if requested in {"ytdlp", "yt_dlp"}:
        requested = "yt-dlp"
    valid = {"auto", "twitchdownloadercli", "yt-dlp", "yt-dlp-aria2c", "streamlink", "manual-twitchlink"}
    if requested not in valid:
        requested = "auto"
    if requested != "auto":
        return requested
    status = twitch_tool_status(settings)
    return status.get("recommended_engine") or "yt-dlp"


def _quality_for_tdcli(value: Any) -> str:
    q = str(value or "best").strip() or "best"
    if any(ch in q for ch in "\r\n;`|&"):
        q = "best"
    return q[:80]


def _vod_download_identity(
    info: dict[str, Any],
    twitch: dict[str, Any],
    settings: dict[str, Any],
    engine: str,
) -> dict[str, Any]:
    """Return the compatibility fields that make a partial VOD reusable."""
    if engine == "twitchdownloadercli":
        output_format = _quality_for_tdcli(twitch.get("quality", settings.get("twitch_quality", "best")))
    else:
        output_format = _safe_format_selector(twitch.get("format_selector", settings.get("twitch_format", "best")))

    def seconds(value: Any) -> float | None:
        return None if value is None else float(value)

    return {
        "engine": engine,
        "format": output_format,
        "range": {
            "end_seconds": seconds(twitch.get("end_seconds")),
            "start_seconds": seconds(twitch.get("start_seconds")),
        },
        "vod_id": str(info.get("vod_id") or "").strip(),
    }


def _vod_download_cache_id(identity: dict[str, Any]) -> str:
    canonical = json.dumps(
        {"version": TWITCH_DOWNLOAD_MANIFEST_VERSION, "identity": identity},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


def _download_manifest_matches(manifest: Any, cache_id: str, identity: dict[str, Any]) -> bool:
    return bool(
        isinstance(manifest, dict)
        and manifest.get("version") == TWITCH_DOWNLOAD_MANIFEST_VERSION
        and manifest.get("cache_id") == cache_id
        and manifest.get("identity") == identity
    )


def _quarantine_incompatible_download(cache_root: Path, attempt_cache: Path, logger) -> None:
    if not attempt_cache.exists():
        return
    orphans = cache_root / "orphans"
    orphans.mkdir(parents=True, exist_ok=True)
    suffix = time.time_ns()
    target = orphans / f"{attempt_cache.name}-incompatible-{suffix}"
    counter = 1
    while target.exists():
        target = orphans / f"{attempt_cache.name}-incompatible-{suffix}-{counter}"
        counter += 1
    attempt_cache.replace(target)
    logger.log(f"[twitch] incompatible partial cache quarantined: {target}")


def _prepare_vod_download_cache(
    cache_root: Path,
    identity: dict[str, Any],
    logger,
) -> tuple[Path, dict[str, Any]]:
    cache_id = _vod_download_cache_id(identity)
    attempt_cache = cache_root / "downloads" / cache_id
    manifest_path = attempt_cache / "download_manifest.json"
    existing = read_json(manifest_path, None) if manifest_path.exists() else None
    if attempt_cache.exists() and not _download_manifest_matches(existing, cache_id, identity):
        _quarantine_incompatible_download(cache_root, attempt_cache, logger)
        existing = None
    attempt_cache.mkdir(parents=True, exist_ok=True)
    now = time.time()
    manifest = dict(existing) if isinstance(existing, dict) else {
        "version": TWITCH_DOWNLOAD_MANIFEST_VERSION,
        "cache_id": cache_id,
        "identity": identity,
        "created_at": now,
    }
    manifest.update({"state": "partial", "updated_at": now})
    write_json(manifest_path, manifest)
    return attempt_cache, manifest


def _update_vod_download_manifest(attempt_cache: Path, state: str, **details: Any) -> None:
    manifest_path = attempt_cache / "download_manifest.json"
    manifest = read_json(manifest_path, {}) or {}
    if not isinstance(manifest, dict):
        return
    manifest.update(details)
    manifest.update({"state": state, "updated_at": time.time()})
    write_json(manifest_path, manifest)


def _path_is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(parent.resolve(strict=False))
        return True
    except ValueError:
        return False


def _cleanup_vod_attempts_after_success(
    cache_root: Path,
    successful_cache: Path,
    final_source: Path,
    logger,
) -> None:
    """Remove failed/orphan attempts only after a validated final source exists."""
    successful_cache = successful_cache.resolve(strict=False)
    final_source = final_source.resolve(strict=False)
    for container_name in ("downloads", "attempts", "orphans"):
        container = cache_root / container_name
        if not container.is_dir():
            continue
        for candidate in list(container.iterdir()):
            resolved = candidate.resolve(strict=False)
            if resolved == successful_cache or _path_is_within(final_source, resolved):
                continue
            try:
                if candidate.is_dir():
                    shutil.rmtree(candidate)
                else:
                    candidate.unlink(missing_ok=True)
                logger.log(f"[twitch] cleaned obsolete download attempt: {candidate}")
            except Exception as exc:
                logger.log(f"[twitch] cleanup warning for {candidate}: {exc}")

    for pattern in ("*.part", "*.ytdl", "*.temp", "*.tmp", "*.frag.urls"):
        for residue in successful_cache.rglob(pattern):
            try:
                if residue.is_file() and residue.resolve(strict=False) != final_source:
                    residue.unlink(missing_ok=True)
            except Exception as exc:
                logger.log(f"[twitch] cleanup warning for {residue}: {exc}")
    tdcli_temp = successful_cache / "tdcli_temp"
    if tdcli_temp.is_dir() and not _path_is_within(final_source, tdcli_temp):
        try:
            shutil.rmtree(tdcli_temp)
        except Exception as exc:
            logger.log(f"[twitch] cleanup warning for {tdcli_temp}: {exc}")


def _prepare_ytdlp_vod(
    project_dir: Path,
    cache: Path,
    info: dict[str, Any],
    twitch: dict[str, Any],
    settings: dict[str, Any],
    logger,
    *,
    use_aria2: bool = False,
    engine_label: str = "yt-dlp",
) -> dict[str, Any]:
    if not _module_or_cmd_available("yt_dlp", "yt-dlp"):
        raise RuntimeError("Для Twitch VOD нужен yt-dlp. Запусти setup_windows.bat или: py -m pip install -r backend\\requirements.txt")
    start = twitch.get("start_seconds")
    end = twitch.get("end_seconds")
    section = None
    if start is not None or end is not None:
        start_s = format_hhmmss(float(start or 0))
        end_s = format_hhmmss(float(end)) if end is not None else "inf"
        section = f"*{start_s}-{end_s}"

    threads = _clamp_threads(twitch.get("download_threads", settings.get("twitch_download_threads", 16)))
    cookies_browser = _cookies_browser_arg(twitch.get("cookies_browser", settings.get("twitch_cookies_browser", "none")))
    fmt = _safe_format_selector(twitch.get("format_selector", settings.get("twitch_format", "best")))
    aria2_connections = _clamp_threads(twitch.get("aria2_connections", settings.get("twitch_aria2_connections", threads)))
    if use_aria2 and not _aria2_cmd_available():
        raise RuntimeError("Выбран yt-dlp + aria2c, но aria2c не найден. Установи aria2c или выбери Auto/yt-dlp.")

    range_label = "выбранный диапазон" if section else "весь VOD"
    cookies_label = f" · cookies: {cookies_browser}" if cookies_browser else " · cookies: none"
    label = f"Twitch VOD Turbo: {engine_label} · -N {threads} · {range_label}{cookies_label}"
    logger.set_status(
        "running",
        2,
        label,
        stage="twitch_vod",
        progress_source="twitch_import",
        twitch_engine=engine_label,
        twitch_threads=threads,
        twitch_cookies_browser=cookies_browser or "none",
    )
    out_tpl = cache / "source.%(ext)s"
    cmd = _python_module_cmd("yt_dlp", "yt-dlp") + [
        "--no-playlist",
        "--newline",
        "-f",
        fmt,
        "-N",
        str(threads),
        "--continue",
        "--retries",
        "infinite",
        "--fragment-retries",
        "infinite",
        "--file-access-retries",
        "10",
        "--extractor-retries",
        "5",
        "--socket-timeout",
        "30",
        "--merge-output-format",
        "mp4",
        "-o",
        str(out_tpl),
    ]
    if use_aria2:
        cmd += ["--downloader", "aria2c", "--downloader-args", f"aria2c:-x {aria2_connections} -s {aria2_connections} -k 1M"]
    if cookies_browser:
        cmd += ["--cookies-from-browser", cookies_browser]
    if section:
        cmd += ["--download-sections", section, "--force-keyframes-at-cuts"]
    cmd.append(info["url"])
    p = _run_twitch_cmd_streamed(
        cmd,
        timeout=int(settings.get("twitch_download_timeout", 12 * 3600) or 12 * 3600),
        project_dir=project_dir,
        cancel_file=project_dir / "cancel.flag",
        logger=logger,
        cache_dir=cache,
        stage="twitch_vod",
        label=label,
        global_start=3.0,
        global_end=88.0,
    )
    if p.returncode != 0:
        output = (p.stdout or "")[-3000:]
        if cookies_browser and ("cookies" in output.lower() or "browser" in output.lower()):
            output += "\n\nПодсказка: закрой браузер полностью или выбери Cookies: none/другой браузер. На Windows браузер иногда блокирует cookie-базу."
        raise RuntimeError(f"{engine_label} не смог получить Twitch VOD: " + output)
    video_path = _find_downloaded_video(cache)
    media_check = validate_media_file(video_path)
    if not media_check.get("ok"):
        raise RuntimeError("Twitch downloader создал некорректный видеофайл: " + str(media_check.get("message") or media_check))
    twitch.update(
        {
            **info,
            "status": "ready",
            "range_section": section,
            "download_tool": engine_label,
            "download_engine": "yt-dlp-aria2c" if use_aria2 else "yt-dlp",
            "download_threads": threads,
            "cookies_browser": cookies_browser or "none",
            "format_selector": fmt,
            "aria2_connections": aria2_connections if use_aria2 else None,
        }
    )
    result = _update_project_source(project_dir, video_path, twitch)
    logger.set_status(
        "done",
        100,
        f"Twitch VOD импортирован через {engine_label}. Теперь можно запускать AI-анализ.",
        stage="twitch_vod",
        progress_source="done",
        twitch_engine=engine_label,
        twitch_threads=threads,
        twitch_cookies_browser=cookies_browser or "none",
    )
    return result.get("twitch", {})


def _prepare_tdcli_vod(
    project_dir: Path, cache: Path, info: dict[str, Any], twitch: dict[str, Any], settings: dict[str, Any], logger
) -> dict[str, Any]:
    tdcli = _twitch_downloader_cli_cmd(settings)
    if not tdcli:
        raise RuntimeError(
            "TwitchDownloaderCLI не найден. В этой сборке он должен лежать в vendor/twitchdownloadercli. Запусти через START_HERE.bat или выбери Auto/yt-dlp."
        )
    threads = _clamp_threads(twitch.get("download_threads", settings.get("twitch_download_threads", 16)))
    quality = _quality_for_tdcli(twitch.get("quality", settings.get("twitch_quality", "best")))
    start = twitch.get("start_seconds")
    end = twitch.get("end_seconds")
    out = cache / "source_tdcli.mp4"
    temp = cache / "tdcli_temp"
    temp.mkdir(parents=True, exist_ok=True)
    ffmpeg = which("ffmpeg") or "ffmpeg"
    vod_id_or_url = str(info.get("vod_id") or info.get("url") or "").strip()
    if not vod_id_or_url:
        raise RuntimeError("Не удалось определить VOD ID для TwitchDownloaderCLI.")
    label = f"Twitch VOD Turbo: TwitchDownloaderCLI · threads {threads} · quality {quality}"
    logger.set_status(
        "running",
        2,
        label,
        stage="twitch_vod",
        progress_source="twitch_import",
        twitch_engine="TwitchDownloaderCLI",
        twitch_threads=threads,
        twitch_quality=quality,
    )
    cmd = tdcli + [
        "videodownload",
        "--id",
        vod_id_or_url,
        "--output",
        str(out),
        "--quality",
        quality,
        "--threads",
        str(threads),
        "--ffmpeg-path",
        ffmpeg,
        "--temp-path",
        str(temp),
        "--collision",
        "Overwrite",
    ]
    if start is not None:
        cmd += ["--beginning", format_hhmmss(float(start))]
    if end is not None:
        cmd += ["--ending", format_hhmmss(float(end))]
    p = _run_twitch_cmd_streamed(
        cmd,
        timeout=int(settings.get("twitch_download_timeout", 12 * 3600) or 12 * 3600),
        project_dir=project_dir,
        cancel_file=project_dir / "cancel.flag",
        logger=logger,
        cache_dir=cache,
        stage="twitch_vod",
        label=label,
        global_start=3.0,
        global_end=88.0,
    )
    if p.returncode != 0:
        output = (p.stdout or "")[-3000:]
        raise RuntimeError(
            "TwitchDownloaderCLI не смог получить Twitch VOD: "
            + output
            + "\n\nFallback: выбери Auto или yt-dlp, либо скачай через TwitchLink и импортируй файл как локальное видео."
        )
    video_path = _find_downloaded_video(cache)
    media_check = validate_media_file(video_path)
    if not media_check.get("ok"):
        raise RuntimeError("Twitch downloader создал некорректный видеофайл: " + str(media_check.get("message") or media_check))
    twitch.update(
        {
            **info,
            "status": "ready",
            "download_tool": "TwitchDownloaderCLI",
            "download_engine": "twitchdownloadercli",
            "download_threads": threads,
            "quality": quality,
            "tdcli_path": tdcli[0],
        }
    )
    result = _update_project_source(project_dir, video_path, twitch)
    logger.set_status(
        "done",
        100,
        f"Twitch VOD импортирован через TwitchDownloaderCLI threads {threads}. Теперь можно запускать AI-анализ.",
        stage="twitch_vod",
        progress_source="done",
        twitch_engine="TwitchDownloaderCLI",
        twitch_threads=threads,
        twitch_quality=quality,
    )
    return result.get("twitch", {})


def twitch_download_plan(url: str, settings: dict[str, Any] | None = None, source_kind: str = "auto") -> dict[str, Any]:
    settings = settings or {}
    try:
        info = classify_twitch_url(url, source_kind)
    except Exception as exc:
        return {"ok": False, "error": str(exc), "tools": twitch_tool_status(settings)}
    status = twitch_tool_status(settings)
    if info["kind"] == "live":
        rec = "streamlink" if status["tools"]["streamlink"]["ok"] else "manual-twitchlink"
        steps = [
            "Live лучше записывать через streamlink.",
            "Если streamlink недоступен — запиши эфир во внешней программе и импортируй файл.",
        ]
    else:
        rec = status.get("recommended_engine") or "yt-dlp"
        steps = [
            "Сначала сделай тест скорости 30–60 секунд.",
            "Если TwitchDownloaderCLI быстрее — оставь Auto/TwitchDownloaderCLI.",
            "Если VOD приватный/требует cookies — попробуй Cookies browser или скачай через TwitchLink и импортируй mp4.",
        ]
    return {"ok": True, "kind": info["kind"], "recommended_engine": rec, "tools": status, "steps": steps}


def run_twitch_speed_test(project_dir: Path, payload: dict[str, Any], settings: dict[str, Any], logger=None) -> dict[str, Any]:
    """Run a short best-effort downloader benchmark.

    This endpoint is optional and safe: if tools/network are missing it returns a
    readable result instead of breaking the project.  It downloads a very short
    sample range to project/twitch_speed_test and measures cache growth.
    """
    url = str(payload.get("url") or "").strip()
    if not url:
        project = read_json(project_dir / "project.json", {}) or {}
        url = str(project.get("source_url") or (project.get("twitch") or {}).get("url") or "").strip()
    info = classify_twitch_url(url, payload.get("source_kind") or "auto")
    if info["kind"] != "vod":
        return {"ok": False, "message": "Speed test доступен только для Twitch VOD. Для Live используй streamlink.", "results": []}
    test_seconds = max(10, min(180, int(payload.get("test_seconds") or settings.get("twitch_speed_test_seconds", 45) or 45)))
    threads = _clamp_threads(payload.get("threads", settings.get("twitch_download_threads", 16)))
    quality = _quality_for_tdcli(payload.get("quality", settings.get("twitch_quality", "best")))
    base = project_dir / "twitch_speed_test"
    if base.exists():
        try:
            shutil.rmtree(base)
        except Exception:
            pass
    base.mkdir(parents=True, exist_ok=True)
    start_seconds = parse_time_to_seconds(payload.get("vod_start")) or 0
    end_seconds = start_seconds + test_seconds
    engines = payload.get("engines") or ["twitchdownloadercli", "yt-dlp-aria2c", "yt-dlp"]
    if isinstance(engines, str):
        engines = [e.strip() for e in engines.split(",") if e.strip()]
    results = []
    for engine in engines:
        engine = str(engine).strip().lower()
        e_dir = base / safe_name(engine, "engine")
        e_dir.mkdir(parents=True, exist_ok=True)
        started = time.time()
        ok = False
        error = ""
        cmd: list[str] | None = None
        try:
            if engine == "twitchdownloadercli":
                tdcli = _twitch_downloader_cli_cmd(settings)
                if not tdcli:
                    raise RuntimeError("TwitchDownloaderCLI не найден")
                out = e_dir / "sample.mp4"
                temp = e_dir / "temp"
                ffmpeg = which("ffmpeg") or "ffmpeg"
                cmd = tdcli + [
                    "videodownload",
                    "--id",
                    str(info.get("vod_id") or info["url"]),
                    "--output",
                    str(out),
                    "--quality",
                    quality,
                    "--threads",
                    str(threads),
                    "--ffmpeg-path",
                    ffmpeg,
                    "--temp-path",
                    str(temp),
                    "--collision",
                    "Overwrite",
                    "--beginning",
                    format_hhmmss(start_seconds),
                    "--ending",
                    format_hhmmss(end_seconds),
                ]
            elif engine in {"yt-dlp", "yt-dlp-aria2c"}:
                if not _module_or_cmd_available("yt_dlp", "yt-dlp"):
                    raise RuntimeError("yt-dlp не найден")
                if engine == "yt-dlp-aria2c" and not _aria2_cmd_available():
                    raise RuntimeError("aria2c не найден")
                out_tpl = e_dir / "sample.%(ext)s"
                cmd = _python_module_cmd("yt_dlp", "yt-dlp") + [
                    "--no-playlist",
                    "--newline",
                    "-f",
                    _safe_format_selector(payload.get("format") or settings.get("twitch_format", "best")),
                    "-N",
                    str(threads),
                    "--download-sections",
                    f"*{format_hhmmss(start_seconds)}-{format_hhmmss(end_seconds)}",
                    "--force-keyframes-at-cuts",
                    "--merge-output-format",
                    "mp4",
                    "-o",
                    str(out_tpl),
                ]
                if engine == "yt-dlp-aria2c":
                    conns = _clamp_threads(payload.get("aria2_connections", settings.get("twitch_aria2_connections", threads)))
                    cmd += ["--downloader", "aria2c", "--downloader-args", f"aria2c:-x {conns} -s {conns} -k 1M"]
                cmd.append(info["url"])
            else:
                raise RuntimeError(f"Неизвестный engine: {engine}")
            if logger:
                logger.set_status(
                    "running",
                    1,
                    f"Тест скорости Twitch: {engine}",
                    stage="twitch_speed_test",
                    progress_source="speed_test",
                    twitch_engine=engine,
                )
            p = run_cmd(cmd, timeout=test_seconds + 180, project_dir=project_dir, cancel_file=project_dir / "cancel.flag")
            ok = p.returncode == 0 and _cache_size_bytes(e_dir) > 1024
            if not ok:
                error = (p.stdout or "")[-1200:] or "не удалось скачать sample"
        except Exception as exc:
            error = str(exc)
        elapsed = max(0.1, time.time() - started)
        size = _cache_size_bytes(e_dir)
        mbps = size / 1024 / 1024 / elapsed
        results.append(
            {
                "engine": engine,
                "ok": ok,
                "size_mb": round(size / 1024 / 1024, 2),
                "elapsed_seconds": round(elapsed, 1),
                "speed_mbps": round(mbps, 2),
                "error": error[:1200],
            }
        )
    best = next((r for r in sorted(results, key=lambda x: x.get("speed_mbps", 0), reverse=True) if r.get("ok")), None)
    report = {"ok": bool(best), "best_engine": best.get("engine") if best else None, "results": results, "recommendation": ""}
    if best:
        report["recommendation"] = f"Рекомендую {best['engine']}: {best['speed_mbps']} MB/s на тестовом диапазоне."
    else:
        report["recommendation"] = "Автотест не смог скачать sample. Проверь VOD/cookies или скачай через TwitchLink и импортируй файл."
    write_json(project_dir / "twitch_speed_test.json", report)
    return report


def _twitch_cache_location_warning(path: Path) -> str:
    """Return a user-facing warning for cache locations known to throttle I/O."""
    text = str(path.resolve()).lower()
    if "onedrive" in text or "dropbox" in text or "google drive" in text:
        return "Twitch cache находится в синхронизируемой папке. Это может сильно ограничивать скорость загрузки."
    if text.startswith("\\\\"):
        return "Twitch cache находится на сетевом диске. Для максимальной скорости используй локальный SSD."
    return ""


def prepare_twitch_source(project_dir: Path, settings: dict[str, Any], logger) -> dict[str, Any]:
    """Download/cache Twitch VOD range or record a live stream into project cache.

    The user does not manually download the stream.  The app creates a local
    cache file because Whisper/FFmpeg/render need bytes to read.  For VOD, the
    optional start/end range keeps the cache small and makes analysis focus on
    the selected part of the stream.
    """
    project = read_json(project_dir / "project.json", {}) or {}
    twitch = dict(project.get("twitch") or {})
    url = twitch.get("url") or project.get("source_url")
    if not url:
        raise RuntimeError("В проекте нет Twitch URL.")
    info = classify_twitch_url(url, twitch.get("source_kind") or "auto")
    cache = project_dir / "twitch_cache"
    cache.mkdir(parents=True, exist_ok=True)
    cache_warning = _twitch_cache_location_warning(cache)
    if cache_warning:
        logger.log("[twitch] WARNING: " + cache_warning + " Path: " + str(cache))
    if info["kind"] == "vod":
        requested_engine = _select_vod_engine(twitch, settings)
        logger.log(
            f"[twitch] selected engine={requested_engine}; cache={cache}; portable={os.environ.get('HIGHLIGHT_STUDIO_PORTABLE', '0')}"
        )
        fallback_enabled = bool(twitch.get("fallback_enabled", settings.get("twitch_fallback_enabled", True)))
        tried: list[str] = []
        errors: list[str] = []

        def attempt(engine: str):
            tried.append(engine)
            identity = _vod_download_identity(info, twitch, settings, engine)
            attempt_cache, manifest = _prepare_vod_download_cache(cache, identity, logger)
            logger.log(
                f"[twitch] attempt cache_id={manifest['cache_id']}; state={manifest['state']}; cache={attempt_cache}"
            )
            try:
                if engine == "twitchdownloadercli":
                    result = _prepare_tdcli_vod(project_dir, attempt_cache, info, twitch, settings, logger)
                elif engine == "yt-dlp-aria2c":
                    result = _prepare_ytdlp_vod(
                        project_dir,
                        attempt_cache,
                        info,
                        twitch,
                        settings,
                        logger,
                        use_aria2=True,
                        engine_label="yt-dlp+aria2c",
                    )
                elif engine == "yt-dlp":
                    result = _prepare_ytdlp_vod(
                        project_dir,
                        attempt_cache,
                        info,
                        twitch,
                        settings,
                        logger,
                        use_aria2=False,
                        engine_label="yt-dlp",
                    )
                elif engine == "streamlink":
                    raise RuntimeError(
                        "streamlink в Highlight Studio используется для Live. Для VOD выбери TwitchDownloaderCLI/yt-dlp или manual TwitchLink import."
                    )
                elif engine == "manual-twitchlink":
                    raise RuntimeError(
                        "Manual TwitchLink: скачай VOD во внешнем TwitchLink, затем импортируй mp4 как локальное видео. Автоматически управлять GUI TwitchLink нельзя безопасно."
                    )
                else:
                    raise RuntimeError(f"Неизвестный Twitch downloader engine: {engine}")

                final_source = Path(str(result.get("cached_video_path") or "")).expanduser()
                if not str(result.get("cached_video_path") or "").strip():
                    final_source = _find_downloaded_video(attempt_cache)
                final_check = validate_media_file(final_source)
                if not final_check.get("ok"):
                    raise RuntimeError(
                        "Twitch downloader не создал валидный финальный source: "
                        + str(final_check.get("message") or final_check)
                    )
                try:
                    _update_vod_download_manifest(
                        attempt_cache,
                        "ready",
                        completed_at=time.time(),
                        final_source=str(final_source.resolve()),
                        final_size_bytes=final_source.stat().st_size,
                    )
                except Exception as exc:
                    logger.log(f"[twitch] final manifest warning: {exc}")
                _cleanup_vod_attempts_after_success(cache, attempt_cache, final_source, logger)
                return result
            except Exception as exc:
                try:
                    _update_vod_download_manifest(
                        attempt_cache,
                        "partial",
                        last_error=f"{type(exc).__name__}: {exc}"[:1000],
                    )
                except Exception as manifest_exc:
                    logger.log(f"[twitch] partial manifest warning: {manifest_exc}")
                raise

        plan = []
        if requested_engine == "auto":
            requested_engine = _select_vod_engine(twitch, settings)
        plan.append(requested_engine)
        if fallback_enabled:
            for e in ["twitchdownloadercli", "yt-dlp-aria2c", "yt-dlp"]:
                if e not in plan:
                    plan.append(e)
        for engine in plan:
            try:
                logger.set_status(
                    "running",
                    1,
                    f"Twitch Turbo: пробую {engine}",
                    stage="twitch_vod",
                    progress_source="twitch_import",
                    twitch_engine=engine,
                    twitch_fallback_plan=plan,
                    allow_progress_backwards=True,
                )
                return attempt(engine)
            except OperationCancelled:
                logger.log(f"[twitch] {engine}: cancelled by user; fallback suppressed")
                raise
            except Exception as exc:
                msg = f"{engine}: {exc}"
                errors.append(msg)
                logger.set_status(
                    "running",
                    1,
                    f"{engine} не сработал, пробую fallback...",
                    stage="twitch_vod",
                    progress_source="twitch_import",
                    twitch_engine=engine,
                    twitch_errors=errors[-3:],
                    allow_progress_backwards=True,
                )
                if not fallback_enabled:
                    break
        hint = "\n\nЧто сделать: проверь папку vendor/twitchdownloadercli или выбери yt-dlp/aria2c, либо скачай через TwitchLink и импортируй mp4 как локальный файл."
        raise RuntimeError("Twitch Turbo Downloader не смог подготовить VOD. Попытки: " + " | ".join(errors[-5:]) + hint)

    if info["kind"] == "live":
        if not _module_or_cmd_available("streamlink", "streamlink"):
            raise RuntimeError(
                "Для Twitch Live нужен streamlink. Запусти setup_windows.bat или: py -m pip install -r backend\\requirements.txt"
            )
        ffmpeg = which("ffmpeg") or "ffmpeg"
        minutes = int(twitch.get("live_record_minutes") or 60)
        seconds = max(60, minutes * 60)
        out = cache / "source_live.ts"
        wallclock_grace = 90
        min_acceptable_duration = max(30.0, float(seconds) - max(30.0, min(180.0, float(seconds) * 0.02)))

        # Recover a near-complete recording from a previous watchdog failure before
        # resolving a fresh Twitch URL or truncating the file with FFmpeg -y. This
        # is especially important for long (2-3 hour) live captures: the user should
        # not have to record the whole stream again when source_live.ts is already valid.
        if out.exists() and out.is_file() and out.stat().st_size > 0:
            existing_media = validate_media_file(out, timeout=60)
            existing_duration = float(existing_media.get("duration_seconds") or 0.0) if existing_media.get("ok") else 0.0
            if existing_media.get("ok") and existing_duration >= min_acceptable_duration:
                twitch.update({
                    **info,
                    "status": "ready",
                    "download_tool": "streamlink+ffmpeg",
                    "live_record_seconds": seconds,
                    "recorded_duration_seconds": round(existing_duration, 3),
                    "live_completion_mode": "recovered_existing",
                })
                logger.log(
                    "[twitch] Recovered existing near-complete Live recording: "
                    f"{format_hhmmss(existing_duration)} / {format_hhmmss(seconds)}."
                )
                result = _update_project_source(project_dir, out.resolve(), twitch)
                logger.set_status(
                    "done",
                    100,
                    f"Twitch Live восстановлен из cache: {format_hhmmss(existing_duration)}. Можно запускать AI-анализ.",
                    stage="twitch_live",
                    progress_source="recovered_existing",
                    stage_progress_percent=100,
                    twitch_recorded_duration_seconds=round(existing_duration, 3),
                    twitch_requested_duration_seconds=seconds,
                    twitch_completion_mode="recovered_existing",
                )
                return result.get("twitch", {})

        logger.set_status(
            "running", 2, f"Twitch Live: подключаюсь к каналу и пишу {minutes} мин.", stage="twitch_live", progress_source="twitch_import"
        )
        resolve_cmd = _python_module_cmd("streamlink", "streamlink") + ["--stream-url", info["url"], "best"]
        p = run_cmd(resolve_cmd, timeout=90, project_dir=project_dir, cancel_file=project_dir / "cancel.flag")
        if p.returncode != 0 or not (p.stdout or "").strip():
            raise RuntimeError("streamlink не смог получить live stream URL: " + (p.stdout or "")[-2000:])
        stream_url = [line.strip() for line in p.stdout.splitlines() if line.strip()][-1]
        cmd = [ffmpeg, "-y", "-i", stream_url, "-t", str(seconds), "-c", "copy", str(out)]
        # FFmpeg's -t is based on media timestamps, while users choose a wall-clock
        # recording duration. Twitch HLS can contain timestamp gaps/discontinuities,
        # so a healthy 150-minute recording used to be killed at 155 minutes and
        # reported as an error even though source_live.ts was already usable.
        monitor_stop = _start_cache_progress_monitor(
            cache,
            logger,
            "twitch_live",
            "Twitch Live: записываю эфир",
            global_start=2.0,
            global_end=95.0,
            target_seconds=float(seconds),
        )
        completion_mode = "normal"
        try:
            try:
                p2 = run_cmd(
                    cmd,
                    timeout=seconds + wallclock_grace,
                    project_dir=project_dir,
                    cancel_file=project_dir / "cancel.flag",
                )
            except subprocess.TimeoutExpired:
                media = validate_media_file(out, timeout=60)
                recorded = float(media.get("duration_seconds") or 0.0) if media.get("ok") else 0.0
                if media.get("ok") and recorded >= min_acceptable_duration:
                    completion_mode = "wallclock_guard_salvaged"
                    logger.log(
                        "[twitch] Live FFmpeg reached the wall-clock guard; "
                        f"accepting valid recording {format_hhmmss(recorded)} / {format_hhmmss(seconds)}."
                    )
                    p2 = subprocess.CompletedProcess(cmd, 0, "", None)
                else:
                    recorded_text = format_hhmmss(recorded) if recorded > 0 else "не удалось определить"
                    raise RuntimeError(
                        "Twitch Live не завершился за выбранное время. "
                        f"Записано: {recorded_text}; ожидалось: {format_hhmmss(seconds)}. "
                        "Частичный файл сохранён в twitch_cache/source_live.ts. "
                        "Проверь, не завершился ли эфир и не пропадал ли интернет, затем можно повторить запись."
                    ) from None
        finally:
            monitor_stop.set()

        media = validate_media_file(out, timeout=60)
        recorded = float(media.get("duration_seconds") or 0.0) if media.get("ok") else 0.0
        if p2.returncode != 0:
            if media.get("ok") and recorded >= min_acceptable_duration:
                completion_mode = "ffmpeg_nonzero_salvaged"
                logger.log(
                    f"[twitch] FFmpeg exited with code {p2.returncode}, but the recording is valid and long enough: "
                    f"{format_hhmmss(recorded)} / {format_hhmmss(seconds)}. Accepting it."
                )
            else:
                recorded_text = format_hhmmss(recorded) if recorded > 0 else "не удалось определить"
                raise RuntimeError(
                    "FFmpeg не смог завершить Twitch Live запись. "
                    f"Записано: {recorded_text}; ожидалось: {format_hhmmss(seconds)}. "
                    "Частичный файл оставлен в twitch_cache/source_live.ts для диагностики."
                )
        if not media.get("ok"):
            raise RuntimeError(
                "Twitch Live запись завершилась, но source_live.ts не прошёл проверку FFprobe. "
                f"Причина: {media.get('message') or media.get('code') or 'неизвестная ошибка медиафайла'}."
            )

        twitch.update({
            **info,
            "status": "ready",
            "download_tool": "streamlink+ffmpeg",
            "live_record_seconds": seconds,
            "recorded_duration_seconds": round(recorded, 3),
            "live_completion_mode": completion_mode,
        })
        result = _update_project_source(project_dir, out.resolve(), twitch)
        logger.set_status(
            "done",
            100,
            f"Twitch Live записан: {format_hhmmss(recorded)}. Теперь можно запускать AI-анализ.",
            stage="twitch_live",
            progress_source="done",
            stage_progress_percent=100,
            twitch_recorded_duration_seconds=round(recorded, 3),
            twitch_requested_duration_seconds=seconds,
            twitch_completion_mode=completion_mode,
        )
        return result.get("twitch", {})

    raise RuntimeError("Не удалось определить тип Twitch-ссылки. Используй VOD /videos/ID или канал для Live.")
