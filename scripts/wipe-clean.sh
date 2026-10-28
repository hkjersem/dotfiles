#!/usr/bin/env bash
#
# Delete all known build artifacts and dependency folders from the current directory.
# Usage: wipe_clean

set -euo pipefail

show_help() {
    cat <<'EOF'
Usage: wipe_clean [--dry-run] [--sort path|size] [--older-than AGE]

Deletes known build artifacts and dependency folders below the current
directory, after previewing allocated sizes and asking for confirmation. Git directories
and submodules are left alone.

Options:
  -n, --dry-run  Show the cleanup plan without prompting or deleting
  --sort ORDER  Preview order: path (default) or size (largest first)
  --older-than AGE  Only targets with no modification within AGE (e.g. 24h, 7d, 2w), including descendants
  -h, --help  Show this help
EOF
}

for arg in "$@"; do
    case "$arg" in -h|--help) show_help; exit 0 ;; esac
done
sort_order=path
dry_run=false
older_than=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run|-n) dry_run=true; shift ;;
        --sort)
            if [[ $# -lt 2 || ( "$2" != path && "$2" != size ) ]]; then
                echo "Error: --sort requires path or size." >&2
                exit 2
            fi
            sort_order=$2
            shift 2 ;;
        --older-than)
            if [[ $# -lt 2 || ! "$2" =~ ^[0-9]+[mhdw]$ ]]; then
                echo "Error: --older-than requires an age such as 30m, 24h, 7d or 2w." >&2
                exit 2
            fi
            older_than=$2
            shift 2 ;;
        *) echo "Error: unknown argument: $1" >&2; exit 2 ;;
    esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/clean-targets.sh
source "$SCRIPT_DIR/lib/clean-targets.sh"
# shellcheck source=package-manager/_update-lib.sh
source "$SCRIPT_DIR/package-manager/_update-lib.sh"
$dry_run && out_dry_run

temporary="$(mktemp -d "${TMPDIR:-/tmp}/wipe-clean.XXXXXX")"
trap 'rm -f -- "$temporary/submodules" "$temporary/targets" "$temporary/selected"; rmdir "$temporary"' EXIT

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

preview_args=(--sort "$sort_order" --targets-output "$temporary/selected")
[[ -z "$older_than" ]] || preview_args+=(--older-than "$older_than")
if ! printf '%s\0' "${targets[@]}" | python3 -B "$SCRIPT_DIR/wipe-clean-preview.py" "${preview_args[@]}"; then
    echo "Error: could not prepare cleanup preview; nothing was deleted." >&2
    exit 1
fi
targets=()
while IFS= read -r -d '' target; do
    targets+=("$target")
done < "$temporary/selected"
[[ ${#targets[@]} -gt 0 ]] || exit 0
$dry_run && exit 0
echo
confirm_apply "Proceed?" false false || exit 0
if [[ -n "$older_than" ]]; then
    if ! printf '%s\0' "${targets[@]}" | python3 -B "$SCRIPT_DIR/wipe-clean-preview.py" \
        --older-than "$older_than" --check-age; then
        echo "Error: could not recheck cleanup ages; nothing was deleted." >&2
        exit 1
    fi
fi
if ! available_before=$(python3 -B "$SCRIPT_DIR/wipe-clean-preview.py" --available-bytes); then
    echo "Error: could not measure available space; nothing was deleted." >&2
    exit 1
fi

for target in "${targets[@]}"; do
    rm -rf -- "$target"
done
printf '  %s Cleanup complete.\n' "$OUT_OK"
python3 -B "$SCRIPT_DIR/wipe-clean-preview.py" --space-after "$available_before"
