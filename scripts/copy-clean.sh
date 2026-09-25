#!/usr/bin/env bash
#
# Clone-copy a directory without known build artifacts.
# Usage: copy_clean [--ignore-git] [--keep-name] [--zip] [source] [destination]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/clean-targets.sh
source "$SCRIPT_DIR/lib/clean-targets.sh"

usage() {
    cat <<'EOF'
Usage: copy_clean [--ignore-git] [--keep-name] [source] <destination>
       copy_clean --zip [--ignore-git] [destination.zip]
       copy_clean --zip [--ignore-git] <source> <destination.zip>

Recursively copy a source directory to a new destination, excluding the build
artifacts removed by wipe_clean. When source is omitted, the current directory
is used. In ZIP mode, destination defaults inside the source and uses its name.
An existing ZIP destination directory receives <source-name>.zip. Files are
copied with macOS clone-on-write copies where supported.

Options:
  --ignore-git  Exclude all .git directories from the copy.
  --keep-name   Create the copy under destination using the source name.
  --zip         Create only a ZIP archive, preserving the source folder name.
  -h, --help    Show this help.
EOF
}

ignore_git=false
keep_name=false
zip_mode=false
paths=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --ignore-git) ignore_git=true ;;
        --keep-name) keep_name=true ;;
        --zip) zip_mode=true ;;
        -h|--help) usage; exit 0 ;;
        --)
            shift
            while [[ $# -gt 0 ]]; do
                paths+=("$1")
                shift
            done
            break
            ;;
        -*) echo "Error: unknown option: $1" >&2; usage >&2; exit 1 ;;
        *) paths+=("$1") ;;
    esac
    shift
done

if [[ "$zip_mode" == true ]]; then
    case "${#paths[@]}" in
        0) source="${PWD}"; destination="" ;;
        1) source="${PWD}"; destination="${paths[0]}" ;;
        2) source="${paths[0]}"; destination="${paths[1]}" ;;
        *) echo "Error: provide at most a source and ZIP destination." >&2
           usage >&2
           exit 1 ;;
    esac
else
    case "${#paths[@]}" in
        1) source="${PWD}"; destination="${paths[0]}" ;;
        2) source="${paths[0]}"; destination="${paths[1]}" ;;
        *) echo "Error: provide a destination and optionally a source." >&2
           usage >&2
           exit 1 ;;
    esac
fi

if [[ ! -d "$source" ]]; then
    echo "Error: source is not a directory: $source" >&2
    exit 1
fi

source_dir="$(cd "$source" && pwd -P)"
if [[ "$source_dir" == "/" ]]; then
    echo "Error: refusing to copy the filesystem root." >&2
    exit 1
fi

source_name="$(basename "$source_dir")"

if [[ "$zip_mode" == true && "$keep_name" == true ]]; then
    echo "Error: --keep-name cannot be used with --zip; ZIP archives always preserve the source name." >&2
    exit 1
fi

if [[ "$zip_mode" == true && -z "$destination" ]]; then
    destination="$source_dir/${source_name}.zip"
fi

if [[ "$zip_mode" == true && -d "$destination" ]]; then
    destination="$destination/${source_name}.zip"
fi

if [[ "$keep_name" == true ]]; then
    if [[ ! -d "$destination" ]]; then
        echo "Error: --keep-name destination must be an existing directory: $destination" >&2
        exit 1
    fi
    destination="$destination/$source_name"
fi

destination_parent="$(cd "$(dirname "$destination")" && pwd -P)" || {
    echo "Error: destination parent does not exist: $(dirname "$destination")" >&2
    exit 1
}
destination_name="$(basename "$destination")"
destination_path="$destination_parent/$destination_name"

if [[ "$destination_name" == "." || "$destination_name" == ".." ]]; then
    echo "Error: destination must name a new directory." >&2
    exit 1
fi

if [[ -e "$destination_path" || -L "$destination_path" ]]; then
    echo "Error: destination already exists: $destination_path" >&2
    exit 1
fi

if [[ "$zip_mode" == false ]]; then
    case "$destination_path/" in
        "$source_dir/"*)
            echo "Error: destination must not be inside the source directory." >&2
            exit 1
            ;;
    esac
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

staging_root=""
output_path="$destination_path"
if [[ "$zip_mode" == true ]]; then
    staging_root="$(mktemp -d "${TMPDIR:-/tmp}/copy-clean.XXXXXX")"
    destination_path="$staging_root/$source_name"
fi

mkdir "$destination_path"
cleanup_failed_copy() {
    if [[ "$zip_mode" == true ]]; then
        rm -f -- "$output_path"
        rm -rf -- "$staging_root"
    else
        rm -rf -- "$destination_path"
    fi
}
trap cleanup_failed_copy ERR

manifest="$(mktemp "${TMPDIR:-/tmp}/copy-clean-list.XXXXXX")"
trap 'rm -f -- "$manifest"' EXIT

find_args=(
    .
    \( -type d \( "${dir_expr[@]}" \) -prune \)
    -o \( -type f \( "${file_expr[@]}" \) \)
    -o -type s
)
if [[ "$ignore_git" == true ]]; then
    find_args+=(-o \( -type d -name .git -prune \))
fi
find_args+=(-o -print0)

cd "$source_dir"
if ! find "${find_args[@]}" > "$manifest"; then
    echo "Error: could not enumerate the source directory; copy aborted." >&2
    cleanup_failed_copy
    exit 1
fi

copied_items=0
show_progress=false
if [[ -t 1 ]]; then
    show_progress=true
    if [[ "$zip_mode" == true ]]; then
        printf 'Staging clean copy: 0 items'
    else
        printf 'Copying: 0 items'
    fi
fi

while IFS= read -r -d '' entry; do
    relative_path="${entry#./}"
    [[ -z "$relative_path" ]] && continue
    target_path="$destination_path/$relative_path"

    if [[ -d "$entry" && ! -L "$entry" ]]; then
        mkdir -p "$target_path"
    else
        mkdir -p "$(dirname "$target_path")"
        cp -cRp "$entry" "$target_path"
    fi

    if [[ "$show_progress" == true ]]; then
        copied_items=$((copied_items + 1))
        if [[ "$zip_mode" == true ]]; then
            printf '\rStaging clean copy: %s items' "$copied_items"
        else
            printf '\rCopying: %s items' "$copied_items"
        fi
    fi
done < "$manifest"

if [[ "$show_progress" == true ]]; then
    printf '\n'
fi

if [[ "$zip_mode" == true ]]; then
    if [[ "$show_progress" == true ]]; then
        printf 'Compressing archive...'
    fi
    ditto -c -k --norsrc --keepParent "$destination_path" "$output_path"
    if [[ "$show_progress" == true ]]; then
        printf ' done\n'
    fi
    rm -rf -- "$staging_root"
fi

trap - ERR
if [[ "$zip_mode" == true ]]; then
    echo "Created clean archive: $output_path"
else
    echo "Copied clean tree to: $destination_path"
fi
