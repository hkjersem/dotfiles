#!/usr/bin/env bash
# Clean package-manager metadata with safe, low-risk rewrites.
# Named pnpm catalogs are detected and skipped conservatively.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_detect-pm.sh
source "$SCRIPT_DIR/_detect-pm.sh"
# shellcheck source=_update-lib.sh
source "$SCRIPT_DIR/_update-lib.sh"

show_help() {
  cat <<'EOF'
Usage: pmc [--dry-run] [--yes]

Cleans package-manager metadata for the detected package manager.

Actions:
  - pnpm: remove unused top-level catalog entries from pnpm-workspace.yaml
  - pnpm: suggest packages that could be centralized in catalog
  - pnpm: skip catalog cleanup when named pnpm catalogs are present
  - all package managers: remove empty top-level manifest sections from package.json files
EOF
}

AUTO_YES=false
DRY_RUN=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run|-n) DRY_RUN=true; shift ;;
    --yes|-y)     AUTO_YES=true; shift ;;
    --help|-h)    show_help; exit 0 ;;
    *)            echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

pm="$(_detect_pm)"
case "$pm" in
  pnpm) _find_root "pnpm-lock.yaml" ;;
  npm)  _find_root "package-lock.json" ;;
  bun)
    _find_root "bun.lockb"
    if [[ ! -f "$ROOT/bun.lockb" && ! -f "$ROOT/bun.lock" ]]; then
      _find_root "bun.lock"
    fi
    ;;
esac
cd "$ROOT"

declare -a pkgjsons=()
while IFS= read -r pkgjson; do pkgjsons+=("$pkgjson"); done < <(_collect_workspace_pkgjsons)

declare -a CLEAN_FILES=() CLEAN_SECTIONS=()
declare -a UNUSED_CATALOG_PKGS=()
declare -a SUGGEST_PKG=() SUGGEST_KIND=() SUGGEST_VERSIONS=() SUGGEST_LOCS=()
named_catalogs_present=false

format_file_label() {
  local file="$1"
  if [[ "$file" == "$ROOT/package.json" ]]; then
    echo "package.json"
  else
    echo "${file#$ROOT/}"
  fi
}

format_sections() {
  mapfile -t _sections < <(printf '%s\n' "$1")
  join_lines ", " "${_sections[@]}"
}

normalize_declared_version() {
  local raw="$1"
  case "$raw" in
    ""|catalog:*|workspace:*|file:*|link:*|git:*|git+*|http:*|https:*|github:*|gitlab:*|bitbucket:*|jsr:*|npm:*)
      return 1
      ;;
  esac

  local norm="$raw"
  norm="${norm#v}"
  norm="${norm#\^}"
  norm="${norm#\~}"
  norm="${norm#>=}"
  norm="${norm#>}"
  norm="${norm#<=}"
  norm="${norm#<}"
  norm="${norm#=}"

  [[ "$norm" =~ ^[0-9]+\.[0-9]+\.[0-9]+([-.][0-9A-Za-z.-]+)?$ ]] || return 1
  echo "$norm"
}

