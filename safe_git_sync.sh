#!/usr/bin/env bash
set -euo pipefail

# Safe git sync helper for OpenClaw Voice
# - Default: non-destructive sync (fetch + pull --rebase)
# - Optional: --hard mode for reset + clean (still protects local env files)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

REMOTE="origin"
BRANCH="master"
MODE="safe"

usage() {
  cat <<'EOF'
Usage: ./safe_git_sync.sh [--hard] [--remote <name>] [--branch <name>]

Default behavior (safe):
  - git fetch
  - git pull --rebase
  - never runs destructive clean/reset

Options:
  --hard             Force hard reset + clean before pull (destructive)
  --remote <name>    Git remote to use (default: origin)
  --branch <name>    Git branch to sync (default: master)
  -h, --help         Show this help

Examples:
  ./safe_git_sync.sh
  ./safe_git_sync.sh --branch master
  ./safe_git_sync.sh --hard
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --hard)
      MODE="hard"
      shift
      ;;
    --remote)
      REMOTE="${2:-}"
      if [[ -z "$REMOTE" ]]; then
        echo "ERROR: --remote requires a value"
        exit 1
      fi
      shift 2
      ;;
    --branch)
      BRANCH="${2:-}"
      if [[ -z "$BRANCH" ]]; then
        echo "ERROR: --branch requires a value"
        exit 1
      fi
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "ERROR: Unknown argument: $1"
      usage
      exit 1
      ;;
  esac
done

if [[ ! -d .git ]]; then
  echo "ERROR: This script must be run from the git repo root."
  exit 1
fi

echo "==> Repo: $SCRIPT_DIR"
echo "==> Mode: $MODE"
echo "==> Target: ${REMOTE}/${BRANCH}"

git fetch "$REMOTE"

if [[ "$MODE" == "hard" ]]; then
  echo "⚠️  HARD MODE enabled: resetting local tracked changes and cleaning untracked files"
  # Preserve local env files and virtual environments even in hard mode.
  git reset --hard "${REMOTE}/${BRANCH}"
  git clean -fd \
    -e .env \
    -e .env.pi \
    -e .venv_orchestrator/ \
    -e .venv311/ \
    -e orchestrator_output.log
else
  echo "==> Safe sync: rebasing local commits and preserving local files"
  STASH_CREATED=0

  if ! git diff --quiet || ! git diff --cached --quiet || [[ -n "$(git ls-files --others --exclude-standard)" ]]; then
    STASH_NAME="safe-sync-$(date +%Y%m%d-%H%M%S)"
    echo "==> Local changes detected; stashing as: $STASH_NAME"
    git stash push -u -m "$STASH_NAME" >/dev/null
    STASH_CREATED=1
  fi

  git pull --rebase "$REMOTE" "$BRANCH"

  if [[ "$STASH_CREATED" -eq 1 ]]; then
    echo "==> Restoring stashed local changes"
    if ! git stash pop >/dev/null; then
      echo "⚠️  Stash pop had conflicts. Your changes are still available in git stash list."
      exit 1
    fi
  fi
fi

echo "✅ Git sync complete"
