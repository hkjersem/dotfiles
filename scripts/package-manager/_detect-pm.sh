#!/usr/bin/env bash
# Sourced by run.sh, or executed directly to print the detected package manager.
#
# Detects the package manager (bun, pnpm or npm) for the current project by walking
# up from $PWD. Checks packageManager field in package.json first, then lockfile.

_detect_pm() {
  local d="$PWD" pm=""
  while [[ "$d" != "/" ]]; do
    if [[ -f "$d/package.json" ]]; then
      pm=$(jq -r '.packageManager // empty' "$d/package.json" 2>/dev/null | cut -d@ -f1 || true)
      [[ -n "$pm" ]] && break
    fi
    [[ -f "$d/bun.lock"          ]] && { pm="bun";  break; }
    [[ -f "$d/bun.lockb"         ]] && { pm="bun";  break; }
    [[ -f "$d/pnpm-lock.yaml"    ]] && { pm="pnpm"; break; }
    [[ -f "$d/package-lock.json" ]] && { pm="npm";  break; }
    d="$(dirname "$d")"
  done
  [[ -n "$pm" ]] || { echo "_detect_pm: no bun, pnpm or npm project found" >&2; return 1; }
  echo "$pm"
}

# When executed directly, print the result
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then _detect_pm; fi
