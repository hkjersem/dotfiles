#!/usr/bin/env bash
# Keep corepack's global pnpm and Yarn defaults current within their major line,
# and report new pnpm, Yarn, and Bun majors without installing them.
# Uses the npm release-age cooldown.
# Usage: corepack-defaults.sh

# shellcheck source=lib/corepack-defaults.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/corepack-defaults.sh" || exit 1

if ! command -v node >/dev/null; then
  echo "node: not available, skipping package-manager version checks"
  exit 0
fi

# One major behind is informational; further behind is a warning on stderr.
report_major() {
  local subject="$1" major="$2" gap="$3" action="$4"
  if [[ "$gap" -gt 1 ]]; then
    echo "Warning: $subject is $gap majors behind $major; $action" >&2
  else
    echo "$subject: $major is a new major; $action"
  fi
}

status=0
managers=$COREPACK_DEFAULT_MANAGERS
if ! command -v corepack >/dev/null; then
  echo "corepack: not available, skipping default updates"
  managers=""
fi
for pm in $managers; do
  if ! corepack_default_plan "$pm"; then
    echo "Error: could not read $pm release metadata from the npm registry." >&2
    status=1
    continue
  fi

  if [[ -n "$CP_TARGET" ]]; then
    if corepack install -g "$pm@$CP_TARGET"; then
      echo "corepack: $pm default ${CP_CURRENT:-unset} → $CP_TARGET"
    else
      echo "Error: corepack could not install $pm@$CP_TARGET." >&2
      status=1
      continue
    fi
  elif [[ -n "$CP_CURRENT_AGE_HOURS" ]]; then
    echo "Warning: $pm default $CP_CURRENT was published ${CP_CURRENT_AGE_HOURS}h ago, inside the release-age cooldown; it was left unchanged." >&2
  elif [[ -z "$CP_CURRENT" ]]; then
    echo "corepack: no $pm release is past the release-age cooldown yet"
  else
    echo "corepack: $pm default $CP_CURRENT is current for its major"
  fi

  if [[ -n "$CP_MAJOR" ]]; then
    report_major "corepack: $pm" "$CP_MAJOR" "$CP_MAJOR_GAP" \
      "to switch the default, run: corepack install -g $pm@$CP_MAJOR"
  fi
done

while IFS='|' read -r label executable; do
  [[ -z "$executable" ]] && continue
  if ! bun_major_plan "$executable"; then
    echo "Warning: could not check bun releases for $label." >&2
    continue
  fi
  if [[ -n "$BUN_MAJOR" ]]; then
    report_major "bun ($label, installed $BUN_CURRENT)" "$BUN_MAJOR" "$BUN_MAJOR_GAP" "upgrade it manually"
  fi
done < <(bun_installations)

exit "$status"
