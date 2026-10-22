#!/usr/bin/env bash
# Detect bun, pnpm or npm and run the appropriate update script.
set -euo pipefail

SCRIPTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_detect-pm.sh
source "$SCRIPTS_DIR/_detect-pm.sh"

# Help needs no project, so show it before detecting the package manager.
for arg in "$@"; do
  case "$arg" in
    --help|-h)
      # shellcheck source=_update-lib.sh
      source "$SCRIPTS_DIR/_update-lib.sh"
      _show_help
      exit 0
      ;;
  esac
done

case "$(_detect_pm)" in
  pnpm) exec bash "$SCRIPTS_DIR/pnpm-update.sh" "$@" ;;
  npm)  exec bash "$SCRIPTS_DIR/npm-update.sh"  "$@" ;;
  bun)  exec bash "$SCRIPTS_DIR/bun-update.sh" "$@" ;;
esac
