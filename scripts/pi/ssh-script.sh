#!/usr/bin/env bash
set -euo pipefail

PI_HOST="${PI_HOST:-10.1.1.210}"
PI_USER="${PI_USER:-stever}"

exec ssh \
  -T \
  -o BatchMode=yes \
  -o ConnectTimeout=8 \
  -o ServerAliveInterval=5 \
  -o ServerAliveCountMax=2 \
  "${PI_USER}@${PI_HOST}" 'bash -seuo pipefail'
