#!/usr/bin/env bash
#
# Delete all known build artifacts and dependency folders from the current directory.
# Usage: wipe_clean

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/clean-targets.sh
source "$SCRIPT_DIR/lib/clean-targets.sh"
# shellcheck source=package-manager/_update-lib.sh
source "$SCRIPT_DIR/package-manager/_update-lib.sh"

temporary="$(mktemp -d "${TMPDIR:-/tmp}/wipe-clean.XXXXXX")"
trap 'rm -f -- "$temporary/submodules" "$temporary/targets"; rmdir "$temporary"' EXIT

prune_expr=(-type d -name .git)
submodule_paths=()
if [[ -f .gitmodules ]]; then
    status=0
    git config --null --file .gitmodules --get-regexp 'submodule\..*\.path' \
        > "$temporary/submodules" || status=$?
    if [[ "$status" -ne 0 && "$status" -ne 1 ]]; then
        echo "Error: could not read submodule paths; cleanup aborted." >&2
        exit 1
    fi
    while IFS= read -r -d '' record; do
        submodule_path="${record#*$'\n'}"
        submodule_path="./${submodule_path#./}"
        submodule_path="${submodule_path%/}"
        submodule_paths+=("$submodule_path")
        escaped="${submodule_path//\\/\\\\}"
        escaped="${escaped//\*/\\*}"
        escaped="${escaped//\?/\\?}"
        escaped="${escaped//\[/\\[}"
        escaped="${escaped//\]/\\]}"
        prune_expr+=(-o -path "$escaped")
    done < "$temporary/submodules"
fi

dir_expr=()
for target in "${CLEAN_DIR_TARGETS[@]}"; do
    [[ ${#dir_expr[@]} -gt 0 ]] && dir_expr+=(-o)
    dir_expr+=(-name "$target")
done

file_expr=()
for target in "${CLEAN_FILE_TARGETS[@]}"; do
    [[ ${#file_expr[@]} -gt 0 ]] && file_expr+=(-o)
    file_expr+=(-name "$target")
done

if ! find . \( "${prune_expr[@]}" \) -prune -o \
    \( \( -type d \( "${dir_expr[@]}" \) -prune \) \
    -o \( -type f \( "${file_expr[@]}" \) \) \) -print0 > "$temporary/targets"; then
    echo "Error: could not enumerate cleanup targets; nothing was deleted." >&2
    exit 1
fi

targets=()
while IFS= read -r -d '' target; do
    contains_submodule=false
    if [[ ${#submodule_paths[@]} -gt 0 ]]; then
        for submodule_path in "${submodule_paths[@]}"; do
            if [[ "$submodule_path" == "$target/"* ]]; then
                contains_submodule=true
                break
            fi
        done
    fi
    [[ "$contains_submodule" == true ]] || targets+=("$target")
done < "$temporary/targets"

if [[ ${#targets[@]} -eq 0 ]]; then
    echo "Nothing to clean."
    exit 0
fi

echo "Will delete:"
printf '  %q\n' "${targets[@]}"
echo
confirm_apply "Proceed?" false false || exit 0

for target in "${targets[@]}"; do
    rm -rf -- "$target"
done
echo "Done."
