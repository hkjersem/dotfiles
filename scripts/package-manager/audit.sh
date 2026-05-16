#!/usr/bin/env bash
# Audit and fix vulnerabilities for the detected package manager.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_detect-pm.sh
source "$SCRIPT_DIR/_detect-pm.sh"

show_help() {
  cat <<'EOF'
Usage: pma [--deep]

Audit and fix vulnerabilities for the detected package manager.

Options:
  --deep  For pnpm only, refresh the lockfile deeply before the audit fix step
EOF
}

deep=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --deep) deep=true; shift ;;
    --help|-h) show_help; exit 0 ;;
    *) echo "Usage: pma [--deep]" >&2; exit 1 ;;
  esac
done

case "$(_detect_pm)" in
  pnpm)
    audit_fix=(--fix)
    pnpm_major="$(pnpm --version 2>/dev/null | cut -d. -f1)"
    if [[ "$pnpm_major" =~ ^[0-9]+$ && "$pnpm_major" -ge 11 ]]; then
      audit_fix=(--fix=update)
    fi

    if $deep; then
      if [[ -f pnpm-workspace.yaml ]]; then
        pnpm -r --include-workspace-root update --depth Infinity --lockfile-only 2>/dev/null || true
      else
        pnpm update --depth Infinity --lockfile-only 2>/dev/null || true
      fi
    fi

    pnpm audit "${audit_fix[@]}" && exec pnpm install --lockfile-only
    ;;
  npm)
    exec npm audit fix --package-lock-only --ignore-scripts
    ;;
  bun)
    exec bun audit
    ;;
  yarn)
    exec yarn npm audit --fix --mode update-lockfile
    ;;
esac
