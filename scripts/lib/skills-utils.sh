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

# Usage: skills_stale_plan CANDIDATE CANONICAL [SUBTREE...]
# SUBTREEs are skill directories relative to CANDIDATE (default: skills). The
# candidate is stale when everything in it is inside a subtree or is a real
# directory on the way to one, and the subtrees hold only managed links and
# empty directories. Returns 0 stale, 1 not stale, 2 inspection failed.
skills_stale_plan() {
    local candidate="$1" canonical="$2" scan path rel tree in_tree found=0 safe=0
    local -a trees
    shift 2
    if [[ $# -gt 0 ]]; then trees=("$@"); else trees=(skills); fi
    SKILLS_STALE_PATHS=()
    [[ -d "$candidate" && ! -L "$candidate" ]] || return 1
    for tree in "${trees[@]}"; do
        [[ -d "$candidate/$tree" || -L "$candidate/$tree" ]] && found=1
    done
    [[ "$found" -eq 1 ]] || return 1
    scan=$(mktemp) || return 2
    if ! find "$candidate" -depth -mindepth 1 -print0 > "$scan"; then
        rm -f "$scan"
        return 2
    fi
    while IFS= read -r -d '' path; do
        rel="${path#"$candidate"/}"
        in_tree=0
        for tree in "${trees[@]}"; do
            if [[ "$rel" == "$tree" || "$rel" == "$tree/"* ]]; then
                in_tree=1
            elif [[ "$in_tree" -eq 0 && "$tree" == "$rel/"* ]]; then
                in_tree=2
            fi
        done
        if [[ "$in_tree" -eq 0 ]]; then
            safe=1
        elif [[ "$in_tree" -eq 2 ]]; then
            [[ -d "$path" && ! -L "$path" ]] || safe=1
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

# External skill sources: each non-comment line is a directory written as
# ~/path. Sets SKILLS_SOURCE_LABELS (as written) and SKILLS_SOURCE_DIRS
# (expanded), including directories that do not exist on this machine.
skills_read_sources() {
    local file="$1" line rest re='^~/[A-Za-z0-9._/-]+$'
    SKILLS_SOURCE_LABELS=()
    SKILLS_SOURCE_DIRS=()
    [[ -f "$file" ]] || return 0
    while IFS= read -r line || [[ -n "$line" ]]; do
        line=$(skills_trim "$line")
        [[ -z "$line" || "$line" == \#* ]] && continue
        rest="${line#\~/}"
        rest="${rest%/}"
        if ! [[ "$line" =~ $re ]] || [[ -z "$rest" || "/$rest/" == *"/../"* || "/$rest/" == *"/./"* || "/$rest/" == *"//"* ]]; then
            echo "Invalid skill source (use ~/path): $line" >&2
            return 1
        fi
        SKILLS_SOURCE_LABELS+=("$line")
        SKILLS_SOURCE_DIRS+=("$HOME/$rest")
    done < "$file"
}

skills_manifest_has() {
    local manifest="$1" name="$2" line
    [[ -f "$manifest" ]] || return 1
    while IFS= read -r line || [[ -n "$line" ]]; do
        line=$(skills_trim "$line")
        if [[ "$line" == "# disabled:"* ]]; then
            line=$(skills_trim "${line#\# disabled:}")
        elif [[ "$line" == \#* ]]; then
            continue
        fi
        [[ "$line" == *"|"* ]] || continue
        [[ "$(skills_trim "${line#*|}")" == "$name" ]] && return 0
    done < "$manifest"
    return 1
}

# Collects skills from existing source directories after skills_read_sources.
# Sets SKILLS_EXTERNAL_NAMES/PATHS for linkable skills and
# SKILLS_EXTERNAL_PROBLEMS for skipped ones (invalid, duplicate or conflicting).
skills_collect_external() {
    local authored="$1" manifest="$2" dir skill name i j duplicate
    SKILLS_EXTERNAL_NAMES=()
    SKILLS_EXTERNAL_PATHS=()
    SKILLS_EXTERNAL_PROBLEMS=()
    for ((i = 0; i < ${#SKILLS_SOURCE_DIRS[@]}; i++)); do
        dir="${SKILLS_SOURCE_DIRS[$i]}"
        [[ -d "$dir" ]] || continue
        for skill in "$dir"/*; do
            [[ -d "$skill" && -f "$skill/SKILL.md" ]] || continue
            name=$(basename "$skill")
            if ! skills_valid_name "$name"; then
                SKILLS_EXTERNAL_PROBLEMS+=("invalid external skill name: $skill")
                continue
            fi
            if [[ -d "$authored/$name" ]]; then
                SKILLS_EXTERNAL_PROBLEMS+=("$name from ${SKILLS_SOURCE_LABELS[$i]} conflicts with a repo-authored skill; not linked")
                continue
            fi
            if skills_manifest_has "$manifest" "$name"; then
                SKILLS_EXTERNAL_PROBLEMS+=("$name from ${SKILLS_SOURCE_LABELS[$i]} conflicts with a manifest skill; not linked")
                continue
            fi
            duplicate=0
            for ((j = 0; j < ${#SKILLS_EXTERNAL_NAMES[@]}; j++)); do
                [[ "${SKILLS_EXTERNAL_NAMES[$j]}" == "$name" ]] && duplicate=1
            done
            if [[ "$duplicate" -eq 1 ]]; then
                SKILLS_EXTERNAL_PROBLEMS+=("$name is provided by more than one skill source; only the first is linked")
                continue
            fi
            SKILLS_EXTERNAL_NAMES+=("$name")
            SKILLS_EXTERNAL_PATHS+=("$skill")
        done
    done
}

# True for a canonical symlink into a declared source directory whose target
# no longer exists or no longer contains a SKILL.md.
skills_dangling_external() {
    local link="$1" target i
    [[ -L "$link" && ! -f "$link/SKILL.md" ]] || return 1
    target=$(readlink "$link") || return 1
    for ((i = 0; i < ${#SKILLS_SOURCE_DIRS[@]}; i++)); do
        [[ "$target" == "${SKILLS_SOURCE_DIRS[$i]}/"* ]] && return 0
    done
    return 1
}

# Usage: skills_dangling_authored LINK AUTHORED_DIR
# True for a link to a repo-authored skill that has since been removed or renamed.
skills_dangling_authored() {
    local link="$1" target
    [[ -L "$link" && ! -e "$link" ]] || return 1
    target=$(readlink "$link") || return 1
    [[ "$(dirname "$target")" == "$2" ]]
}

skills_is_external_link() {
    local link="$1" i
    [[ -L "$link" ]] || return 1
    for ((i = 0; i < ${#SKILLS_EXTERNAL_PATHS[@]}; i++)); do
        skills_same_path "$link" "${SKILLS_EXTERNAL_PATHS[$i]}" && return 0
    done
    return 1
}
