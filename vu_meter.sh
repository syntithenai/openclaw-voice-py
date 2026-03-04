#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "ERROR: .env not found at $ENV_FILE"
  exit 1
fi

choose_python() {
  local candidates=(
    "$SCRIPT_DIR/.venv_orchestrator/bin/python"
    "python3"
    "python"
  )

  for py in "${candidates[@]}"; do
    if command -v "$py" >/dev/null 2>&1; then
      if "$py" - <<'PY' >/dev/null 2>&1
import sounddevice, numpy
print("ok")
PY
      then
        echo "$py"
        return 0
      fi
    fi
  done

  return 1
}

PYTHON_BIN="$(choose_python || true)"
if [[ -z "$PYTHON_BIN" ]]; then
  echo "ERROR: Could not find Python with sounddevice and numpy installed."
  echo "Try: source .venv_orchestrator/bin/activate"
  exit 1
fi

CAPTURE_DEVICE="$(grep -E '^AUDIO_CAPTURE_DEVICE=' "$ENV_FILE" | tail -n1 | cut -d'=' -f2- || true)"
CAPTURE_DEVICE="${CAPTURE_DEVICE:-default}"
SAMPLE_RATE="$(grep -E '^AUDIO_SAMPLE_RATE=' "$ENV_FILE" | tail -n1 | cut -d'=' -f2- || true)"
SAMPLE_RATE="${SAMPLE_RATE:-16000}"

echo "Starting VU meter (Ctrl+C to stop)"
echo "  Device: $CAPTURE_DEVICE"
echo "  Sample rate: $SAMPLE_RATE"
echo ""

"$PYTHON_BIN" - <<PY
import math
import sys
import time

import numpy as np
import sounddevice as sd

device = ${CAPTURE_DEVICE@Q}
sample_rate = int(${SAMPLE_RATE@Q})
blocksize = int(sample_rate * 0.1)  # 100ms

if device.isdigit():
    device = int(device)

bar_width = 50

print("Listening... speak into the mic.")


def render(rms: float, peak: float) -> None:
    rms = max(0.0, min(1.0, rms))
    peak = max(0.0, min(1.0, peak))
    db = -120.0 if rms <= 1e-8 else 20.0 * math.log10(rms)
    filled = int(rms * bar_width)
    peak_pos = min(bar_width - 1, int(peak * bar_width))

    chars = [" "] * bar_width
    for i in range(filled):
        chars[i] = "█"
    if 0 <= peak_pos < bar_width:
        chars[peak_pos] = "▌"

    line = "[{}] RMS={:.4f} {:6.1f} dBFS".format("".join(chars), rms, db)
    sys.stdout.write("\r" + line)
    sys.stdout.flush()


with sd.InputStream(device=device, channels=1, samplerate=sample_rate, blocksize=blocksize, dtype="float32") as stream:
    try:
        while True:
            data, overflowed = stream.read(blocksize)
            if overflowed:
                sys.stdout.write("\nWARNING: input overflow\n")
            mono = np.squeeze(data)
            if mono.size == 0:
                continue
            rms = float(np.sqrt(np.mean(np.square(mono))))
            peak = float(np.max(np.abs(mono)))
            render(rms, peak)
            time.sleep(0.02)
    except KeyboardInterrupt:
        pass

print("\nDone.")
PY
