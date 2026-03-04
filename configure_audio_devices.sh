#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "ERROR: .env file not found at $ENV_FILE"
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
import sounddevice
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
  echo "ERROR: Could not find Python with sounddevice installed."
  echo "Try activating your orchestrator venv first: source .venv_orchestrator/bin/activate"
  exit 1
fi

trim() {
  local var="$*"
  var="${var#"${var%%[![:space:]]*}"}"
  var="${var%"${var##*[![:space:]]}"}"
  printf '%s' "$var"
}

get_env_value() {
  local key="$1"
  local val
  val="$(grep -E "^${key}=" "$ENV_FILE" | tail -n1 | cut -d'=' -f2- || true)"
  trim "$val"
}

get_supported_output_rates() {
  local device_id="$1"

  "$PYTHON_BIN" - "$device_id" <<'PY'
import sys

import sounddevice as sd

device = int(sys.argv[1])
common_rates = [8000, 11025, 12000, 16000, 22050, 24000, 32000, 44100, 48000, 88200, 96000, 176400, 192000]
supported = []

for rate in common_rates:
    try:
        sd.check_output_settings(device=device, samplerate=rate, channels=1)
        supported.append(rate)
    except Exception:
        pass

if not supported:
    try:
        info = sd.query_devices(device)
        default_sr = int(round(float(info.get('default_samplerate', 16000) or 16000)))
        supported = [default_sr]
    except Exception:
        supported = [16000]

supported = sorted(set(supported))
print(" ".join(str(x) for x in supported))
PY
}

escape_sed_replacement() {
  printf '%s' "$1" | sed -e 's/[\\&|]/\\&/g'
}

upsert_env() {
  local key="$1"
  local value="$2"
  local escaped
  escaped="$(escape_sed_replacement "$value")"

  if grep -qE "^${key}=" "$ENV_FILE"; then
    sed -i "s|^${key}=.*|${key}=${escaped}|" "$ENV_FILE"
  else
    printf '\n%s=%s\n' "$key" "$value" >> "$ENV_FILE"
  fi
}

echo ""
echo "Detecting audio devices using: $PYTHON_BIN"
echo ""

DEVICES_RAW="$($PYTHON_BIN - <<'PY'
import re
import sounddevice as sd

for idx, d in enumerate(sd.query_devices()):
    name = str(d.get('name', '')).replace('|', '/').strip()
    inch = int(d.get('max_input_channels', 0) or 0)
    outch = int(d.get('max_output_channels', 0) or 0)
    hostapi_idx = int(d.get('hostapi', -1) or -1)
    try:
        hostapi_name = sd.query_hostapis(hostapi_idx).get('name', '')
    except Exception:
        hostapi_name = ''
    hostapi_name = str(hostapi_name).replace('|', '/').strip()
    m = re.search(r'\(hw:(\d+,\d+)\)', name)
    alsa_hw = f"hw:{m.group(1)}" if m else ""
    print(f"{idx}|{name}|{inch}|{outch}|{hostapi_name}|{alsa_hw}")
PY
)"

if [[ -z "$DEVICES_RAW" ]]; then
  echo "ERROR: No audio devices found."
  exit 1
fi

mapfile -t DEVICE_LINES < <(printf '%s\n' "$DEVICES_RAW")

declare -a INPUT_IDS=()
declare -a INPUT_LABELS=()
declare -a INPUT_ENV_VALUES=()
declare -a OUTPUT_IDS=()
declare -a OUTPUT_LABELS=()
declare -a OUTPUT_ENV_VALUES=()

for line in "${DEVICE_LINES[@]}"; do
  IFS='|' read -r idx name inch outch hostapi alsa_hw <<< "$line"
  env_value="$idx"
  if [[ -n "${alsa_hw:-}" ]]; then
    env_value="$alsa_hw"
  fi

  if [[ "${inch:-0}" -gt 0 ]]; then
    INPUT_IDS+=("$idx")
    INPUT_LABELS+=("#$idx $name ($hostapi) [in:$inch out:$outch]")
    INPUT_ENV_VALUES+=("$env_value")
  fi
  if [[ "${outch:-0}" -gt 0 ]]; then
    OUTPUT_IDS+=("$idx")
    OUTPUT_LABELS+=("#$idx $name ($hostapi) [in:$inch out:$outch]")
    OUTPUT_ENV_VALUES+=("$env_value")
  fi
done

