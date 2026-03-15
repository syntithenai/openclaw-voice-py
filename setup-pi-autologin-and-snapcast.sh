#!/usr/bin/env bash
set -euo pipefail

PI_HOST="${1:-10.1.1.210}"
PI_USER="${2:-stever}"
PI_KEY="${3:-$HOME/.ssh/id_ed25519.pub}"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

if [[ ! -f "$PI_KEY" ]]; then
  echo "SSH public key not found: $PI_KEY" >&2
  exit 1
fi

echo "==> Waiting for SSH on ${PI_USER}@${PI_HOST}..."
for i in {1..30}; do
  if (echo >/dev/tcp/"$PI_HOST"/22) 2>/dev/null; then
    echo "SSH port is open."
    break
  fi
  sleep 2
  if [[ "$i" -eq 30 ]]; then
    echo "SSH port 22 is still closed on $PI_HOST" >&2
    exit 2
  fi
done

echo "==> Installing your SSH public key for autologin..."
ssh-copy-id -i "$PI_KEY" "${PI_USER}@${PI_HOST}"

echo "==> Verifying passwordless SSH..."
ssh -o BatchMode=yes "${PI_USER}@${PI_HOST}" 'echo "autologin-ok" && hostname'

echo "==> Running Snapcast install script on Pi..."
ssh "${PI_USER}@${PI_HOST}" 'bash -s' < "$SCRIPT_DIR/install-snapcast-pi.sh"

echo "==> Done. Autologin + Snapcast install complete on ${PI_HOST}."
