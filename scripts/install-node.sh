#!/usr/bin/env bash
#
# Install a Node version via fnm, migrate globals from the previous same-major
# version, and remove old same-major versions (one major = one installed version).
# Usage: install-node.sh <version>
#   version: any fnm-accepted version — e.g. 20, lts, v22.1.0 (default: lts)

VERSION="${1:-lts}"

fnm_versions() {
    fnm list 2>/dev/null | awk '{print $2}' | grep '^v[0-9]'
}

# Snapshot installed versions before we do anything
PRE_INSTALL=$(fnm_versions)

fnm install "$VERSION" >/dev/null 2>&1
[[ "$VERSION" == "lts" ]] && fnm default lts-latest >/dev/null 2>&1

# Resolve the actual installed version by diffing before/after,
# or by finding the best match in the current list
POST_INSTALL=$(fnm_versions)
NEW_NODE=$(comm -13 <(echo "$PRE_INSTALL" | sort) <(echo "$POST_INSTALL" | sort) | head -1)

if [ -z "$NEW_NODE" ]; then
    # Nothing newly installed — find best match from existing versions
    if [[ "$VERSION" == "lts" ]]; then
        NEW_NODE=$(fnm list 2>/dev/null | grep 'default' | awk '{print $2}')
    else
        MAJOR_REQ=$(echo "$VERSION" | sed 's/v//' | cut -d. -f1)
        NEW_NODE=$(echo "$POST_INSTALL" | grep "^v${MAJOR_REQ}\." | sort -V | tail -1)
    fi
fi

if [ -z "$NEW_NODE" ]; then
    echo "Error: could not resolve installed version." >&2
    exit 1
fi

MAJOR=$(echo "$NEW_NODE" | sed 's/v//' | cut -d. -f1)

# If already had this version, just clean up old same-major versions
if echo "$PRE_INSTALL" | grep -qFx "$NEW_NODE"; then
    OLD_VERSIONS=$(fnm_versions | grep "^v${MAJOR}\." | grep -vFx "$NEW_NODE")
    if [ -z "$OLD_VERSIONS" ]; then
        echo "Already on $NEW_NODE — nothing to do."
        exit 0
    fi
    echo "Cleaning up old v${MAJOR}.x versions..."
    while IFS= read -r old; do
        [[ -z "$old" ]] && continue
        echo "  Removing $old"
        fnm uninstall "$old"
    done < <(echo "$OLD_VERSIONS")
    exit 0
fi

# Find other installed versions of this same major (to migrate from and clean up)
OLD_VERSIONS=$(fnm_versions | grep "^v${MAJOR}\." | grep -vFx "$NEW_NODE")

if [ -z "$OLD_VERSIONS" ]; then
    # No same-major version — fall back to current default node
    CURRENT_NODE=$(node --version 2>/dev/null)
    if [ -n "$CURRENT_NODE" ] && [ "$CURRENT_NODE" != "$NEW_NODE" ]; then
        echo "Installed $NEW_NODE — migrating globals from $CURRENT_NODE..."
        GLOBALS=$(npm list -g --depth 0 2>/dev/null \
            | grep -E '├──|└──' | sed 's/.*── //' | grep -v '^npm@')
        if [ -n "$GLOBALS" ]; then
            echo "  Reinstalling: $(echo "$GLOBALS" | tr '\n' ' ')"
            echo "$GLOBALS" | xargs fnm exec --using="$NEW_NODE" npm install -g
        fi
    else
        echo "Installed $NEW_NODE."
    fi
    exit 0
fi

# Upgrade: migrate from highest old same-major version, then remove old
MIGRATE_FROM=$(echo "$OLD_VERSIONS" | sort -V | tail -1)
echo "Upgraded $MIGRATE_FROM → $NEW_NODE"

GLOBALS=$(fnm exec --using="$MIGRATE_FROM" npm list -g --depth 0 2>/dev/null \
    | grep -E '├──|└──' | sed 's/.*── //' | grep -v '^npm@')

if [ -n "$GLOBALS" ]; then
    echo "  Reinstalling globals: $(echo "$GLOBALS" | tr '\n' ' ')"
    echo "$GLOBALS" | xargs fnm exec --using="$NEW_NODE" npm install -g
fi

while IFS= read -r old; do
    [[ -z "$old" ]] && continue
    echo "  Removing $old"
    fnm uninstall "$old"
done < <(echo "$OLD_VERSIONS")

echo "Done."
