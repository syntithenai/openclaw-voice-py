#!/usr/bin/env bash
# Deploy a precise-engine tarball to Raspberry Pi and install into orchestrator venv.
# Usage: ./deploy_precise_engine_to_pi.sh <ssh-target> <path-to-precise-engine.tar.gz>

set -euo pipefail

if [ "$#" -ne 2 ]; then
  echo "Usage: $0 <ssh-target> <path-to-precise-engine.tar.gz>"
  echo "Example: $0 pi ./artifacts/precise-engine-armv7/precise-engine.tar.gz"
  exit 1
fi

SSH_TARGET="$1"
TAR_PATH="$2"

if [ ! -f "$TAR_PATH" ]; then
  echo "ERROR: tarball not found: $TAR_PATH"
  exit 1
fi

echo "[1/4] Uploading tarball to $SSH_TARGET..."
scp "$TAR_PATH" "$SSH_TARGET:~/openclaw-voice/precise-engine.tar.gz"

echo "[2/4] Extracting runtime bundle and installing launcher..."
ssh "$SSH_TARGET" << 'DEPLOY_CMD'
set -euo pipefail
cd ~/openclaw-voice
VENV_BIN="$PWD/.venv_orchestrator/bin"
rm -rf /tmp/precise-engine-install
mkdir -p /tmp/precise-engine-install
tar -xzf precise-engine.tar.gz -C /tmp/precise-engine-install
BUNDLE_DIR=$(find /tmp/precise-engine-install -type d -name precise-engine | head -n1)
if [ -z "$BUNDLE_DIR" ]; then echo "ERROR: precise-engine bundle not found"; exit 1; fi
rm -rf "$VENV_BIN/precise-engine.dist"
cp -a "$BUNDLE_DIR" "$VENV_BIN/precise-engine.dist"

if [ ! -x "$VENV_BIN/precise-engine.dist/precise-engine" ]; then
  echo "ERROR: precise-engine binary missing from installed bundle"
  ls -la "$VENV_BIN/precise-engine.dist" || true
  exit 1
fi

# Create launcher script
cat > "$VENV_BIN/precise-engine" << 'LAUNCHER'
#!/usr/bin/env bash
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
BUNDLE="$DIR/precise-engine.dist"
export LD_LIBRARY_PATH="$BUNDLE:${LD_LIBRARY_PATH:-}"
export PYTHONPATH="$BUNDLE:/usr/lib/python3.11:${PYTHONPATH:-}"
exec "$BUNDLE/precise-engine" "$@"
LAUNCHER

chmod +x "$VENV_BIN/precise-engine"
echo "Installed bundle at $VENV_BIN/precise-engine.dist"
echo "Installed launcher at $VENV_BIN/precise-engine"
DEPLOY_CMD

echo "[2b/5] Applying platform wakeword defaults and ensuring resources..."
ssh "$SSH_TARGET" << 'WAKE_DEFAULTS_CMD'
set -euo pipefail
cd ~/openclaw-voice

ARCH=$(uname -m)
if [[ "$ARCH" == "armv7l" || "$ARCH" == "armv6l" ]]; then
  # ARMv7 default: Precise + hey-mycroft model file
  if [ ! -f .env ]; then
    touch .env
  fi

  python3 - << 'PY'
from pathlib import Path

env_path = Path('.env')
lines = env_path.read_text(encoding='utf-8').splitlines()
kv = {}
order = []
for line in lines:
    if '=' in line and not line.strip().startswith('#'):
        k, v = line.split('=', 1)
        k = k.strip()
        kv[k] = v
        if k not in order:
            order.append(k)

defaults = {
    'WAKE_WORD_ENABLED': 'true',
    'WAKE_WORD_ENGINE': 'precise',
    'WAKE_WORD_CONFIDENCE': '0.5',
    'OPENWAKEWORD_MODEL_PATH': 'docker/wakeword-models/hey-mycroft.pb',
}

for k, v in defaults.items():
    if k not in kv:
        order.append(k)
    kv[k] = v

out = []
for k in order:
    out.append(f"{k}={kv[k]}")
env_path.write_text("\n".join(out) + "\n", encoding='utf-8')
print('Updated .env wakeword defaults for ARMv7')
PY

  bash ./ensure_wakeword_resources.sh all || true
else
  # ARM64/Raspbian64 default: OpenWakeWord + hey_mycroft
  if [ ! -f .env ]; then
    touch .env
  fi
  python3 - << 'PY'
from pathlib import Path

env_path = Path('.env')
lines = env_path.read_text(encoding='utf-8').splitlines()
kv = {}
order = []
for line in lines:
    if '=' in line and not line.strip().startswith('#'):
        k, v = line.split('=', 1)
        k = k.strip()
        kv[k] = v
        if k not in order:
            order.append(k)

defaults = {
    'WAKE_WORD_ENABLED': 'true',
    'WAKE_WORD_ENGINE': 'openwakeword',
    'WAKE_WORD_CONFIDENCE': '0.95',
    'OPENWAKEWORD_MODEL_PATH': 'hey_mycroft',
}

for k, v in defaults.items():
    if k not in kv:
        order.append(k)
    kv[k] = v

out = []
for k in order:
    out.append(f"{k}={kv[k]}")
env_path.write_text("\n".join(out) + "\n", encoding='utf-8')
print('Updated .env wakeword defaults for ARM64')
PY

  bash ./ensure_wakeword_resources.sh all || true
fi
WAKE_DEFAULTS_CMD


echo "[3/5] Verifying executable..."
ssh "$SSH_TARGET" "set -e; cd ~/openclaw-voice; source .venv_orchestrator/bin/activate; precise-engine --version >/tmp/precise_version.txt 2>&1 || true; cat /tmp/precise_version.txt"

echo "[4/5] Running precise-engine runtime smoke test (imports + model load)..."
ssh "$SSH_TARGET" << 'SMOKE_CMD'
set -euo pipefail
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
