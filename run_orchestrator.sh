#!/bin/bash

# Helper script to run the OpenClaw Voice Orchestrator
# Activates isolated Python 3.11 venv and runs orchestrator

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

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

# Run orchestrator with any passed arguments (tee output to log)
set -o pipefail
python -m orchestrator.main "$@" 2>&1 | tee -a "$SCRIPT_DIR/orchestrator_output.log"
