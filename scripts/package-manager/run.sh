#!/usr/bin/env bash
# Detect bun, pnpm or npm and run the appropriate update script.
set -euo pipefail

SCRIPTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_detect-pm.sh
source "$SCRIPTS_DIR/_detect-pm.sh"

case "$(_detect_pm)" in
  pnpm) exec bash "$SCRIPTS_DIR/pnpm-update.sh" "$@" ;;
  npm)  exec bash "$SCRIPTS_DIR/npm-update.sh"  "$@" ;;
  bun)  echo "pmu does not support bun — use 'bun update' directly." >&2; exit 1 ;;
esac
