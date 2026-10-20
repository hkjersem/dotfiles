#!/usr/bin/env bash
# Keep every installed fnm Node major current, not only the default:
# update each other major to its newest patch, then give each installation the
# newest cooldown-eligible npm that supports it and update its global packages.
# Run after install-node.sh has updated the default LTS.
# Usage: update-node-versions.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/corepack-defaults.sh
source "$SCRIPT_DIR/lib/corepack-defaults.sh" || exit 1

if ! command -v fnm >/dev/null || ! command -v node >/dev/null; then
  echo "fnm or node: not available, skipping Node version updates"
  exit 0
fi

status=0
current=$(fnm current 2>/dev/null)
current_major="${current#v}"
current_major="${current_major%%.*}"

majors=$(fnm_node_installations | while IFS= read -r inst; do
  label=$(fnm_installation_label "$inst")
  major="${label#v}"
  printf '%s\n' "${major%%.*}"
done | sort -un)
for major in $majors; do
  [[ "$major" == "$current_major" ]] && continue
  echo "Node $major: updating to its newest patch"
  bash "$SCRIPT_DIR/install-node.sh" "$major" || status=1
done

while IFS= read -r inst; do
  [[ -z "$inst" ]] && continue
  label=$(fnm_installation_label "$inst")
  # stdin carries the installation list, so keep npm from reading it.
  run() { PATH="$inst/bin:$PATH" "$@" </dev/null; }
  npm_version=$(run npm --version 2>/dev/null) || {
    echo "Warning: npm is not available for Node $label." >&2
    continue
  }

  if ! npm_node_plan "$label" "$npm_version"; then
    echo "Warning: could not check npm releases for Node $label; npm was left at $npm_version." >&2
  else
    if [[ -n "$NP_TARGET" ]]; then
      if run npm install -g --no-fund "npm@$NP_TARGET"; then
        echo "npm (Node $label): $npm_version → $NP_TARGET"
        npm_version="$NP_TARGET"
      else
        echo "Error: could not install npm@$NP_TARGET for Node $label." >&2
        status=1
      fi
    fi
    if [[ -z "$NP_COMPATIBLE" ]]; then
      echo "Warning: no cooldown-eligible npm 10 or newer supports Node $label; npm was left at $npm_version." >&2
    elif [[ "$NP_NEWEST" != "$NP_COMPATIBLE" ]]; then
      echo "Warning: npm $NP_NEWEST no longer supports Node $label; it stays on npm $NP_COMPATIBLE." >&2
    fi
  fi

  if ! version_at_least "$npm_version" "$NPM_COOLDOWN_SINCE"; then
    echo "Warning: npm $npm_version for Node $label ignores min-release-age; its global packages were not updated." >&2
    continue
  fi
  outdated=$(run npm outdated -g 2>/dev/null || true)
  [[ -n "$outdated" ]] && printf 'Outdated global npm packages (Node %s):\n%s\n' "$label" "$outdated"
  run npm update -g --no-fund || {
    echo "Error: could not update global packages for Node $label." >&2
    status=1
  }
done < <(fnm_node_installations)

exit "$status"
