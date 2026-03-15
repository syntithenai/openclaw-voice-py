#!/usr/bin/env bash
set -euo pipefail

PI_HOST="${PI_HOST:-10.1.1.210}"
PI_USER="${PI_USER:-stever}"

if [[ $# -eq 0 ]]; then
  echo "Usage: $0 <remote-command...>" >&2
  exit 2
fi

exec ssh \
  -o BatchMode=yes \
  -o ConnectTimeout=8 \
  -o ServerAliveInterval=5 \
  -o ServerAliveCountMax=2 \
  "${PI_USER}@${PI_HOST}" "$*"
