#!/usr/bin/env bash
#
# Recursively delete all files or folders with a given name below the current directory.

usage="Usage: wipe <name> [--file|-f|--dir|-d]"

show_help() {
    cat <<'EOF'
Usage: wipe <name> [--file|-f|--dir|-d]

Deletes every file or folder named <name> below the current directory,
skipping node_modules and .git. Names containing a dot are treated as
files and other names as folders, unless --file or --dir is given.

Options:
  -f, --file  Delete files with this name
  -d, --dir   Delete folders with this name
  -h, --help  Show this help
EOF
}

for arg in "$@"; do
    case "$arg" in
        -h|--help) show_help; exit 0 ;;
    esac
done

name="${1:-}" flag="${2:-}"
if [[ -z "$name" || "$name" == -* || $# -gt 2 ]]; then
    echo "$usage" >&2
    exit 1
fi

# Deletes without asking, so an unknown option must not fall back to a guessed type.
case "$flag" in
    --file|-f) type=f ;;
    --dir|-d)  type=d ;;
    "")        if [[ "$name" == *.* ]]; then type=f; else type=d; fi ;;
    *)         echo "$usage" >&2; exit 1 ;;
esac
find . -not -path "*/node_modules/*" -not -path "*/.git/*" -name "$name" -type "$type" -prune -exec rm -rf '{}' \;
