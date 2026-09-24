#!/usr/bin/env bash
#
# Install a Node version via fnm, migrate globals from the previous same-major
# version, and remove old same-major versions (one major = one installed version).
# Usage: install-node.sh <version>
#   version: any fnm-accepted version — e.g. 20, lts, v22.1.0 (default: lts)

set -euo pipefail

VERSION="${1:-lts}"

fail() {
    echo "Error: $*" >&2
    exit 1
}

fnm_versions() {
    awk '$2 ~ /^v[0-9]+\.[0-9]+\.[0-9]+([-+][0-9A-Za-z.-]+)*$/ {print $2}'
}

PRE_LIST=$(fnm list) || fail "could not list installed Node versions."
PRE_INSTALL=$(printf '%s\n' "$PRE_LIST" | fnm_versions)
PRE_DEFAULT=$(printf '%s\n' "$PRE_LIST" | awk \
    '$2 ~ /^v[0-9]/ && /(^|[[:space:],])default([[:space:],]|$)/ {print $2}')
RESOLVE_VERSION="$VERSION"

run_fnm_install() {
    # fnm prints a benign "already installed" notice on stderr when re-running
    # on an already-up-to-date version; pass through anything else.
    local out
    if ! out=$(fnm --corepack-enabled install "$@" 2>&1 >/dev/null); then
        printf '%s\n' "$out" >&2
        return 1
    fi
    printf '%s\n' "$out" | grep -v -E '^warning: Version already installed at' >&2 || true
}

if [[ "$VERSION" == "lts" ]]; then
    run_fnm_install --lts || fail "could not install the latest LTS."
    RESOLVE_VERSION="lts-latest"
else
    run_fnm_install "$VERSION" || fail "could not install $VERSION."
fi

# Let fnm resolve exact versions, partial versions, and aliases, even on reruns.
NEW_NODE=$(fnm exec --using="$RESOLVE_VERSION" node --version) \
    || fail "could not resolve $VERSION to a working Node installation."
[[ "$NEW_NODE" =~ ^v[0-9]+\.[0-9]+\.[0-9]+([-+][0-9A-Za-z.-]+)*$ ]] \
    || fail "invalid resolved Node version: $NEW_NODE"
POST_INSTALL=$(fnm list | fnm_versions) \
    || fail "could not list installed Node versions after installation."
printf '%s\n' "$POST_INSTALL" | grep -qFx "$NEW_NODE" \
    || fail "resolved version $NEW_NODE is not in fnm's installed versions."

MAJOR="${NEW_NODE#v}"
MAJOR="${MAJOR%%.*}"
OLD_VERSIONS=$(printf '%s\n' "$POST_INSTALL" | awk -v major="$MAJOR" -v target="$NEW_NODE" \
    'index($0, "v" major ".") == 1 && $0 != target')
MIGRATE_FROM=""

if [[ -n "$OLD_VERSIONS" ]]; then
    MIGRATE_FROM=$(printf '%s\n' "$OLD_VERSIONS" | sort -V | tail -1)
elif ! printf '%s\n' "$PRE_INSTALL" | grep -qFx "$NEW_NODE"; then
    # For a new major, keep the source installation and copy from the highest previous version.
    MIGRATE_FROM=$(printf '%s\n' "$PRE_INSTALL" | sort -V | tail -1)
fi

if [[ -n "$MIGRATE_FROM" ]]; then
    command -v jq >/dev/null || fail "jq is required to migrate global packages; old versions were kept."
    echo "Migrating globals from $MIGRATE_FROM to $NEW_NODE..."
    GLOBALS_JSON=$(fnm exec --using="$MIGRATE_FROM" npm list -g --depth 0 --json) \
        || fail "could not list globals for $MIGRATE_FROM; old versions were kept."
    GLOBALS=$(printf '%s\n' "$GLOBALS_JSON" | jq -ser '
        if length != 1 or (.[0] | type) != "object" then
            error("expected one npm listing")
        else .[0] end
        | if has("error") then error("npm reported an error") else . end
        | if has("dependencies") then .dependencies else {} end
        | if type != "object" then error("invalid npm dependencies") else . end
        | to_entries
        | map(select(.key != "npm")
            | if (.key | test("^(@[A-Za-z0-9][A-Za-z0-9._-]*/)?[A-Za-z0-9][A-Za-z0-9._-]*$"))
                and (.value.version | type) == "string"
                and (.value.version | test("^[0-9]+\\.[0-9]+\\.[0-9]+([-+][0-9A-Za-z.-]+)*$"))
              then "\(.key)@\(.value.version)"
              else error("invalid global package metadata for \(.key)") end)
        | join("\n")
    ') || fail "could not parse globals for $MIGRATE_FROM; old versions were kept."

    if [[ -n "$GLOBALS" ]]; then
        packages=()
        while IFS= read -r package; do
            packages+=("$package")
        done <<< "$GLOBALS"
        printf '  Reinstalling: %s\n' "${packages[*]}"
        fnm exec --using="$NEW_NODE" npm install -g -- "${packages[@]}" \
            || fail "global migration to $NEW_NODE failed; old versions were kept."
    fi
fi

# Keep an existing default usable when its installation is about to be removed.
# Default changes and cleanup happen only after successful migration.
if [[ "$VERSION" == "lts" ]] || { [[ -n "$PRE_DEFAULT" ]] && printf '%s\n' "$OLD_VERSIONS" | grep -qFx "$PRE_DEFAULT"; }; then
    fnm default "$NEW_NODE" || fail "could not set the default to $NEW_NODE; old versions were kept."
fi

while IFS= read -r old; do
    [[ -z "$old" ]] && continue
    echo "  Removing $old"
    fnm uninstall "$old" || fail "could not remove $old; cleanup stopped."
done <<< "$OLD_VERSIONS"

echo "Done: $NEW_NODE is ready."
