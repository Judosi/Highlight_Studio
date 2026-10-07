from __future__ import annotations

import hashlib
import platform
import uuid


def device_code() -> str:
    source = "|".join(
        [
            platform.system(),
            platform.machine(),
            platform.node(),
            str(uuid.getnode()),
            "highlight-studio-paid-beta-v1",
        ]
    )
    return hashlib.sha256(source.encode("utf-8", errors="ignore")).hexdigest()


if __name__ == "__main__":
    print(device_code())
