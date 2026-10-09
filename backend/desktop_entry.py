"""Standalone desktop engine entry point.

The Electron shell starts this executable as a sidecar. It intentionally binds
only to loopback and serves both the FastAPI API and the built React UI.
"""

from __future__ import annotations

import json
import multiprocessing
import os
import runpy
import sys
import tempfile
from pathlib import Path

import uvicorn


def _project_root() -> Path:
    override = os.environ.get("HIGHLIGHT_STUDIO_APP_ROOT", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def _run_embedded_module() -> int | None:
    if len(sys.argv) < 3 or sys.argv[1] != "--run-module":
        return None
    module_name = sys.argv[2]
    if module_name not in {"yt_dlp", "streamlink"}:
        raise SystemExit(f"Embedded module is not allowed: {module_name}")
    sys.argv = [module_name, *sys.argv[3:]]
    runpy.run_module(module_name, run_name="__main__", alter_sys=True)
    return 0


def _probe_release_runtime() -> int | None:
    if sys.argv[1:] != ["--probe-release-runtime"]:
        return None
    import mediapipe
    import streamlink
    from yt_dlp.version import __version__ as yt_dlp_version

    from backend.src.highlight_studio.services.face_tracking import MODEL_PATH, detect_faces

    with tempfile.TemporaryDirectory(prefix="highlight-runtime-probe-") as temp_dir:
        raw_path = Path(temp_dir) / "black.rgb"
        raw_path.write_bytes(bytes(128 * 128 * 3 * 2))
        faces = detect_faces(raw_path, width=128, height=128, fps=1)
    payload = {
        "ok": MODEL_PATH.is_file() and faces == [],
        "mediapipe": getattr(mediapipe, "__version__", "unknown"),
        "streamlink": getattr(streamlink, "__version__", "unknown"),
        "yt_dlp": yt_dlp_version,
        "face_model": str(MODEL_PATH),
        "black_frame_face_count": len(faces),
    }
    print("HS_RELEASE_RUNTIME_RESULT=" + json.dumps(payload, ensure_ascii=False))
    return 0 if payload["ok"] else 2


def main() -> int:
    multiprocessing.freeze_support()
    root = _project_root()
    root_text = str(root)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    if len(sys.argv) == 4 and sys.argv[1] == "--shorts-caption-worker":
        from backend.src.highlight_studio.services.pipeline import _shorts_caption_worker
        _shorts_caption_worker(sys.argv[2], sys.argv[3])
        return 0
    if sys.argv[1:] == ["--probe-ctranslate2"]:
        from backend.src.highlight_studio.services.hardware import _ctranslate2_probe_in_process
        print("HS_CT2_RESULT=" + json.dumps(_ctranslate2_probe_in_process()))
        return 0
    release_probe = _probe_release_runtime()
    if release_probe is not None:
        return release_probe
    embedded_result = _run_embedded_module()
    if embedded_result is not None:
        return embedded_result
    host = "127.0.0.1"
    try:
        port = int(os.environ.get("HIGHLIGHT_STUDIO_PORT", "8000"))
    except ValueError:
        port = 8000
    if not (1024 <= port <= 65535):
        raise SystemExit("HIGHLIGHT_STUDIO_PORT must be between 1024 and 65535")

    os.environ.setdefault("HIGHLIGHT_STUDIO_APP_ROOT", root_text)
    os.environ.setdefault("HIGHLIGHT_STUDIO_DESKTOP", "1")
    os.environ.setdefault("PYTHONUNBUFFERED", "1")

    # Direct import is intentional: PyInstaller can statically discover the
    # application package, unlike an Uvicorn string import.
    from backend.src.highlight_studio.api.app import app as fastapi_app

    config = uvicorn.Config(
        fastapi_app,
        host=host,
        port=port,
        access_log=False,
        log_level=os.environ.get("HIGHLIGHT_STUDIO_LOG_LEVEL", "info"),
        workers=1,
    )
    server = uvicorn.Server(config)
    fastapi_app.state.desktop_shutdown_callback = lambda: setattr(server, "should_exit", True)
    server.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
