from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))



def main() -> int:
    from backend.src.highlight_studio.services.hardware import detect_hardware_capabilities
    parser = argparse.ArgumentParser()
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--require-gpu', action='store_true')
    args = parser.parse_args()
    data = detect_hardware_capabilities(force=True)
    nvidia = data.get('nvidia') or {}
    ct2 = data.get('ctranslate2') or {}
    ffmpeg = data.get('ffmpeg') or {}
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(data.get('summary') or '')
        print(f"NVIDIA: {'OK' if nvidia.get('ok') else 'not found'}")
        print(f"CTranslate2 CUDA backend: {'OK' if ct2.get('cuda_ok') else 'NOT READY'} - {ct2.get('hint','')}")
        print(f"NVENC: {'OK' if ffmpeg.get('nvenc_runtime_ok') else 'NOT READY'} - {ffmpeg.get('hint','')}")
    if args.require_gpu and nvidia.get('ok') and not ct2.get('cuda_ok'):
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
