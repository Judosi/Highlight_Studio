# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, copy_metadata

ROOT = Path(SPECPATH).resolve().parents[1]
ENTRY = ROOT / "backend" / "desktop_entry.py"

hiddenimports = [
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.lifespan.on",
    "multipart",
    "multipart.multipart",
    "audioop",
    "faster_whisper",
    "faster_whisper.transcribe",
    "faster_whisper.vad",
    "onnxruntime",
    "onnxruntime.capi",
    "onnxruntime.capi._pybind_state",
    "ctranslate2",
    "av",
    "yt_dlp",
    "yt_dlp.__main__",
    "streamlink",
    "streamlink.__main__",
    "streamlink_cli.main",
]

datas = []
for distribution in [
    "fastapi",
    "uvicorn",
    "pydantic",
    "python-multipart",
    "requests",
    "httpx",
    "faster-whisper",
    "onnxruntime",
    "ctranslate2",
    "av",
    "yt-dlp",
    "streamlink",
    "audioop-lts",
    "cryptography",
]:
    try:
        datas += copy_metadata(distribution)
    except Exception:
        pass

datas += collect_data_files("faster_whisper", includes=["assets/*.onnx"])

binaries = []
for package in ["ctranslate2", "av", "onnxruntime"]:
    try:
        binaries += collect_dynamic_libs(package)
    except Exception:
        pass

analysis = Analysis(
    [str(ENTRY)],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "matplotlib",
        "notebook",
        "IPython",
        "tensorflow",
        "torch",
        "onnx",
        "pandas",
        "scipy",
    ],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="HighlightStudioEngine",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)

collect = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="HighlightStudioEngine",
)
