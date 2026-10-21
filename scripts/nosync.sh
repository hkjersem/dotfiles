#!/usr/bin/env bash

attribute='com.apple.fileprovider.ignore#P'
mode="set"
recursive=false
status=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --inspect) mode="inspect" ;;
        -r|--recursive) recursive=true ;;
        --) shift; break ;;
        -*)
            printf 'Unknown option: %s\n' "$1" >&2
            exit 1
            ;;
        *) break ;;
    esac
    shift
done

if [[ $# -eq 0 ]]; then
    if [[ "$mode" == "inspect" && "$recursive" == false ]]; then
        set -- "."
    else
        set -- "node_modules" ".git"
    fi
fi

if [[ "$recursive" == true ]]; then
    names=()
    for arg in "$@"; do
        if [[ -z "$arg" || "$arg" == */* || "$arg" == "." || "$arg" == ".." ]]; then
            printf 'Recursive mode requires folder names, not paths: %s\n' "$arg" >&2
            exit 1
        fi
        # find -name treats glob characters specially; match literal folder names.
        name=${arg//\\/\\\\}
        name=${name//\*/\\*}
        name=${name//\?/\\?}
        name=${name//\[/\\[}
        [[ ${#names[@]} -eq 0 ]] || names+=(-o)
        names+=(-name "$name")
    done
    matches=$(mktemp) || exit 1
    trap 'rm -f "$matches"' EXIT
    if ! find . -type d ! -path . \( "${names[@]}" \) -prune -print0 > "$matches"; then
        printf 'Could not complete recursive folder search\n' >&2
        exit 1
    fi
    folders=()
    while IFS= read -r -d '' arg; do
        folders+=("$arg")
    done < "$matches"
    if [[ ${#folders[@]} -eq 0 ]]; then
        printf 'No matching folders found below the current directory\n'
        exit 0
    fi
    set -- "${folders[@]}"
fi

for arg in "$@"; do
    if [[ "$mode" == "inspect" ]]; then
        if [[ ! -e "$arg" ]]; then
            printf '%s: folder does not exist\n' "$arg"
            status=1
            continue
        fi
        if ! attrs=$(xattr "$arg" 2>/dev/null); then
            printf '%s: could not read extended attributes\n' "$arg" >&2
            status=1
            continue
        fi
        if ! printf '%s\n' "$attrs" | grep -Fqx "$attribute"; then
            printf '%s: %s is not set\n' "$arg" "$attribute"
            continue
        fi
        if value=$(xattr -p "$attribute" "$arg" 2>/dev/null); then
            printf '%s: %s=%s\n' "$arg" "$attribute" "$value"
        else
            printf '%s: could not read %s\n' "$arg" "$attribute" >&2
            status=1
        fi
    else
        if [[ "$recursive" == false ]] && ! mkdir -p "$arg"; then
            printf '%s: could not create folder\n' "$arg" >&2
            status=1
            continue
        fi
        if xattr -w "$attribute" 1 "$arg"; then
            printf '%s: set %s=1\n' "$arg" "$attribute"
        else
            printf '%s: could not set %s\n' "$arg" "$attribute" >&2
            status=1
        fi
    fi
done

exit "$status"
