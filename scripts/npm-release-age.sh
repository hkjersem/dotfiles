#!/usr/bin/env bash
# Check days since release for an npm package.
# Usage:
#   npm-release-age <package>[@version]              # last 10 versions, sorted by semver
#   npm-release-age <package>[@version] --all        # all versions
#   npm-release-age <package>[@version] --date       # sort by release date instead of semver

set -e

SHOW_ALL=false
SORT_BY_DATE=false
SHOW_BETA=false
ARGS=()
for arg in "$@"; do
    case "$arg" in
        --all|-a)   SHOW_ALL=true ;;
        --date|-d)  SORT_BY_DATE=true ;;
        --beta|-b)  SHOW_BETA=true ;;
        *) ARGS+=("$arg") ;;
    esac
done

if [ "${#ARGS[@]}" -eq 0 ]; then
    echo "Usage: npm-release-age <package>[@version] [--all] [--date] [--beta]" >&2
    exit 1
fi

# Split "pkg@version" or "@scope/pkg@version" on the last '@'
ARG="${ARGS[0]}"
if [ "${#ARGS[@]}" -ge 2 ]; then
    PACKAGE="$ARG"
    VERSION="${ARGS[1]}"
elif echo "$ARG" | grep -qE '@[^/].*@'; then
    # scoped package with version: @scope/pkg@ver
    PACKAGE="${ARG%@*}"
    VERSION="${ARG##*@}"
elif echo "$ARG" | grep -qE '^[^@].*@'; then
    # unscoped package with version: pkg@ver
    PACKAGE="${ARG%@*}"
    VERSION="${ARG##*@}"
else
    PACKAGE="$ARG"
    VERSION=""
fi

NOW=$(date +%s)
APPLE_LOCALE=$(defaults read NSGlobalDomain AppleLocale 2>/dev/null || echo "")
HELPER="$(dirname "$0")/helpers/format-dates.py"

# Match only stable X.Y.Z versions by default; include pre-release with --beta
$SHOW_BETA \
    && VER_PAT='"[0-9]+\.[0-9]+\.[0-9]+[^"]*"' \
    || VER_PAT='"[0-9]+\.[0-9]+\.[0-9]+"'

# Prepend package name to ver field so the helper label is "pkg@ver"
format_rows() {
    local package="$1"
    awk -v pkg="$package" '{print pkg "@" $0}' \
        | python3 "$HELPER" "$APPLE_LOCALE" "$NOW"
}

maybe_tail() {
    $SHOW_ALL && cat || tail -10
}

# Sort "ver iso" lines — by semver (default) or by release date
sort_versions() {
    if $SORT_BY_DATE; then
        sort -k2         # iso date is second field, lexicographic sort works
    else
        sort -t'.' -k1,1n -k2,2n -k3,3n
    fi
}

if [ -n "$VERSION" ]; then
    # Fetch all version timestamps once
    TIME_JSON=$(npm view "$PACKAGE" time --json 2>/dev/null)

    # Check for exact match first
    EXACT_DATE=$(echo "$TIME_JSON" | grep "\"${VERSION}\"" | awk -F'"' '{print $4}')

    if [ -n "$EXACT_DATE" ]; then
        echo "$VERSION $EXACT_DATE" | format_rows "$PACKAGE"
    else
        # Try prefix match
        MATCHES=$(echo "$TIME_JSON" \
            | grep -E "\"${VERSION}[-.\"]" \
            | grep -E "$VER_PAT" \
            | awk -F'"' '{print $2, $4}')
        if [ -z "$MATCHES" ]; then
            echo "No versions matching '${VERSION}' found for ${PACKAGE}" >&2
            exit 1
        fi
        echo "$MATCHES" | sort_versions | maybe_tail | format_rows "$PACKAGE"
    fi
else
    # All versions
    npm view "$PACKAGE" time --json 2>/dev/null \
        | grep -E "$VER_PAT" \
        | awk -F'"' '{print $2, $4}' \
        | sort_versions \
        | maybe_tail \
        | format_rows "$PACKAGE"
fi