if [[ ${#INPUT_IDS[@]} -eq 0 ]]; then
  echo "ERROR: No input-capable devices found."
  exit 1
fi

if [[ ${#OUTPUT_IDS[@]} -eq 0 ]]; then
  echo "ERROR: No output-capable devices found."
  exit 1
fi

pick_default_menu_index() {
  local -n ids_ref=$1
  local -n labels_ref=$2
  local -n env_ref=$3
  local current_env="$4"

  local i

  # 1) Prefer PipeWire devices
  for i in "${!labels_ref[@]}"; do
    if [[ "${labels_ref[$i],,}" == *"pipewire"* ]]; then
      echo "$i"
      return
    fi
  done

  # 2) Prefer PulseAudio devices
  for i in "${!labels_ref[@]}"; do
    if [[ "${labels_ref[$i],,}" == *"pulseaudio"* || "${labels_ref[$i],,}" == *"pulse"* ]]; then
      echo "$i"
      return
    fi
  done

  # 3) Prefer USB devices
  for i in "${!labels_ref[@]}"; do
    if [[ "${labels_ref[$i],,}" == *"usb"* ]]; then
      echo "$i"
      return
    fi
  done

  # 4) Fall back to current .env value if it exists in list
  if [[ -n "$current_env" ]]; then
    for i in "${!ids_ref[@]}"; do
      if [[ "${ids_ref[$i]}" == "$current_env" || "${env_ref[$i]}" == "$current_env" ]]; then
        echo "$i"
        return
      fi
    done
  fi

  # 5) First device
  echo "0"
}

CURRENT_INPUT="$(get_env_value AUDIO_CAPTURE_DEVICE)"
CURRENT_OUTPUT="$(get_env_value AUDIO_PLAYBACK_DEVICE)"
CURRENT_PLAYBACK_SR="$(get_env_value AUDIO_PLAYBACK_SAMPLE_RATE)"

DEFAULT_OUTPUT_MENU_INDEX="$(pick_default_menu_index OUTPUT_IDS OUTPUT_LABELS OUTPUT_ENV_VALUES "$CURRENT_OUTPUT")"

DEFAULT_INPUT_MENU_INDEX="$(pick_default_menu_index INPUT_IDS INPUT_LABELS INPUT_ENV_VALUES "$CURRENT_INPUT")"

prompt_choice() {
  local title="$1"
  local -n ids_ref=$2
  local -n labels_ref=$3
  local default_idx="$4"

  echo "=== $title ===" >&2
  local i
  for i in "${!ids_ref[@]}"; do
    local marker=" "
    if [[ "$i" == "$default_idx" ]]; then
      marker="*"
    fi
    printf " %s [%2d] id=%-3s %s\n" "$marker" "$i" "${ids_ref[$i]}" "${labels_ref[$i]}" >&2
  done
  echo "" >&2
  printf "Select menu index (Enter for default [%s] -> id=%s): " "$default_idx" "${ids_ref[$default_idx]}" >&2

  local choice
  read -r choice
  choice="$(trim "$choice")"

  if [[ -z "$choice" ]]; then
    choice="$default_idx"
  fi

  if ! [[ "$choice" =~ ^[0-9]+$ ]]; then
    echo "ERROR: Invalid choice '$choice'" >&2
    return 1
  fi

  if (( choice < 0 || choice >= ${#ids_ref[@]} )); then
    echo "ERROR: Choice out of range: $choice" >&2
    return 1
  fi

  echo "${ids_ref[$choice]}"
}

echo "Current .env values:"
echo "  AUDIO_CAPTURE_DEVICE=${CURRENT_INPUT:-<unset>}"
echo "  AUDIO_PLAYBACK_DEVICE=${CURRENT_OUTPUT:-<unset>}"
echo ""
echo "Defaults are marked with '*' (PipeWire → PulseAudio → USB-preferred)."
echo ""

SELECTED_INPUT="$(prompt_choice "Input devices" INPUT_IDS INPUT_LABELS "$DEFAULT_INPUT_MENU_INDEX")"
SELECTED_OUTPUT="$(prompt_choice "Output devices" OUTPUT_IDS OUTPUT_LABELS "$DEFAULT_OUTPUT_MENU_INDEX")"

resolve_env_value_for_id() {
  local -n ids_ref=$1
  local -n env_ref=$2
  local selected_id="$3"
  local i
  for i in "${!ids_ref[@]}"; do
    if [[ "${ids_ref[$i]}" == "$selected_id" ]]; then
      echo "${env_ref[$i]}"
      return
    fi
  done
  echo "$selected_id"
}

SELECTED_INPUT_ENV="$(resolve_env_value_for_id INPUT_IDS INPUT_ENV_VALUES "$SELECTED_INPUT")"
SELECTED_OUTPUT_ENV="$(resolve_env_value_for_id OUTPUT_IDS OUTPUT_ENV_VALUES "$SELECTED_OUTPUT")"

SUPPORTED_OUTPUT_RATES_RAW="$(get_supported_output_rates "$SELECTED_OUTPUT")"
read -r -a SUPPORTED_OUTPUT_RATES <<< "$SUPPORTED_OUTPUT_RATES_RAW"

if [[ ${#SUPPORTED_OUTPUT_RATES[@]} -eq 0 ]]; then
  SUPPORTED_OUTPUT_RATES=(16000)
fi

# Default to highest available sample rate.
DEFAULT_PLAYBACK_RATE="${SUPPORTED_OUTPUT_RATES[-1]}"
if [[ -n "$CURRENT_PLAYBACK_SR" ]]; then
  for r in "${SUPPORTED_OUTPUT_RATES[@]}"; do
    if [[ "$r" == "$CURRENT_PLAYBACK_SR" ]]; then
      DEFAULT_PLAYBACK_RATE="$CURRENT_PLAYBACK_SR"
      break
    fi
  done
fi

echo "" >&2
echo "=== Output sample rates for device id=$SELECTED_OUTPUT ===" >&2
DEFAULT_PLAYBACK_RATE_OPTION=1
for i in "${!SUPPORTED_OUTPUT_RATES[@]}"; do
  r="${SUPPORTED_OUTPUT_RATES[$i]}"
  option=$((i + 1))
  marker=" "
  if [[ "$r" == "$DEFAULT_PLAYBACK_RATE" ]]; then
    marker="*"
    DEFAULT_PLAYBACK_RATE_OPTION="$option"
  fi
  printf " %s [%d] %s Hz\n" "$marker" "$option" "$r" >&2
done
printf "Select playback sample-rate option (1-%d, Enter for default [%d] -> %s Hz): " "${#SUPPORTED_OUTPUT_RATES[@]}" "$DEFAULT_PLAYBACK_RATE_OPTION" "$DEFAULT_PLAYBACK_RATE" >&2
read -r SELECTED_PLAYBACK_RATE_OPTION
SELECTED_PLAYBACK_RATE_OPTION="$(trim "$SELECTED_PLAYBACK_RATE_OPTION")"
if [[ -z "$SELECTED_PLAYBACK_RATE_OPTION" ]]; then
  SELECTED_PLAYBACK_RATE_OPTION="$DEFAULT_PLAYBACK_RATE_OPTION"
fi

if ! [[ "$SELECTED_PLAYBACK_RATE_OPTION" =~ ^[0-9]+$ ]]; then
  echo "ERROR: Invalid playback sample-rate option: $SELECTED_PLAYBACK_RATE_OPTION" >&2
  exit 1
fi

if (( SELECTED_PLAYBACK_RATE_OPTION < 1 || SELECTED_PLAYBACK_RATE_OPTION > ${#SUPPORTED_OUTPUT_RATES[@]} )); then
  echo "ERROR: Playback sample-rate option out of range: $SELECTED_PLAYBACK_RATE_OPTION" >&2
  exit 1
fi

SELECTED_PLAYBACK_RATE="${SUPPORTED_OUTPUT_RATES[$((SELECTED_PLAYBACK_RATE_OPTION - 1))]}"

# Write stable ALSA names (hw:X,Y) when available; otherwise fall back to numeric IDs.
upsert_env AUDIO_CAPTURE_DEVICE "$SELECTED_INPUT_ENV"
upsert_env AUDIO_PLAYBACK_DEVICE "$SELECTED_OUTPUT_ENV"
upsert_env AUDIO_PLAYBACK_SAMPLE_RATE "$SELECTED_PLAYBACK_RATE"

echo ""
echo "Updated $ENV_FILE"
echo "  AUDIO_CAPTURE_DEVICE=$SELECTED_INPUT_ENV"
echo "  AUDIO_PLAYBACK_DEVICE=$SELECTED_OUTPUT_ENV"
echo "  AUDIO_PLAYBACK_SAMPLE_RATE=$SELECTED_PLAYBACK_RATE"
echo ""
echo "Tip: run ./vu_meter.sh to validate microphone input and ./test_speaker.sh to validate output."