for pkgjson in "${pkgjsons[@]}"; do
  sections=$(jq -r '
    . as $root
    | ["dependencies","devDependencies","peerDependencies","optionalDependencies","overrides","resolutions"][]
    | select(($root[.]? | type) == "object" and ($root[.] | length) == 0)
  ' "$pkgjson" 2>/dev/null || true)
  [[ -z "$sections" ]] && continue
  rewriteable_sections=""
  while IFS= read -r section; do
    [[ -z "$section" ]] && continue
    if grep -Eq "^[[:space:]]*\"${section}\":[[:space:]]*\\{\\}[[:space:]]*,?[[:space:]]*$" "$pkgjson"; then
      rewriteable_sections+="${rewriteable_sections:+$'\n'}${section}"
    fi
  done <<< "$sections"
  [[ -z "$rewriteable_sections" ]] && continue
  CLEAN_FILES+=("$pkgjson")
  CLEAN_SECTIONS+=("$rewriteable_sections")
done

if [[ "$pm" == "pnpm" && -f "$ROOT/pnpm-workspace.yaml" ]]; then
  if pnpm_has_named_catalogs; then
    named_catalogs_present=true
  else
    while IFS= read -r pkg; do
      [[ -z "$pkg" ]] && continue
      is_used=false
      for pkgjson in "${pkgjsons[@]}"; do
        if jq -e --arg pkg "$pkg" '
          any([
            (.dependencies[$pkg] // ""),
            (.devDependencies[$pkg] // ""),
            (.peerDependencies[$pkg] // ""),
            (.optionalDependencies[$pkg] // "")
          ][]; . == "catalog:")
        ' "$pkgjson" >/dev/null 2>&1; then
          is_used=true
          break
        fi
      done
      $is_used || UNUSED_CATALOG_PKGS+=("$pkg")
    done < <(pnpm_catalog_keys)

    suggest_file=$(mktemp)
    trap 'rm -f "$suggest_file"' EXIT
    for pkgjson in "${pkgjsons[@]}"; do
      while IFS=$'\t' read -r pkg deptype raw; do
        [[ -z "$pkg" || -z "$raw" ]] && continue
        pnpm_catalog_contains_package "$pkg" && continue
        norm="$(normalize_declared_version "$raw" || true)"
        [[ -z "$norm" ]] && continue
        prefix="$(breaking_prefix "$norm")"
        printf '%s\t%s\t%s\t%s\t%s\n' \
          "$pkg" "$prefix" "$norm" "$(format_file_label "$pkgjson"):$deptype" "$raw" >> "$suggest_file"
      done < <(jq -r '
        ["dependencies","devDependencies","peerDependencies","optionalDependencies"] as $types
        | $types[] as $dt
        | (.[$dt] // {}) | to_entries[]
        | "\(.key)\t\($dt)\t\(.value)"
      ' "$pkgjson" 2>/dev/null)
    done

    while IFS=$'\t' read -r pkg kind versions locs; do
      SUGGEST_PKG+=("$pkg")
      SUGGEST_KIND+=("$kind")
      SUGGEST_VERSIONS+=("$versions")
      SUGGEST_LOCS+=("$locs")
    done < <(
      sort -t $'\t' -k1,1 "$suggest_file" | awk -F'\t' '
        function flush() {
          if (pkg == "" || count < 2 || prefixCount != 1) return
          kind = (versionCount == 1 ? "same version" : "same breaking range")
          print pkg "\t" kind "\t" versions "\t" locs
        }
        {
          if ($1 != pkg) {
            flush()
            pkg = $1
            count = 0
            prefixCount = 0
            versionCount = 0
            versions = ""
            locs = ""
            delete prefixes
            delete versionSeen
          }
          count++
          if (!($2 in prefixes)) {
            prefixes[$2] = 1
            prefixCount++
          }
          if (!($3 in versionSeen)) {
            versionSeen[$3] = 1
            versionCount++
            versions = versions (versions ? ", " : "") $3
          }
          locs = locs (locs ? " | " : "") $4 "=" $5
        }
        END {
          flush()
        }
      '
    )
  fi
fi

cleanup_count=${#UNUSED_CATALOG_PKGS[@]}
for sections in "${CLEAN_SECTIONS[@]}"; do
  while IFS= read -r section; do
    [[ -n "$section" ]] && cleanup_count=$((cleanup_count + 1))
  done <<< "$sections"
done

echo -e "${BOLD}Checking cleanup opportunities...${RESET}  ${DIM}(${pm})${RESET}"

if [[ ${#UNUSED_CATALOG_PKGS[@]} -gt 0 ]]; then
  echo ""
  echo -e "  ${BOLD}Unused pnpm catalog entries:${RESET}"
  for pkg in "${UNUSED_CATALOG_PKGS[@]}"; do
    printf "  ${YELLOW}%-38s${RESET}  ${DIM}pnpm-workspace.yaml${RESET}\n" "$pkg"
  done
fi

if [[ ${#CLEAN_FILES[@]} -gt 0 ]]; then
  echo ""
  echo -e "  ${BOLD}Empty manifest sections:${RESET}"
  for i in "${!CLEAN_FILES[@]}"; do
    printf "  ${YELLOW}%-38s${RESET}  ${DIM}%s${RESET}\n" \
      "$(format_sections "${CLEAN_SECTIONS[$i]}")" "$(format_file_label "${CLEAN_FILES[$i]}")"
  done
fi

if [[ ${#SUGGEST_PKG[@]} -gt 0 ]]; then
  echo ""
  echo -e "  ${BOLD}Catalog suggestions${RESET} ${DIM}(report only):${RESET}"
  for i in "${!SUGGEST_PKG[@]}"; do
    printf "  ${CYAN}%-38s${RESET}  ${DIM}%s — %s${RESET}\n" \
      "${SUGGEST_PKG[$i]}" "${SUGGEST_KIND[$i]}" "${SUGGEST_VERSIONS[$i]}"
    printf "  ${DIM}  ↳ %s${RESET}\n" "${SUGGEST_LOCS[$i]}"
  done
fi

if $named_catalogs_present; then
  echo ""
  echo -e "  ${BOLD}Catalog cleanup skipped${RESET} ${DIM}(named pnpm catalogs detected).${RESET}"
fi

echo ""

if [[ $cleanup_count -eq 0 ]]; then
  if [[ ${#SUGGEST_PKG[@]} -gt 0 ]]; then
    echo -e "${GREEN}✓ No automatic cleanup needed.${RESET}"
  else
    echo -e "${GREEN}✓ Nothing to clean.${RESET}"
  fi
  exit 0
fi

$DRY_RUN && { echo -e "${CYAN}Dry run — no changes made.${RESET}"; exit 0; }

confirm_apply "Apply ${cleanup_count} cleanup(s)?" "$AUTO_YES" || exit 0

if [[ ${#UNUSED_CATALOG_PKGS[@]} -gt 0 ]]; then
  pnpm_remove_catalog_keys "${UNUSED_CATALOG_PKGS[@]}"
  for pkg in "${UNUSED_CATALOG_PKGS[@]}"; do
    echo -e "  ${CYAN}pnpm-workspace.yaml${RESET}  removed unused catalog entry ${pkg}"
  done
fi

for i in "${!CLEAN_FILES[@]}"; do
  file="${CLEAN_FILES[$i]}"
  mapfile -t sections < <(printf '%s\n' "${CLEAN_SECTIONS[$i]}")
  node - "$file" "${sections[@]}" <<'EOF'
const fs = require('fs');
const file = process.argv[2];
const sections = process.argv.slice(3);
const source = fs.readFileSync(file, 'utf8');
let changed = false;
const hasFinalNewline = source.endsWith('\n');
const lines = source.replace(/\n$/, '').split('\n');

function previousContentIndex(start) {
  for (let i = start; i >= 0; i--) {
    if (lines[i].trim() !== '') return i;
  }
  return -1;
}

for (const key of sections) {
  const escaped = key.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const matcher = new RegExp(`^(\\s*)"${escaped}":\\s*\\{\\}\\s*(,?)\\s*$`);
  const index = lines.findIndex((line) => matcher.test(line));
  if (index === -1) continue;

  const match = lines[index].match(matcher);
  const hadTrailingComma = Boolean(match && match[2]);
  lines.splice(index, 1);
  changed = true;

  if (!hadTrailingComma) {
    const prev = previousContentIndex(index - 1);
    if (prev >= 0) {
      lines[prev] = lines[prev].replace(/,\s*$/, '');
    }
  }
}

if (changed) {
  fs.writeFileSync(file, lines.join('\n') + (hasFinalNewline ? '\n' : ''));
}
EOF
  echo -e "  ${CYAN}$(format_file_label "$file")${RESET}  removed $(format_sections "${CLEAN_SECTIONS[$i]}")"
done

echo ""
echo -e "${GREEN}✓ Done! ${cleanup_count} cleanup(s) applied.${RESET}"
