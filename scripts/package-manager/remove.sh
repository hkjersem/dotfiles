#!/usr/bin/env bash
# Remove packages with workspace-aware targeting for pnpm.
# Ambiguous pnpm workspace declarations error instead of guessing.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_detect-pm.sh
source "$SCRIPT_DIR/_detect-pm.sh"
# shellcheck source=_update-lib.sh
source "$SCRIPT_DIR/_update-lib.sh"

show_help() {
  cat <<'EOF'
Usage: pmr <package...> [flags]

Removes dependencies for the detected package manager.

pnpm behavior:
  - In a workspace member, removes from that member.
  - In the workspace root, re-targets removals when an existing dependency is
    found in exactly one workspace package.
  - Ambiguous declarations error instead of guessing.
  - Use --root to force removing from the workspace root package.json.
  - Use pnpm --filter <workspace> remove ... to target a specific workspace.
EOF
}

run_default_remove() {
  local pm="$1"
  shift
  case "$pm" in
    pnpm|bun) exec "$pm" remove "$@" ;;
    npm)      exec npm uninstall "$@" ;;
  esac
}

[[ $# -gt 0 && ( "$1" == "--help" || "$1" == "-h" ) ]] && { show_help; exit 0; }
pm="$(_detect_pm)"

if [[ "$pm" != "pnpm" ]]; then
  run_default_remove "$pm" "$@"
fi

CURRENT_DIR="$PWD"
CURRENT_PACKAGE_DIR="$(find_nearest_package_dir || true)"
_find_pnpm_workspace_root
cd "$ROOT"

force_root=false
explicit_target=false
unknown_option=false
declare -a args=() pkg_specs=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --root)
      force_root=true
      shift
      ;;
    --filter|--dir|-F|-C)
      explicit_target=true
      [[ $# -ge 2 ]] || { echo "Missing value for $1" >&2; exit 1; }
      args+=("$1" "$2")
      shift 2
      ;;
    --filter=*|--dir=*|--workspace-root|-w|--global|-g)
      explicit_target=true
      args+=("$1")
      shift
      ;;
    --help|-h)
      show_help
      exit 0
      ;;
    --*)
      case "$1" in
        --ignore-workspace-root-check|--recursive|--offline|--ignore-scripts|--no-optional)
          ;;
        *)
          unknown_option=true
          ;;
      esac
      args+=("$1")
      shift
      ;;
    -*)
      args+=("$1")
      shift
      ;;
    *)
      args+=("$1")
      pkg_specs+=("$1")
      shift
      ;;
  esac
done

if [[ ${#pkg_specs[@]} -eq 0 ]]; then
  echo "pmr requires at least one package" >&2
  exit 1
fi

if $force_root; then
  exec pnpm remove "${args[@]}"
fi

if $explicit_target; then
  cd "$CURRENT_DIR"
  exec pnpm remove "${args[@]}"
fi

if [[ -n "$CURRENT_PACKAGE_DIR" && "$CURRENT_PACKAGE_DIR" != "$ROOT" ]]; then
  exec pnpm --dir "$CURRENT_PACKAGE_DIR" remove "${args[@]}"
fi

if [[ ! -f "$ROOT/pnpm-workspace.yaml" ]]; then
  exec pnpm remove "${args[@]}"
fi

if $unknown_option; then
  echo "Cannot safely infer a workspace target with the provided options. Use --root or pnpm --filter <workspace> remove ..." >&2
  exit 1
fi

resolved_target=""

for spec in "${pkg_specs[@]}"; do
  pkg="$(pkg_name_from_spec "$spec" || true)"
  if [[ -z "$pkg" ]]; then
    echo "Cannot infer a workspace target for '$spec'. Use --root or pnpm --filter <workspace> remove ..." >&2
    exit 1
  fi

  target="$(resolve_existing_target "$pkg" find_all_declared_locations)"
  if [[ "$target" == "unknown" ]]; then
    echo "Could not infer where to remove $pkg from the workspace root. Use --root or pnpm --filter <workspace> remove ..." >&2
    exit 1
  fi
  if [[ "$target" == "ambiguous" || "$target" == "catalog" ]]; then
    echo "Found multiple remove targets for $pkg. Use --root or pnpm --filter <workspace> remove ..." >&2
    exit 1
  fi

  if [[ -z "$resolved_target" ]]; then
    resolved_target="$target"
  elif [[ "$resolved_target" != "$target" ]]; then
    echo "Packages resolve to different targets. Use --root or pnpm --filter <workspace> remove ..." >&2
    exit 1
  fi
done

case "$resolved_target" in
  root)
    exec pnpm remove "${args[@]}"
    ;;
  workspace:*)
    exec pnpm --filter "${resolved_target#workspace:}" remove "${args[@]}"
    ;;
  workspace-dir:*)
    exec pnpm --dir "${resolved_target#workspace-dir:}" remove "${args[@]}"
    ;;
  *)
    echo "Could not infer remove target. Use --root or pnpm --filter <workspace> remove ..." >&2
    exit 1
    ;;
esac
