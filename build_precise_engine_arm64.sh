#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"

TARGET_ARCH=arm64 "$ROOT_DIR/build_precise_engine_armv7.sh"
