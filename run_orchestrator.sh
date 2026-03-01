#!/bin/bash

# Helper script to run the OpenClaw Voice Orchestrator
# Activates isolated Python 3.11 venv and runs orchestrator

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# Kill any existing orchestrator process
pkill -f "python.*orchestrator.main" 2>/dev/null || true
sleep 1

# Activate isolated venv
# Try .venv_orchestrator first (Raspbian), then fall back to .venv311 (Ubuntu)
if [ -f "$SCRIPT_DIR/.venv_orchestrator/bin/activate" ]; then
  source "$SCRIPT_DIR/.venv_orchestrator/bin/activate"
elif [ -f "$SCRIPT_DIR/.venv311/bin/activate" ]; then
  source "$SCRIPT_DIR/.venv311/bin/activate"
else
  echo "ERROR: No virtual environment found (.venv_orchestrator or .venv311)"
  exit 1
fi

# Run orchestrator with any passed arguments (tee output to log)
set -o pipefail
python -m orchestrator.main "$@" 2>&1 | tee -a "$SCRIPT_DIR/orchestrator_output.log"
