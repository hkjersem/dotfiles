#!/usr/bin/env bash
# Link a local library into the current npm or pnpm project to try it out,
# and undo the link without leaving changes behind.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_detect-pm.sh
source "$SCRIPT_DIR/_detect-pm.sh"

show_help() {
  cat <<'EOF'
Usage: pml <library path>
       pml --undo <library path>

Links a local library into the current project instead of the installed
version, for trying out changes. --undo restores the installed version.
Supports npm and pnpm 11 or newer.

npm:  the project's files stay unchanged. --undo reinstalls from package.json
      and removes the global link npm created for the library.
pnpm: pnpm records the link in pnpm-lock.yaml, and in package.json when the
      library is not a dependency, plus an override in pnpm-workspace.yaml.
      --undo removes all of it, including on pnpm releases whose own unlink
      keeps the package.json entry.
EOF
}

undo=false
lib=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) show_help; exit 0 ;;
    --undo) undo=true ;;
    -*) echo "Error: unknown option $1" >&2; exit 2 ;;
    *)
      [[ -z "$lib" ]] || { echo "Error: give one library path" >&2; exit 2; }
      lib="$1"
      ;;
  esac
  shift
done
[[ -n "$lib" ]] || { show_help >&2; exit 2; }
[[ -f "$lib/package.json" ]] || { echo "Error: $lib has no package.json" >&2; exit 1; }
name=$(jq -r '.name // empty' "$lib/package.json")
[[ -n "$name" ]] || { echo "Error: $lib/package.json has no name" >&2; exit 1; }
# An absolute path also works from a subfolder: pnpm 12 resolves a relative
# one from the project root, and both read a bare word as a package name.
lib_dir=$(cd "$lib" && pwd -P)

pm=$(_detect_pm)
case "$pm" in
  npm)
    if [[ "$undo" == false ]]; then
      npm link "$lib_dir"
      exit 0
    fi
    # `npm unlink` is `npm uninstall`, which would drop a declared dependency.
    npm install
    global="$(npm root -g)/$name"
    if [[ -L "$global" && "$(cd "$global" && pwd -P)" == "$lib_dir" ]]; then
      npm rm -g "$name"
    fi
    ;;
  pnpm)
    version=$(pnpm --version)
    # pnpm 10's unlink keeps the link and the changed files.
    if [[ "${version%%.*}" -lt 11 ]]; then
      echo "Error: pml needs pnpm 11 or newer (found $version)" >&2
      exit 1
    fi
    if [[ "$undo" == true ]]; then
      pnpm unlink "$name"
      # Before 11.28 and 12.7, unlink keeps the link: dependency that link
      # added to package.json, and the empty pnpm-workspace.yaml it created;
      # later versions remove both themselves.
      minor=${version#*.}
      minor=${minor%%.*}
      major=${version%%.*}
      if [[ "$major" -eq 11 && "$minor" -lt 28 ]] || [[ "$major" -eq 12 && "$minor" -lt 7 ]]; then
        project=$PWD
        while [[ ! -f "$project/package.json" && "$project" != / ]]; do
          project=$(dirname "$project")
        done
        spec=$(jq -r --arg n "$name" '[.dependencies, .devDependencies, .optionalDependencies]
          | map(select(. != null) | .[$n] // empty) | first // empty' \
          "$project/package.json" 2>/dev/null || true)
        target="$project/${spec#link:}"
        [[ "${spec#link:}" == /* ]] && target=${spec#link:}
        if [[ "$spec" == link:* && -d "$target" && "$(cd "$target" && pwd -P)" == "$lib_dir" ]]; then
          (cd "$project" && pnpm remove "$name")
        fi
        workspace="$project/pnpm-workspace.yaml"
        if [[ -f "$workspace" && "$(tr -d '[:space:]' <"$workspace")" == "{}" ]]; then
          rm "$workspace"
        fi
      fi
    else
      pnpm link "$lib_dir"
    fi
    ;;
  *)
    echo "Error: pml supports npm and pnpm, not $pm" >&2
    exit 1
    ;;
esac
