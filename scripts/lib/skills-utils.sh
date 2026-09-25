#!/usr/bin/env bash

skills_valid_name() {
    [[ "$1" =~ ^[a-z0-9][a-z0-9._-]*$ ]]
}

skills_valid_source() {
    [[ "$1" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$ ]]
}

skills_trim() {
    local value="$1"
    value="${value#"${value%%[![:space:]]*}"}"
    value="${value%"${value##*[![:space:]]}"}"
    printf '%s' "$value"
}

skills_check_manifest() {
    local manifest="$1" authored="$2" line source name i
    local -a seen=()
    [[ -f "$manifest" ]] || return 0
    while IFS= read -r line || [[ -n "$line" ]]; do
        line=$(skills_trim "$line")
        [[ -z "$line" ]] && continue
        if [[ "$line" == "# disabled:"* ]]; then
            line=$(skills_trim "${line#\# disabled:}")
        elif [[ "$line" == \#* ]]; then
            continue
        fi
        source=$(skills_trim "${line%%|*}")
        name=$(skills_trim "${line#*|}")
        if [[ "$line" != *"|"* ]] || ! skills_valid_source "$source" || ! skills_valid_name "$name"; then
            echo "Invalid skill manifest entry: $line" >&2
            return 1
        fi
        if [[ -d "$authored/$name" ]]; then
            echo "Manifest conflicts with repo-authored skill: $name" >&2
            return 1
        fi
        for ((i = 0; i < ${#seen[@]}; i++)); do
            if [[ "${seen[$i]}" == "$name" ]]; then
                echo "Duplicate skill manifest name: $name" >&2
                return 1
            fi
        done
        seen+=("$name")
    done < "$manifest"
}

skills_same_path() {
    local first second
    first=$(realpath "$1" 2>/dev/null) || return 1
    second=$(realpath "$2" 2>/dev/null) || return 1
    [[ "$first" == "$second" ]]
}

skills_valid_lockfile() {
    jq -e '
        (.skills | type) == "object"
        and all(.skills | keys[]; test("^[a-z0-9][a-z0-9._-]*$"))
        and all(.skills[]; (.source | type) == "string")
    ' "$1" >/dev/null
}

skills_managed_link() {
    [[ -L "$1" ]] || return 1
    python3 -B - "$1" "$2" <<'PY'
import os
import re
import sys

link, canonical = sys.argv[1:]
target = os.path.abspath(os.path.join(os.path.dirname(link), os.readlink(link)))
managed = (os.path.dirname(target) == os.path.abspath(canonical)
           and re.fullmatch(r"[a-z0-9][a-z0-9._-]*", os.path.basename(target)))
sys.exit(0 if managed else 1)
PY
}

skills_stale_plan() {
    local candidate="$1" canonical="$2" scan path safe=0
    SKILLS_STALE_PATHS=()
    [[ -d "$candidate" && ! -L "$candidate" ]] || return 1
    [[ -d "$candidate/skills" || -L "$candidate/skills" ]] || return 1
    scan=$(mktemp) || return 2
    if ! find "$candidate" -depth -mindepth 1 -print0 > "$scan"; then
        rm -f "$scan"
        return 2
    fi
    while IFS= read -r -d '' path; do
        if [[ "$path" != "$candidate/skills" && "$path" != "$candidate/skills/"* ]]; then
            safe=1
        elif [[ -L "$path" ]]; then
            if [[ "$path" == "$candidate/skills" ]] && skills_same_path "$path" "$canonical"; then
                :
            elif ! skills_managed_link "$path" "$canonical"; then
                safe=1
            fi
        elif [[ ! -d "$path" ]]; then
            safe=1
        fi
        SKILLS_STALE_PATHS+=("$path")
    done < "$scan"
    rm -f "$scan"
    return "$safe"
}

skills_clean_stale() {
    local candidate="$1" canonical="$2" path i
    for ((i = 0; i < ${#SKILLS_STALE_PATHS[@]}; i++)); do
        path="${SKILLS_STALE_PATHS[$i]}"
        if [[ -L "$path" ]]; then
            if [[ "$path" == "$candidate/skills" ]] && skills_same_path "$path" "$canonical"; then
                :
            else
                skills_managed_link "$path" "$canonical" || return 1
            fi
            rm "$path" || return 1
        elif [[ -d "$path" ]]; then
            rmdir "$path" || return 1
        else
            echo "Cleanup path changed; left unchanged: $path" >&2
            return 1
        fi
    done
    rmdir "$candidate"
}
