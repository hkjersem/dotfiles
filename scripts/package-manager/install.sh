#!/usr/bin/env bash
# Install packages with workspace-aware targeting for pnpm.
# Unknown packages at a workspace root fall back to root add.
# Existing catalog entries are only rewritten when the version is explicit.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_detect-pm.sh
source "$SCRIPT_DIR/_detect-pm.sh"
# shellcheck source=_update-lib.sh
source "$SCRIPT_DIR/_update-lib.sh"

show_help() {
  cat <<'EOF'
Usage: pmi [<package...>] [flags]

Installs dependencies for the detected package manager.

pnpm behavior:
  - In a workspace member, installs into that member.
  - In the workspace root, re-targets installs when an existing dependency is
    found in exactly one workspace package or in pnpm-workspace.yaml catalog.
  - Unknown packages at the workspace root fall back to a root install.
  - Catalog entries are only rewritten when the requested spec includes a version.
  - Use --root to force installing into the workspace root package.json.
  - Use pnpm --filter <workspace> add ... to target a specific workspace.
EOF
}

find_pnpm_root() {
  _find_root "pnpm-lock.yaml"
  cd "$ROOT"
}

spec_has_explicit_version() {
  local spec="$1"
  if [[ "$spec" == @* ]]; then
    [[ "$spec" =~ ^@[^/]+/[^@]+@.+$ ]]
  else
    [[ "$spec" =~ ^[^@/][^@]*@.+$ ]]
  fi
}

_find_location_extra() {
  local pkg="$1"
  pnpm_catalog_contains_package "$pkg" && { echo "catalog"; return; }
  echo ""
}

apply_catalog() {
  pnpm_apply_catalog_version "$1" "$2"
}

run_default_install() {
  local pm="$1"
  shift
  if [[ $# -eq 0 ]]; then
    exec "$pm" install
  fi
  case "$pm" in
    pnpm|bun) exec "$pm" add "$@" ;;
    npm)      exec npm install "$@" ;;
  esac
}

[[ $# -gt 0 && ( "$1" == "--help" || "$1" == "-h" ) ]] && { show_help; exit 0; }
pm="$(_detect_pm)"

if [[ "$pm" != "pnpm" ]]; then
  run_default_install "$pm" "$@"
fi

CURRENT_PACKAGE_DIR="$(find_nearest_package_dir || true)"
find_pnpm_root

if [[ $# -eq 0 ]]; then
  if [[ -n "$CURRENT_PACKAGE_DIR" && "$CURRENT_PACKAGE_DIR" != "$ROOT" ]]; then
    exec pnpm --dir "$CURRENT_PACKAGE_DIR" install
  fi
  exec pnpm install
fi

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
        --save-prod|--save-dev|--save-optional|--save-peer|--save-exact|--prefer-offline|--offline|--ignore-scripts|--no-optional)
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

if [[ -n "$CURRENT_PACKAGE_DIR" && "$CURRENT_PACKAGE_DIR" != "$ROOT" ]]; then
  exec pnpm --dir "$CURRENT_PACKAGE_DIR" add "${args[@]}"
fi

if $unknown_option; then
  echo "Cannot safely infer a workspace target with the provided options. Use --root or pnpm --filter <workspace> add ..." >&2
  exit 1
fi

if $force_root || $explicit_target || [[ ! -f "$ROOT/pnpm-workspace.yaml" ]]; then
  exec pnpm add "${args[@]}"
fi

[[ ${#pkg_specs[@]} -gt 0 ]] || exec pnpm add "${args[@]}"

declare -A target_seen=()
resolved_target=""

for spec in "${pkg_specs[@]}"; do
  pkg="$(pkg_name_from_spec "$spec" || true)"
  if [[ -z "$pkg" ]]; then
    echo "Cannot infer a workspace target for '$spec'. Use --root or pnpm --filter <workspace> add ..." >&2
    exit 1
  fi

  explicit_version=false
  spec_has_explicit_version "$spec" && explicit_version=true
  target="$(resolve_existing_target "$pkg")"
  if [[ "$target" == "unknown" || ( "$target" == "catalog" && "$explicit_version" != "true" ) ]]; then
    target="root"
  fi
  if [[ "$target" == "ambiguous" ]]; then
    echo "Found multiple install targets for $pkg. Use --root or pnpm --filter <workspace> add ..." >&2
    exit 1
  fi

  [[ -n "${target_seen[$target]:-}" ]] || target_seen["$target"]=1
  if [[ -z "$resolved_target" ]]; then
    resolved_target="$target"
  elif [[ "$resolved_target" != "$target" ]]; then
    echo "Packages resolve to different targets. Use --root or pnpm --filter <workspace> add ..." >&2
    exit 1
  fi
done

case "$resolved_target" in
  catalog)
    for spec in "${pkg_specs[@]}"; do
      pkg="$(pkg_name_from_spec "$spec")"
      ver="$(resolve_registry_version "$spec")"
      apply_catalog "$pkg" "$ver"
      echo "catalog: $pkg -> $ver"
    done
    exec pnpm install
    ;;
  root)
    exec pnpm add "${args[@]}"
    ;;
  workspace:*)
    exec pnpm --filter "${resolved_target#workspace:}" add "${args[@]}"
    ;;
  workspace-dir:*)
    exec pnpm --dir "${resolved_target#workspace-dir:}" add "${args[@]}"
    ;;
  *)
    echo "Could not infer install target. Use --root or pnpm --filter <workspace> add ..." >&2
    exit 1
    ;;
esac
