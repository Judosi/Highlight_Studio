# Bundled FFmpeg

Windows installer builds expect the following runtime files:

```text
vendor/ffmpeg/bin/ffmpeg.exe
vendor/ffmpeg/bin/ffprobe.exe
vendor/ffmpeg/bin/*.dll   # when using a shared build
```

Prepare them with:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/windows/fetch_ffmpeg.ps1
```

The download script stores the source URL and copies license/readme files into
`vendor/ffmpeg/licenses/`. Do not publish an installer without retaining the
license materials for the exact FFmpeg build that is shipped.
