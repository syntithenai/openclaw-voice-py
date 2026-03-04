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

PLAYBACK_DEVICE="$(grep -E '^AUDIO_PLAYBACK_DEVICE=' "$ENV_FILE" | tail -n1 | cut -d'=' -f2- || true)"
PLAYBACK_DEVICE="${PLAYBACK_DEVICE:-default}"
PLAYBACK_SAMPLE_RATE="$(grep -E '^AUDIO_PLAYBACK_SAMPLE_RATE=' "$ENV_FILE" | tail -n1 | cut -d'=' -f2- || true)"
if [[ -z "$PLAYBACK_SAMPLE_RATE" ]]; then
  PLAYBACK_SAMPLE_RATE="$(grep -E '^AUDIO_SAMPLE_RATE=' "$ENV_FILE" | tail -n1 | cut -d'=' -f2- || true)"
fi
PLAYBACK_SAMPLE_RATE="${PLAYBACK_SAMPLE_RATE:-16000}"

echo "Running speaker test"
echo "  Device: $PLAYBACK_DEVICE"
echo "  Sample rate: $PLAYBACK_SAMPLE_RATE"

"$PYTHON_BIN" - <<PY
import math
import time

import numpy as np
import sounddevice as sd

device = ${PLAYBACK_DEVICE@Q}
sample_rate = int(${PLAYBACK_SAMPLE_RATE@Q})

if device.isdigit():
    device = int(device)


def pick_working_samplerate(dev, preferred):
  common_rates = [192000, 176400, 96000, 88200, 48000, 44100, 32000, 24000, 22050, 16000, 12000, 11025, 8000]
  ordered = [preferred] + [r for r in common_rates if r != preferred]

  for rate in ordered:
    try:
      sd.check_output_settings(device=dev, samplerate=rate, channels=1)
      return rate
    except Exception:
      pass

  try:
    info = sd.query_devices(dev)
    fallback = int(round(float(info.get("default_samplerate", 16000) or 16000)))
    sd.check_output_settings(device=dev, samplerate=fallback, channels=1)
    return fallback
  except Exception:
    return None


working_rate = pick_working_samplerate(device, sample_rate)
if working_rate is None:
  raise RuntimeError(f"No valid sample rate found for output device {device}")

if working_rate != sample_rate:
  print(f"Configured sample rate {sample_rate} Hz is not supported by device {device}; using {working_rate} Hz")

sample_rate = working_rate


def tone(freq: float, duration: float, volume: float = 0.25):
    t = np.linspace(0, duration, int(sample_rate * duration), endpoint=False)
    env = np.minimum(1.0, np.minimum(t * 80.0, (duration - t) * 80.0))
    return (volume * env * np.sin(2 * np.pi * freq * t)).astype(np.float32)

# Left-right alternating tone sequence helps verify channel/output routing.
sequence = [
    (440.0, 0.25),
    (660.0, 0.25),
    (880.0, 0.25),
]

print("Playing startup tones...")
for freq, dur in sequence:
    data = tone(freq, dur)
    sd.play(data, samplerate=sample_rate, device=device)
    sd.wait()
    time.sleep(0.08)

print("Playing 1-second sweep...")
duration = 1.0
t = np.linspace(0, duration, int(sample_rate * duration), endpoint=False)
start_f = 300.0
end_f = 1200.0
freq = start_f + (end_f - start_f) * (t / duration)
phase = 2 * np.pi * np.cumsum(freq) / sample_rate
sweep = (0.2 * np.sin(phase)).astype(np.float32)
sd.play(sweep, samplerate=sample_rate, device=device)
sd.wait()

print("Done. If you heard tones + sweep, output device is working.")
PY
