#!/usr/bin/env bash

set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

TARGET_PYTHON="${1:-}"
if [[ -z "${TARGET_PYTHON}" ]]; then
  if [[ -x "./.venv_orchestrator/bin/python" ]]; then
    TARGET_PYTHON="./.venv_orchestrator/bin/python"
  else
    TARGET_PYTHON="$(command -v python3 || true)"
  fi
fi

if [[ -z "${TARGET_PYTHON}" ]]; then
  echo -e "${RED}Error: could not determine Python executable${NC}" >&2
  exit 1
fi

if ! command -v setcap >/dev/null 2>&1; then
  echo -e "${YELLOW}setcap not found; install libcap2-bin to enable low-port binding${NC}"
  exit 0
fi

if ! command -v getcap >/dev/null 2>&1; then
  echo -e "${YELLOW}getcap not found; install libcap2-bin to verify capability${NC}"
  exit 0
fi

REAL_PYTHON="$(readlink -f "${TARGET_PYTHON}")"
if [[ ! -x "${REAL_PYTHON}" ]]; then
  echo -e "${RED}Error: python executable not found: ${REAL_PYTHON}${NC}" >&2
  exit 1
fi

if getcap "${REAL_PYTHON}" 2>/dev/null | grep -q 'cap_net_bind_service=ep'; then
  echo -e "${GREEN}✓ bind capability already present on ${REAL_PYTHON}${NC}"
  exit 0
fi

SETCAP_CMD=(setcap cap_net_bind_service=+ep "${REAL_PYTHON}")
if [[ "$(id -u)" -ne 0 ]]; then
  if command -v sudo >/dev/null 2>&1; then
    SETCAP_CMD=(sudo "${SETCAP_CMD[@]}")
  else
    echo -e "${YELLOW}Cannot set capability without root privileges (sudo missing).${NC}"
    exit 0
  fi
fi

if "${SETCAP_CMD[@]}"; then
  if getcap "${REAL_PYTHON}" 2>/dev/null | grep -q 'cap_net_bind_service=ep'; then
    echo -e "${GREEN}✓ granted low-port bind capability to ${REAL_PYTHON}${NC}"
  else
    echo -e "${YELLOW}Capability command ran, but capability was not visible on ${REAL_PYTHON}${NC}"
  fi
else
  echo -e "${YELLOW}Failed to set capability on ${REAL_PYTHON}; installer can continue using fallback ports${NC}"
fi
