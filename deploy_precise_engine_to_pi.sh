#!/usr/bin/env bash
# Deploy a precise-engine tarball to Raspberry Pi and install into orchestrator venv.
# Usage: ./deploy_precise_engine_to_pi.sh [ssh-target] [path-to-precise-engine.tar.gz]

set -euo pipefail

# Default to Pi at 10.1.1.210 and standard artifact path
SSH_TARGET="${1:-stever@10.1.1.210}"
TAR_PATH="${2:-./artifacts/precise-engine-armv7/precise-engine.tar.gz}"

if [ "$#" -eq 0 ]; then
  echo "Using default SSH target: $SSH_TARGET"
  echo "Using default tarball: $TAR_PATH"
fi

if [ ! -f "$TAR_PATH" ]; then
  echo "ERROR: tarball not found: $TAR_PATH"
  exit 1
fi

echo "[1/4] Uploading tarball to $SSH_TARGET..."
scp "$TAR_PATH" "$SSH_TARGET:~/openclaw-voice/precise-engine.tar.gz"

echo "[2/4] Extracting runtime bundle and creating symlink..."
ssh "$SSH_TARGET" << 'DEPLOY_CMD'
set -euo pipefail
cd ~/openclaw-voice
VENV_BIN="$PWD/.venv_orchestrator/bin"

# Extract to ~/openclaw-voice/precise-engine
rm -rf precise-engine
tar -xzf precise-engine.tar.gz

if [ ! -x "precise-engine/precise-engine" ]; then
  echo "ERROR: precise-engine binary missing from bundle"
  ls -la precise-engine/ || true
  exit 1
fi

# Remove old symlink/directory if it exists
rm -rf "$VENV_BIN/precise-engine"

# Create symlink to the executable
ln -s "$PWD/precise-engine/precise-engine" "$VENV_BIN/precise-engine"

echo "Installed bundle at $PWD/precise-engine"
echo "Created symlink at $VENV_BIN/precise-engine"
DEPLOY_CMD

echo "[2b/5] Applying platform wakeword defaults and ensuring resources..."
ssh "$SSH_TARGET" << 'WAKE_DEFAULTS_CMD'
set -euo pipefail
cd ~/openclaw-voice

ARCH=$(uname -m)
if [[ "$ARCH" == "armv7l" || "$ARCH" == "armv6l" ]]; then
  # ARMv7 default: Precise + hey-mycroft model file
  if [ ! -f .env ]; then
    touch .enPreserving existing settings and ensuring resources..."
ssh "$SSH_TARGET" << 'WAKE_DEFAULTS_CMD'
set -euo pipefail
cd ~/openclaw-voice

# Use .env.pi if it exists, otherwise .env
ENV_FILE=".env.pi"
if [ ! -f "$ENV_FILE" ]; then
  ENV_FILE=".env"
  if [ ! -f "$ENV_FILE" ]; then
    echo "No .env or .env.pi found. Settings will be preserved from existing config."
  fi
fi

# Only set defaults if no existing wake word config
if [ -f "$ENV_FILE" ]; then
  if grep -q "PRECISE_ENABLED=true" "$ENV_FILE" 2>/dev/null; then
    echo "Using existing Precise configuration from $ENV_FILE"
  else
    echo "No Precise config found, will use defaults"
  fi
else
  echo "No env file found - settings will be initialized on first run"
fi

# Ensure wake word model files exist
bash ./setup_wakeword.sh --non-interactive precise 2>/dev/null || truet -euo pipefail
cd ~/openclaw-voice
source .venv_orchestrator/bin/activate

MODEL_PATH="docker/wakeword-models/hey-mycroft.pb"
if [ ! -f "$MODEL_PATH" ]; then
  echo "ERROR: model not found for smoke test: $MODEL_PATH"
  exit 1
fi

# Feed a short silent stream to force engine startup/import path.
if ! (head -c 65536 /dev/zero | precise-engine "$MODEL_PATH" 2048 > /tmp/precise_smoke.out 2> /tmp/precise_smoke.err); then
  true
fi

if grep -Eq "ModuleNotFoundError|Failed to execute script|Traceback" /tmp/precise_smoke.err; then
  echo "ERROR: precise-engine smoke test failed"
  cat /tmp/precise_smoke.err
  exit 1
fi

echo "precise-engine smoke test passed"
SMOKE_CMD

echo "[5/5] Running orchestrator smoke test (fresh log + sustained capture)..."
ssh "$SSH_TARGET" << 'ORCH_SMOKE_CMD'
set -euo pipefail
cd ~/openclaw-voice
source .venv_orchestrator/bin/activate

pkill -f "python.*orchestrator.main" 2>/dev/null || true
sleep 1

# Fresh log required for deterministic validation.
: > orchestrator_output.log

nohup ./run_orchestrator.sh > /tmp/orchestrator_smoke_stdout.log 2>&1 &
sleep 20

if grep -Eq "ModuleNotFoundError|Failed to execute script|Traceback" orchestrator_output.log; then
  echo "ERROR: orchestrator smoke test failed due to runtime exception"
  tail -120 orchestrator_output.log
  exit 1
fi

MIC_COUNT=$(grep -c "Mic level" orchestrator_output.log || true)
if [ "$MIC_COUNT" -lt 8 ]; then
  echo "ERROR: orchestrator smoke test did not reach sustained capture threshold (Mic level lines: $MIC_COUNT)"
  tail -120 orchestrator_output.log
  exit 1
fi

echo "orchestrator smoke test passed (Mic level lines: $MIC_COUNT)"
ORCH_SMOKE_CMD

echo "Done: deploy + runtime validation passed."
