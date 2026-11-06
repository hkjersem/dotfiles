#!/usr/bin/env bash
# Print the dependency names pmu or pmr can act on, one per line, for shell
# completion. Reuses the wrappers' root and workspace detection; reads
# package.json files with jq and never runs a package manager.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

show_help() {
  cat <<'EOF'
Usage: deps.sh <update|remove>

Print the dependency names a wrapper can act on, for shell completion.

Arguments:
  update      Every package in the workspace, as pmu sees it
  remove      The packages pmr would remove from here

Options:
  -h, --help  Show this help
EOF
}

case "${1:-}" in
  -h|--help) show_help; exit 0 ;;
  update|remove) scope=$1 ;;
  *) echo "Error: expected update or remove" >&2; exit 2 ;;
esac

# shellcheck source=_detect-pm.sh
source "$SCRIPT_DIR/_detect-pm.sh"
# shellcheck source=_update-lib.sh
source "$SCRIPT_DIR/_update-lib.sh"

pm=$(_detect_pm)
manifests=()

if [[ $scope == update ]]; then
  # Matches the root each pmu wrapper settles on.
  case "$pm" in
    pnpm) _find_root "pnpm-lock.yaml" ;;
    npm)  _find_root "package-lock.json" ;;
    bun)
      _find_root "bun.lockb"
      [[ -f "$ROOT/bun.lockb" || -f "$ROOT/bun.lock" ]] || _find_root "bun.lock"
      ;;
  esac
  while IFS= read -r manifest; do manifests+=("$manifest"); done < <(_collect_workspace_pkgjsons)
else
  # Matches remove.sh: npm and bun remove from the nearest package; pnpm from the
  # current member, or from any workspace package when run at the workspace root.
  package_dir=$(find_nearest_package_dir || true)
  [[ -n "$package_dir" ]] || exit 1
  if [[ "$pm" == pnpm ]]; then
    _find_pnpm_workspace_root
    if [[ "$package_dir" == "$ROOT" ]]; then
      while IFS= read -r manifest; do manifests+=("$manifest"); done < <(_collect_workspace_pkgjsons)
    else
      manifests=("$package_dir/package.json")
    fi
  else
    manifests=("$package_dir/package.json")
  fi
fi

names='[.dependencies, .devDependencies, .peerDependencies, .optionalDependencies]
  | map(select(type == "object") | keys[]) | .[]'
# One jq call is fast; if a manifest is malformed, read the others one by one.
if ! output=$(jq -r "$names" "${manifests[@]}" 2>/dev/null); then
  output=$(for manifest in "${manifests[@]}"; do jq -r "$names" "$manifest" 2>/dev/null || true; done)
fi
[[ -n "$output" ]] && printf '%s\n' "$output" | sort -u
