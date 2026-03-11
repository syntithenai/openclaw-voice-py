#!/bin/bash

# Helper script to run the OpenClaw Voice Orchestrator
# Activates isolated Python 3.11 venv and runs orchestrator

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# Select env file in the same priority as orchestrator/config.py.
# 1) OPENCLAW_ENV_FILE, 2) .env.docker (in container), 3) .env.pi (ARM), 4) .env
ENV_FILE_PATH="${OPENCLAW_ENV_FILE:-}"
if [ -z "$ENV_FILE_PATH" ]; then
  if [ -f "/.dockerenv" ] && [ -f "$SCRIPT_DIR/.env.docker" ]; then
    ENV_FILE_PATH="$SCRIPT_DIR/.env.docker"
  else
    ARCH="$(uname -m 2>/dev/null || true)"
    if [[ "$ARCH" == arm* ]] && [ -f "$SCRIPT_DIR/.env.pi" ]; then
      ENV_FILE_PATH="$SCRIPT_DIR/.env.pi"
    else
      ENV_FILE_PATH="$SCRIPT_DIR/.env"
    fi
  fi
fi
export OPENCLAW_ENV_FILE="$ENV_FILE_PATH"

# Kill any existing orchestrator process
pkill -f "python.*orchestrator.main" 2>/dev/null || true
sleep 1

# Activate isolated venv
# Try .venv_orchestrator first (Raspbian), then fall back to .venv311 (Ubuntu)
VENV_PATH=""
if [ -f "$SCRIPT_DIR/.venv_orchestrator/bin/activate" ]; then
  VENV_PATH="$SCRIPT_DIR/.venv_orchestrator"
elif [ -f "$SCRIPT_DIR/.venv311/bin/activate" ]; then
  VENV_PATH="$SCRIPT_DIR/.venv311"
else
  echo "WARN: No virtual environment found. Bootstrapping .venv_orchestrator..."
  if ! command -v python3 >/dev/null 2>&1; then
    echo "ERROR: python3 not found. Please install Python 3 and rerun."
    exit 1
  fi

  python3 -m venv "$SCRIPT_DIR/.venv_orchestrator" || {
    echo "ERROR: Failed to create virtual environment at .venv_orchestrator"
    exit 1
  }

  source "$SCRIPT_DIR/.venv_orchestrator/bin/activate"
  python -m pip install --upgrade pip setuptools wheel || {
    echo "ERROR: Failed to bootstrap pip tooling"
    exit 1
  }

  if [ -f "$SCRIPT_DIR/requirements.txt" ]; then
    python -m pip install -r "$SCRIPT_DIR/requirements.txt" || {
      echo "ERROR: Failed to install requirements.txt"
      exit 1
    }
  fi

  VENV_PATH="$SCRIPT_DIR/.venv_orchestrator"
  echo "✓ Created and initialized .venv_orchestrator"
fi

source "$VENV_PATH/bin/activate"

# Resolve whether media key capture is explicitly enabled. Do not force-enable
# it from this launcher, because broad captures can lock keyboard/media devices
# unexpectedly when no device filter is configured.
MEDIA_KEYS_ENABLED_VALUE="${MEDIA_KEYS_ENABLED:-}"
if [ -z "$MEDIA_KEYS_ENABLED_VALUE" ] && [ -f "$ENV_FILE_PATH" ]; then
  MEDIA_KEYS_ENABLED_VALUE="$(grep -E '^MEDIA_KEYS_ENABLED=' "$ENV_FILE_PATH" | tail -n1 | cut -d= -f2-)"
fi
MEDIA_KEYS_ENABLED_NORMALIZED="$(printf '%s' "$MEDIA_KEYS_ENABLED_VALUE" | tr '[:upper:]' '[:lower:]' | xargs)"
MEDIA_KEYS_CAPTURE_ENABLED=false
if [ "$MEDIA_KEYS_ENABLED_NORMALIZED" = "true" ] || [ "$MEDIA_KEYS_ENABLED_NORMALIZED" = "1" ] || [ "$MEDIA_KEYS_ENABLED_NORMALIZED" = "yes" ]; then
  MEDIA_KEYS_CAPTURE_ENABLED=true
fi

# Ensure key runtime deps exist even for pre-existing venvs created before
# requirements changed.
if ! "$VENV_PATH/bin/python" -c "import httpx" >/dev/null 2>&1; then
  echo "INFO: Missing Python dependency 'httpx' in $VENV_PATH. Installing requirements..."
  if [ -f "$SCRIPT_DIR/requirements.txt" ]; then
    "$VENV_PATH/bin/python" -m pip install -r "$SCRIPT_DIR/requirements.txt" || {
      echo "ERROR: Failed to install requirements.txt"
      exit 1
    }
  else
    echo "ERROR: requirements.txt not found; cannot auto-install missing dependencies"
    exit 1
  fi
fi

# Ensure this process has an active 'input' group only when media key capture is enabled.
if [ "$MEDIA_KEYS_CAPTURE_ENABLED" = true ] && [ "${OPENCLAW_DISABLE_INPUT_GROUP_REEXEC:-0}" != "1" ]; then
  ACTIVE_GROUPS="$(id -nG 2>/dev/null || true)"
  if [ "$EUID" -ne 0 ] && ! printf '%s' "$ACTIVE_GROUPS" | grep -qw input; then
    MEMBER_GROUPS="$(id -nG "$USER" 2>/dev/null || true)"
    if printf '%s' "$MEMBER_GROUPS" | grep -qw input; then
      if [ -z "${OPENCLAW_INPUT_GROUP_REEXEC:-}" ] && command -v sg >/dev/null 2>&1; then
        echo "INFO: 'input' group is not active in this shell. Re-launching via 'sg input' so speaker media buttons can be grabbed..."
        args_escaped=""
        for arg in "$@"; do
          printf -v q '%q' "$arg"
          args_escaped+=" $q"
        done
        printf -v script_dir_q '%q' "$SCRIPT_DIR"
        printf -v script_path_q '%q' "$0"
        exec sg input -c "cd $script_dir_q && OPENCLAW_INPUT_GROUP_REEXEC=1 $script_path_q$args_escaped"
      fi
      if ! printf '%s' "$(id -nG 2>/dev/null || true)" | grep -qw input; then
        echo "WARN: Could not activate 'input' group for this process. Media buttons may control system playback instead of wakeword/media handling."
      fi
    else
      echo "WARN: User '$USER' is not in group 'input'. Run: sudo usermod -aG input $USER && re-login"
    fi
  fi
fi

if [ "$MEDIA_KEYS_CAPTURE_ENABLED" = true ] && [ -f "$ENV_FILE_PATH" ] && ! grep -qE '^MEDIA_KEYS_DEVICE_FILTER=' "$ENV_FILE_PATH"; then
  echo "WARN: MEDIA_KEYS_ENABLED=true but MEDIA_KEYS_DEVICE_FILTER is not set."
  echo "      Set MEDIA_KEYS_DEVICE_FILTER to your speaker name (e.g. Burr-Brown/Anker) to avoid grabbing non-speaker devices."
fi

# Run orchestrator with any passed arguments (tee output to log)
set -o pipefail
"$VENV_PATH/bin/python" -m orchestrator.main "$@" 2>&1 | tee -a "$SCRIPT_DIR/orchestrator_output.log"
