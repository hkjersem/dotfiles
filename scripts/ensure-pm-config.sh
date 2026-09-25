#!/usr/bin/env bash
# Ensures package manager release-age cooldowns are configured globally.
# Sets min-release-age in ~/.npmrc, pnpm global minimumReleaseAge (if pnpm installed),
# and minimumReleaseAge in ~/.bunfig.toml [install] (if bun installed).
# Updates values in place rather than overwriting other config.
set -e

# ── npm ───────────────────────────────────────────────────────────────────────
NPMRC="$HOME/.npmrc"
KEY="min-release-age"
VALUE="3"
LINE="$KEY=$VALUE"

if grep -qxF "$LINE" "$NPMRC" 2>/dev/null; then
  echo "npmrc: $LINE already present"
elif grep -qE "^$KEY=" "$NPMRC" 2>/dev/null; then
  # Key exists with a different value — update it in place
  sed -i '' "s/^$KEY=.*/$LINE/" "$NPMRC"
  echo "npmrc: updated $LINE"
else
  echo "$LINE" >> "$NPMRC"
  echo "npmrc: added $LINE"
fi

# ── pnpm ──────────────────────────────────────────────────────────────────────
if command -v pnpm &>/dev/null; then
  PNPM_KEY="minimumReleaseAge"
  PNPM_VALUE="4320"
  CURRENT=$(pnpm config get "$PNPM_KEY" --global)
  if [ "$CURRENT" = "$PNPM_VALUE" ]; then
    echo "pnpm: $PNPM_KEY=$PNPM_VALUE already set"
  else
    pnpm config set "$PNPM_KEY" "$PNPM_VALUE" --global
    echo "pnpm: set $PNPM_KEY=$PNPM_VALUE"
  fi
fi

# ── bun ───────────────────────────────────────────────────────────────────────
if command -v bun &>/dev/null; then
  # shellcheck source=lib/pm-utils.sh
  source "$(dirname "${BASH_SOURCE[0]}")/lib/pm-utils.sh"
  BUN_BUNFIG="$HOME/.bunfig.toml"
  BUN_KEY="minimumReleaseAge"
  BUN_VALUE="259200"  # 3 days in seconds

  _current_bun=$(read_bunfig_mra "$BUN_BUNFIG" 2>/dev/null || true)

  if [[ "$_current_bun" == "$BUN_VALUE" ]]; then
    echo "bunfig: $BUN_KEY=$BUN_VALUE already set"
  elif [[ -n "$_current_bun" ]]; then
    # Update existing key in [install] section.
    # Line-by-line awk with section tracking avoids breakage from TOML arrays
    # containing "[", and ^[[:space:]]*minimumReleaseAge won't match commented lines.
    awk '
      /^\[/ { in_install = /^\[install\]/ }
      in_install && /^[[:space:]]*minimumReleaseAge[[:space:]]*=/ {
        print "'"$BUN_KEY"' = '"$BUN_VALUE"'"; next
      }
      1
    ' "$BUN_BUNFIG" > "$BUN_BUNFIG.tmp"
    mv "$BUN_BUNFIG.tmp" "$BUN_BUNFIG"
    echo "bunfig: updated $BUN_KEY=$BUN_VALUE"
  elif [[ ! -f "$BUN_BUNFIG" ]]; then
    printf '[install]\n%s = %s\n' "$BUN_KEY" "$BUN_VALUE" > "$BUN_BUNFIG"
    echo "bunfig: created with $BUN_KEY=$BUN_VALUE"
  elif grep -q '^\[install\]' "$BUN_BUNFIG"; then
    # Insert key on the line after [install].
    # awk handles EOF-without-trailing-newline correctly.
    awk '
      /^\[install\]/ { print; print "'"$BUN_KEY"' = '"$BUN_VALUE"'"; next }
      1
    ' "$BUN_BUNFIG" > "$BUN_BUNFIG.tmp"
    mv "$BUN_BUNFIG.tmp" "$BUN_BUNFIG"
    echo "bunfig: added $BUN_KEY=$BUN_VALUE to [install]"
  else
    # Append new [install] section
    printf '\n[install]\n%s = %s\n' "$BUN_KEY" "$BUN_VALUE" >> "$BUN_BUNFIG"
    echo "bunfig: added [install] with $BUN_KEY=$BUN_VALUE"
  fi
  unset _current_bun
fi
