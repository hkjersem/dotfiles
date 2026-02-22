#!/usr/bin/env bash
#
# Diff global npm packages between two node versions.
# Usage: npm-globals-diff.sh <version_a> [version_b]
#   Versions can be a major (22), full version (v22.22.0), or omitted for current.
#   e.g. npm-globals-diff.sh 22, npm-globals-diff.sh 22 24

fnm_versions() {
    fnm list 2>/dev/null | awk '{print $2}' | grep '^v[0-9]'
}

resolve_version() {
    local input="$1"
    if [[ "$input" == "lts" ]]; then
        local match
        match=$(fnm list 2>/dev/null | grep 'lts-latest\|default' | awk '{print $2}' | grep '^v[0-9]' | head -1)
        if [[ -z "$match" ]]; then
            echo "Error: could not resolve lts version" >&2
            return 1
        fi
        echo "$match"
    elif [[ "$input" =~ ^v?[0-9]+$ ]]; then
        local major="${input#v}"
        local match
        match=$(fnm_versions | grep "^v${major}\." | sort -V | tail -1)
        if [[ -z "$match" ]]; then
            echo "Error: no installed version found for major $major" >&2
            return 1
        fi
        echo "$match"
    else
        echo "$input"
    fi
}

if [[ -z "$1" ]]; then
    echo "Usage: npm-globals-diff.sh <version_a> [version_b]"
    echo "Installed versions:"
    fnm_versions | sed 's/^/  /'
    exit 1
fi

A=$(resolve_version "$1") || exit 1
B=$(resolve_version "${2:-$(node --version 2>/dev/null)}") || exit 1

if [[ -z "$B" ]]; then
    echo "Error: no current node version found. Provide a second argument." >&2
    exit 1
fi

if [[ "$A" == "$B" ]]; then
    echo "Warning: both versions resolve to $A — nothing to compare."
    exit 0
fi

# Always put current node first
CURRENT=$(node --version 2>/dev/null)
if [[ "$B" == "$CURRENT" ]]; then
    tmp="$A"; A="$B"; B="$tmp"
fi

globals_for() {
    fnm exec --using="$1" npm list -g --depth 0 2>/dev/null \
        | grep -E '├──|└──' | sed 's/.*── //' | sort
}

BOLD=$'\033[1m'
DIM=$'\033[2m'
YELLOW=$'\033[33m'
RED=$'\033[31m'
GREEN=$'\033[32m'
RESET=$'\033[0m'

ga=$(globals_for "$A") || { echo "Error: could not list globals for $A" >&2; exit 1; }
gb=$(globals_for "$B") || { echo "Error: could not list globals for $B" >&2; exit 1; }

# Extract package names only (strip @version)
names_a=$(echo "$ga" | sed 's/@[^@]*$//')
names_b=$(echo "$gb" | sed 's/@[^@]*$//')

only_a_names=$(comm -23 <(echo "$names_a") <(echo "$names_b"))
only_b_names=$(comm -13 <(echo "$names_a") <(echo "$names_b"))
common_names=$(comm -12 <(echo "$names_a") <(echo "$names_b"))

# Version mismatches: same package, different version
mismatches=()
while IFS= read -r pkg; do
    [[ -z "$pkg" ]] && continue
    ver_a=$(echo "$ga" | grep "^${pkg}@" | sed 's/.*@//')
    ver_b=$(echo "$gb" | grep "^${pkg}@" | sed 's/.*@//')
    [[ "$ver_a" != "$ver_b" ]] && mismatches+=("$pkg|$ver_a|$ver_b")
done < <(echo "$common_names")

# Packages only in one version
only_a=$(while IFS= read -r pkg; do [[ -z "$pkg" ]] && continue; echo "$ga" | grep "^${pkg}@"; done < <(echo "$only_a_names"))
only_b=$(while IFS= read -r pkg; do [[ -z "$pkg" ]] && continue; echo "$gb" | grep "^${pkg}@"; done < <(echo "$only_b_names"))

label_a="$A"; label_b="$B"
[[ "$A" == "$CURRENT" ]] && label_a="${A} (current)"
[[ "$B" == "$CURRENT" ]] && label_b="${B} (current)"

printf "\n${BOLD}Comparing ${label_a} → ${label_b}${RESET}\n\n"

has_diff=0

if [[ ${#mismatches[@]} -gt 0 ]]; then
    has_diff=1
    printf "${BOLD}%-24s  %-22s  %-22s${RESET}\n" "Package" "$label_a" "$label_b"
    printf "${DIM}%-24s  %-22s  %-22s${RESET}\n" "────────────────────────" "──────────────────────" "──────────────────────"
    for entry in "${mismatches[@]}"; do
        IFS='|' read -r pkg ver_a ver_b <<< "$entry"
        newer=$(printf '%s\n%s\n' "$ver_a" "$ver_b" | sort -V | tail -1)
        major_a=$(echo "$ver_a" | cut -d. -f1)
        major_b=$(echo "$ver_b" | cut -d. -f1)
        # Package name: yellow/red if current is outdated, green if current is ahead
        current_outdated=0
        if   [[ "$A" == "$CURRENT" && "$newer" == "$ver_b" ]]; then current_outdated=1
        elif [[ "$B" == "$CURRENT" && "$newer" == "$ver_a" ]]; then current_outdated=1
        elif [[ "$A" != "$CURRENT" && "$B" != "$CURRENT" && "$newer" == "$ver_b" ]]; then current_outdated=1
        fi
        if [[ $current_outdated -eq 1 ]]; then
            [[ "$major_a" != "$major_b" ]] && pkg_color="$RED" || pkg_color="$YELLOW"
        else
            pkg_color="$GREEN"
        fi
        if [[ "$newer" == "$ver_b" ]]; then
            printf "${pkg_color}%-24s${RESET}  ${DIM}%-22s${RESET}  ${GREEN}${BOLD}%-22s${RESET}\n" "$pkg" "$ver_a" "$ver_b"
        else
            printf "${pkg_color}%-24s${RESET}  ${GREEN}${BOLD}%-22s${RESET}  ${DIM}%-22s${RESET}\n" "$pkg" "$ver_a" "$ver_b"
        fi
    done
    echo ""
fi

if [[ -n "$only_a" ]]; then
    has_diff=1
    printf "${BOLD}Only in ${label_a}:${RESET}\n"
    echo "$only_a" | while IFS= read -r line; do printf "  %s\n" "$line"; done
    echo ""
fi

if [[ -n "$only_b" ]]; then
    has_diff=1
    printf "${BOLD}Only in ${label_b}:${RESET}\n"
    echo "$only_b" | while IFS= read -r line; do printf "  %s\n" "$line"; done
    echo ""
fi

[[ $has_diff -eq 0 ]] && printf "${GREEN}✓${RESET} No differences — same globals in both versions.\n"
exit 0
