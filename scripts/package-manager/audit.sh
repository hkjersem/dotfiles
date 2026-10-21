#!/usr/bin/env bash
# Audit and fix vulnerabilities for the detected package manager.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_detect-pm.sh
source "$SCRIPT_DIR/_detect-pm.sh"

show_help() {
  cat <<'EOF'
Usage: pma [--dry-run | --deep]

Audit and fix vulnerabilities for the detected package manager.

Options:
  --dry-run  Report findings, and for npm the fixes it would make, without changing files
  --deep     For pnpm only, refresh the lockfile deeply before the audit fix step
EOF
}

deep=false
dry_run=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --deep) deep=true; shift ;;
    --dry-run) dry_run=true; shift ;;
    --help|-h) show_help; exit 0 ;;
    *) echo "Usage: pma [--dry-run | --deep]" >&2; exit 1 ;;
  esac
done

if $dry_run && $deep; then
  echo "pma: --deep rewrites the lockfile, so it cannot be combined with --dry-run." >&2
  exit 1
fi

pm="$(_detect_pm)"
if $deep && [[ "$pm" != "pnpm" ]]; then
  echo "Warning: --deep only applies to pnpm; ignoring for $pm." >&2
fi

case "$pm" in
  pnpm)
    if $dry_run; then
      exec pnpm audit
    fi
    audit_fix=(--fix)
    pnpm_major="$(pnpm --version 2>/dev/null | cut -d. -f1)"
    if [[ "$pnpm_major" =~ ^[0-9]+$ && "$pnpm_major" -ge 11 ]]; then
      audit_fix=(--fix=update)
    fi

    if $deep; then
      echo "Running deep pnpm lockfile refresh..."
      if [[ -f pnpm-workspace.yaml ]]; then
        pnpm -r --include-workspace-root update --depth Infinity --lockfile-only 2>/dev/null || true
      else
        pnpm update --depth Infinity --lockfile-only 2>/dev/null || true
      fi
    fi

    pnpm audit "${audit_fix[@]}" && exec pnpm install --lockfile-only
    ;;
  npm)
    if $dry_run; then
      exec npm audit fix --dry-run --package-lock-only --ignore-scripts
    fi
    exec npm audit fix --package-lock-only --ignore-scripts
    ;;
  bun)
    exec bun audit
    ;;
  *)
    echo "pma: unsupported package manager: $pm" >&2
    exit 1
    ;;
esac
