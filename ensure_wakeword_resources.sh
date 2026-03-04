#!/usr/bin/env bash
# Ensure wake-word resources are present for configured/selected engines.
# Usage:
#   ./ensure_wakeword_resources.sh [all|precise|openwakeword|picovoice]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENGINE="${1:-all}"

# Activate venv if available (best effort)
if [ -f "$SCRIPT_DIR/.venv_orchestrator/bin/activate" ]; then
  # shellcheck disable=SC1091
  source "$SCRIPT_DIR/.venv_orchestrator/bin/activate"
elif [ -f "$SCRIPT_DIR/.venv311/bin/activate" ]; then
  # shellcheck disable=SC1091
  source "$SCRIPT_DIR/.venv311/bin/activate"
fi

MODELS_DIR_REL="${OPENWAKEWORD_MODELS_DIR:-docker/wakeword-models}"
MODELS_DIR="$SCRIPT_DIR/$MODELS_DIR_REL"
mkdir -p "$MODELS_DIR"

log() { printf '%s\n' "$*"; }
warn() { printf 'WARN: %s\n' "$*"; }

_download_if_missing() {
  local target="$1"
  local min_bytes="$2"
  shift 2

  if [ -f "$target" ]; then
    local size
    size=$(wc -c < "$target" || echo 0)
    if [ "$size" -ge "$min_bytes" ]; then
      return 0
    fi
    rm -f "$target"
  fi

  for url in "$@"; do
    if command -v curl >/dev/null 2>&1; then
      if curl -fsSL "$url" -o "$target"; then
        local size
        size=$(wc -c < "$target" || echo 0)
        if [ "$size" -ge "$min_bytes" ]; then
          return 0
        fi
      fi
    elif command -v wget >/dev/null 2>&1; then
      if wget -q "$url" -O "$target"; then
        local size
        size=$(wc -c < "$target" || echo 0)
        if [ "$size" -ge "$min_bytes" ]; then
          return 0
        fi
      fi
    fi
  done

  rm -f "$target"
  return 1
}

ensure_precise() {
  local pb="${OPENWAKEWORD_MODEL_PATH:-$MODELS_DIR_REL/hey-mycroft.pb}"
  if [[ "$pb" != /* ]]; then
    pb="$SCRIPT_DIR/$pb"
  fi
  local params="${pb}.params"
  mkdir -p "$(dirname "$pb")"

  log "Ensuring Mycroft Precise model resources..."

  if ! _download_if_missing "$pb" 20000 \
    "https://github.com/MycroftAI/precise-data/raw/master/models/hey-mycroft.pb" \
    "https://github.com/MycroftAI/precise-data/raw/models/hey-mycroft.pb"; then
    warn "Could not auto-download hey-mycroft.pb to $pb"
  else
    log "✓ precise model: $pb"
  fi

  if ! _download_if_missing "$params" 32 \
    "https://github.com/MycroftAI/precise-data/raw/master/models/hey-mycroft.pb.params" \
    "https://github.com/MycroftAI/precise-data/raw/models/hey-mycroft.pb.params"; then
    warn "Could not auto-download hey-mycroft.pb.params to $params"
  else
    log "✓ precise params: $params"
  fi
}

ensure_openwakeword() {
  log "Ensuring OpenWakeWord resources (hey_mycroft)..."
  python3 - <<'PY' || true
try:
    from openwakeword.model import Model
    m = Model(wakeword_models=['hey_mycroft'])
    print('✓ openwakeword model warm-loaded: hey_mycroft')
    del m
except Exception as e:
    print(f'WARN: openwakeword resource warm-load failed: {e}')
PY
}

ensure_picovoice() {
  log "Ensuring Picovoice runtime dependency..."
  python3 - <<'PY' || true
try:
    import pvporcupine
    print(f'✓ pvporcupine installed (v{getattr(pvporcupine, "__version__", "unknown")})')
    print('INFO: built-in keywords require PICOVOICE_ACCESS_KEY at runtime.')
except Exception as e:
    print(f'WARN: pvporcupine not available: {e}')
PY
}

case "$ENGINE" in
  all)
    ensure_precise
    ensure_openwakeword
    ensure_picovoice
    ;;
  precise)
    ensure_precise
    ;;
  openwakeword)
    ensure_openwakeword
    ;;
  picovoice)
    ensure_picovoice
    ;;
  *)
    echo "Unknown engine: $ENGINE"
    echo "Usage: $0 [all|precise|openwakeword|picovoice]"
    exit 1
    ;;
esac

log "Wakeword resource check complete."
