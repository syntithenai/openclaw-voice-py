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
AUDIO_OUTPUT_GAIN="$(grep -E '^AUDIO_OUTPUT_GAIN=' "$ENV_FILE" | tail -n1 | cut -d'=' -f2- || true)"
AUDIO_OUTPUT_GAIN="${AUDIO_OUTPUT_GAIN:-1.0}"
PIPER_URL="$(grep -E '^PIPER_URL=' "$ENV_FILE" | tail -n1 | cut -d'=' -f2- || true)"
PIPER_URL="${PIPER_URL:-http://localhost:10001}"
PIPER_VOICE_ID="$(grep -E '^PIPER_VOICE_ID=' "$ENV_FILE" | tail -n1 | cut -d'=' -f2- || true)"
PIPER_VOICE_ID="${PIPER_VOICE_ID:-en_US-amy-medium}"

echo "Running speaker test"
echo "  Device: $PLAYBACK_DEVICE"
echo "  Sample rate: $PLAYBACK_SAMPLE_RATE"
echo "  Output gain: ${AUDIO_OUTPUT_GAIN}x"
echo "  Piper URL: $PIPER_URL"

"$PYTHON_BIN" - <<PY
import io
import json
import wave
import time
import urllib.request
import urllib.error

import numpy as np
import sounddevice as sd

device = ${PLAYBACK_DEVICE@Q}
sample_rate = int(${PLAYBACK_SAMPLE_RATE@Q})
output_gain = float(${AUDIO_OUTPUT_GAIN@Q})
piper_url = ${PIPER_URL@Q}
piper_voice_id = ${PIPER_VOICE_ID@Q}

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


def tone(freq: float, duration: float, volume: float = 0.75):
    """Generate test tone with output gain applied"""
    t = np.linspace(0, duration, int(sample_rate * duration), endpoint=False)
    env = np.minimum(1.0, np.minimum(t * 80.0, (duration - t) * 80.0))
    audio = (volume * env * np.sin(2 * np.pi * freq * t)).astype(np.float32)
    # Apply output gain to match orchestrator playback
    audio = (audio * output_gain).astype(np.float32)
    # Clip to prevent distortion
    audio = np.clip(audio, -1.0, 1.0)
    return audio

# Left-right alternating tone sequence helps verify channel/output routing.
sequence = [
    (440.0, 0.25),
    (660.0, 0.25),
    (880.0, 0.25),
]

print(f"Playing startup tones with {output_gain:.2f}x gain...")
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
sweep = (0.6 * np.sin(phase)).astype(np.float32)
# Apply output gain
sweep = (sweep * output_gain).astype(np.float32)
sweep = np.clip(sweep, -1.0, 1.0)
sd.play(sweep, samplerate=sample_rate, device=device)
sd.wait()

# Test TTS generation and playback
print(f"\\nGenerating TTS test from {piper_url}...")
test_text = "This is a speaker test. You should hear this at the configured volume with output gain applied."

try:
    # Generate TTS audio from Piper via /synthesize endpoint
    req = urllib.request.Request(
        f"{piper_url}/synthesize",
        data=json.dumps({
            "text": test_text,
            "voice": piper_voice_id
        }).encode('utf-8'),
        headers={'Content-Type': 'application/json'}
    )
    
    with urllib.request.urlopen(req, timeout=10) as response:
        if response.status != 200:
            print(f"TTS generation failed: HTTP {response.status}")
        else:
            # Read WAV audio from response
            wav_data = response.read()
            
            # Parse WAV file
            with wave.open(io.BytesIO(wav_data), 'rb') as wav_file:
                n_channels = wav_file.getnchannels()
                sample_width = wav_file.getsampwidth()
                wav_sample_rate = wav_file.getframerate()
                n_frames = wav_file.getnframes()
                audio_data = wav_file.readframes(n_frames)
            
            # Convert bytes to numpy array (16-bit PCM)
            audio_int16 = np.frombuffer(audio_data, dtype=np.int16)
            
            # Convert to float32 [-1.0, 1.0]
            audio_float = audio_int16.astype(np.float32) / 32768.0
            
            # Resample if needed to match device playback rate
            playback_rate = working_rate
            if wav_sample_rate != playback_rate:
                print(f"Resampling TTS from {wav_sample_rate} Hz to {playback_rate} Hz...")
                # Simple linear interpolation resampling
                ratio = playback_rate / wav_sample_rate
                new_length = int(len(audio_float) * ratio)
                resampled = np.interp(
                    np.linspace(0, len(audio_float) - 1, new_length),
                    np.arange(len(audio_float)),
                    audio_float
                )
                audio_float = resampled.astype(np.float32)
                wav_sample_rate = playback_rate
            
            # Apply output gain (same as orchestrator does)
            audio_float = (audio_float * output_gain).astype(np.float32)
            audio_float = np.clip(audio_float, -1.0, 1.0)
            
            print(f"Playing TTS audio ({len(audio_float)} samples, {len(audio_float)/wav_sample_rate:.2f}s) with {output_gain:.2f}x gain...")
            sd.play(audio_float, samplerate=wav_sample_rate, device=device)
            sd.wait()
            
            print("\\n✓ TTS test complete!")

except urllib.error.URLError as e:
    print(f"Could not connect to Piper at {piper_url}: {e}")
    print("Skipping TTS test. Make sure Piper is running.")
except urllib.error.HTTPError as e:
    print(f"TTS request failed: HTTP {e.code} - {e.reason}")
    print("Skipping TTS test. Check Piper API endpoint and configuration.")
except Exception as e:
    print(f"TTS test failed: {e}")
    print("Skipping TTS test.")

print("\\nDone. If you heard tones + sweep + TTS, output device is working correctly.")
print(f"TTS volume matches what you'll hear from the orchestrator (gain: {output_gain:.2f}x).")
PY
