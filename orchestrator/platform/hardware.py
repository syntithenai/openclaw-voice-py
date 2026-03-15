import platform
from pathlib import Path


def is_raspberry_pi() -> bool:
    """Best-effort Raspberry Pi detection for Pi-specific audio workarounds."""
    try:
        model_path = Path("/proc/device-tree/model")
        if model_path.exists():
            model = model_path.read_text(encoding="utf-8", errors="ignore").lower()
            if "raspberry pi" in model:
                return True
    except Exception:
        pass
    machine = platform.machine().lower()
    return machine.startswith("arm") or machine.startswith("aarch64")
