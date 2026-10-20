#!/usr/bin/env bash
# shellcheck disable=SC2034  # globals set here are read by corepack-defaults.sh and audit.sh
# Shared package-manager version helpers, sourced by corepack-defaults.sh and audit.sh.
#
# Outside projects with a packageManager field, corepack runs the version in
# lastKnownGood.json. It only advances that default when a project happens to
# download a newer release in the same major, and it never applies the npm
# release-age cooldown. These helpers choose cooldown-eligible replacements.

# shellcheck source=pm-utils.sh
source "$(dirname "${BASH_SOURCE[0]}")/pm-utils.sh"

COREPACK_DEFAULT_MANAGERS="pnpm yarn"
COREPACK_DEFAULT_COOLDOWN_MINUTES=4320

corepack_lkg_file() {
  printf '%s/lastKnownGood.json\n' "${COREPACK_HOME:-$HOME/.cache/node/corepack}"
}

# Prints the current default version for a package manager, without the hash.
corepack_current_default() {
  local file
  file=$(corepack_lkg_file)
  [[ -f "$file" ]] || return 0
  node -e '
    const fs = require("fs");
    try {
      const value = JSON.parse(fs.readFileSync(process.argv[1], "utf8"))[process.argv[2]];
      if (typeof value === "string") console.log(value.replace(/\+.*/, ""));
    } catch {}
  ' "$file" "$1"
}

corepack_cooldown_minutes() {
  local COOLDOWN_MINUTES="" COOLDOWN_SOURCE="" CONFIG_RELEASE_AGE=""
  [[ -f "$HOME/.npmrc" ]] && read_npmrc_release_age "$HOME/.npmrc" days
  printf '%s\n' "${COOLDOWN_MINUTES:-$COREPACK_DEFAULT_COOLDOWN_MINUTES}"
}

# Usage: release_plan <npm package> <current version or empty>
# Sets RP_TARGET (newer release in the current major, or the newest release when
# current is empty), RP_MAJOR (newest release in a later major) and RP_MAJOR_GAP
# (how many majors RP_MAJOR is ahead). Only stable releases older than the
# cooldown and not above the latest major are eligible. RP_CURRENT_AGE_HOURS is
# set when the current version itself is still inside the cooldown.
# Returns 1 when registry metadata is unavailable.
_RP_CACHE_PACKAGE=""
_RP_CACHE_METADATA=""
release_plan() {
  local package="$1" current="$2" metadata result cooldown
  RP_TARGET=""
  RP_MAJOR=""
  RP_MAJOR_GAP=0
  RP_CURRENT_AGE_HOURS=""
  if [[ "$_RP_CACHE_PACKAGE" == "$package" ]]; then
    metadata="$_RP_CACHE_METADATA"
  else
    metadata=$(npm view "$package" time dist-tags versions --json 2>/dev/null) || return 1
    _RP_CACHE_PACKAGE="$package"
    _RP_CACHE_METADATA="$metadata"
  fi
  cooldown=$(corepack_cooldown_minutes)
  result=$(printf '%s' "$metadata" | node -e '
    const [current, cooldown] = process.argv.slice(1);
    let data = JSON.parse(require("fs").readFileSync(0, "utf8"));
    // npm 12 wraps multi-field `npm view --json` output in an array.
    if (Array.isArray(data)) data = data[0] || {};
    const parse = (v) => v.split(".").map(Number);
    const cmp = (a, b) => a[0] - b[0] || a[1] - b[1] || a[2] - b[2];
    const latest = data["dist-tags"] && data["dist-tags"].latest;
    if (!latest || !data.time) process.exit(1);
    const latestVersion = parse(latest);
    const published = new Set([].concat(data.versions || []));
    const cutoff = Date.now() - Number(cooldown) * 60000;
    const eligible = Object.keys(data.time)
      .filter((v) => /^\d+\.\d+\.\d+$/.test(v) && published.has(v))
      .filter((v) => Date.parse(data.time[v]) <= cutoff)
      .map(parse)
      // Releases above `latest` carry only next-style tags.
      .filter((v) => cmp(v, latestVersion) <= 0)
      .sort(cmp);
    const newest = (list) => (list.length ? list[list.length - 1].join(".") : "");
    if (!current) {
      console.log(newest(eligible) + "||0|");
      process.exit(0);
    }
    const cur = parse(current);
    const sameMajor = eligible.filter((v) => v[0] === cur[0] && cmp(v, cur) > 0);
    const laterMajor = eligible.filter((v) => v[0] > cur[0]);
    const major = newest(laterMajor);
    const released = Date.parse(data.time[current]);
    const young = released > cutoff ? Math.floor((Date.now() - released) / 3600000) : "";
    console.log(newest(sameMajor) + "|" + major + "|" + (major ? parse(major)[0] - cur[0] : 0) + "|" + young);
  ' "$current" "$cooldown") || return 1

  IFS='|' read -r RP_TARGET RP_MAJOR RP_MAJOR_GAP RP_CURRENT_AGE_HOURS <<< "$result"
  return 0
}

# Sets CP_CURRENT, CP_TARGET, CP_MAJOR and CP_MAJOR_GAP for a corepack-managed
# package manager. The default is shared by every fnm Node version.
corepack_default_plan() {
  local pm="$1" package="$1"
  CP_CURRENT=$(corepack_current_default "$pm")
  CP_TARGET=""
  CP_MAJOR=""
  CP_MAJOR_GAP=0
  CP_CURRENT_AGE_HOURS=""
  # Yarn 2+ is published separately; Yarn 1 stays on the classic package.
  if [[ "$pm" == yarn && -n "$CP_CURRENT" && "${CP_CURRENT%%.*}" != 1 ]]; then
    package="@yarnpkg/cli-dist"
  fi
  release_plan "$package" "$CP_CURRENT" || return 1
  CP_TARGET="$RP_TARGET"
  CP_MAJOR="$RP_MAJOR"
  CP_MAJOR_GAP="$RP_MAJOR_GAP"
  CP_CURRENT_AGE_HOURS="$RP_CURRENT_AGE_HOURS"
}

# Prints each fnm Node installation directory.
fnm_node_installations() {
  local dir="${FNM_DIR:-}" inst
  if [[ -z "$dir" ]]; then
    dir="$HOME/.local/share/fnm"
    [[ -d "$dir" || ! -d "$HOME/.fnm" ]] || dir="$HOME/.fnm"
  fi
  for inst in "$dir"/node-versions/*/installation; do
    [[ -x "$inst/bin/node" ]] && printf '%s\n' "$inst"
  done
  return 0
}

# Prints the corepack version installed in an fnm Node installation, if any.
corepack_version_for() {
  local inst="$1" script
  [[ -e "$inst/bin/corepack" ]] || return 1
  script=$(realpath "$inst/bin/corepack" 2>/dev/null) || return 1
  "$inst/bin/node" "$script" --version 2>/dev/null
}

# Usage: corepack_update_plan <installed version>
# Sets COREPACK_LATEST to the newest cooldown-eligible corepack release when it
# is newer than the installed one; returns 1 when the registry is unavailable.
corepack_update_plan() {
  COREPACK_LATEST=""
  release_plan corepack "$1" || return 1
  COREPACK_LATEST="${RP_MAJOR:-$RP_TARGET}"
}

fnm_installation_label() {
  basename "$(dirname "$1")"
}

# Prints "major|version version ..." (oldest first) for each Node major that has
# more than one fnm installation; install-node.sh keeps one version per major.
fnm_duplicate_majors() {
  local inst label major
  while IFS= read -r inst; do
    [[ -z "$inst" ]] && continue
    label=$(fnm_installation_label "$inst")
    major="${label#v}"
    printf '%s %s\n' "${major%%.*}" "$label"
  done < <(fnm_node_installations) | sort -k1,1n -k2,2V | awk '
    function flush() { if (n > 1) print major "|" versions }
    $1 != major { flush(); major = $1; versions = $2; n = 1; next }
    { versions = versions " " $2; n++ }
    END { flush() }
  '
}

# Prints "major|end date" for each installed fnm Node major that is past its
# end-of-life in Node's release schedule; returns 1 when the schedule is unavailable.
NODE_SCHEDULE_URL="${NODE_SCHEDULE_URL:-https://raw.githubusercontent.com/nodejs/Release/main/schedule.json}"
fnm_end_of_life_majors() {
  local schedule majors inst label
  majors=$(while IFS= read -r inst; do
    [[ -z "$inst" ]] && continue
    label=$(fnm_installation_label "$inst")
    label="${label#v}"
    printf '%s\n' "${label%%.*}"
  done < <(fnm_node_installations) | sort -un)
  schedule=$(curl -fsS --max-time 10 "$NODE_SCHEDULE_URL" 2>/dev/null) || return 1
  printf '%s' "$schedule" | node -e '
    const schedule = JSON.parse(require("fs").readFileSync(0, "utf8"));
    const today = new Date().toISOString().slice(0, 10);
    for (const major of process.argv[1].split("\n").filter(Boolean)) {
      const end = (schedule["v" + major] || {}).end;
      if (end && end < today) console.log(major + "|" + end);
    }
  ' "$majors"
}

# Prints "label|executable" for each Bun: one per fnm Node version with a global
# bun package, plus a bun on PATH that comes from somewhere else.
bun_installations() {
  local inst path_bun resolved seen=""
  while IFS= read -r inst; do
    [[ -z "$inst" || ! -x "$inst/bin/bun" ]] && continue
    printf 'Node %s|%s\n' "$(fnm_installation_label "$inst")" "$inst/bin/bun"
    resolved=$(realpath "$inst/bin/bun" 2>/dev/null) && seen="$seen|$resolved|"
  done < <(fnm_node_installations)
  if path_bun=$(command -v bun 2>/dev/null); then
    resolved=$(realpath "$path_bun" 2>/dev/null) || resolved="$path_bun"
    [[ "$seen" == *"|$resolved|"* ]] || printf '%s|%s\n' "$path_bun" "$path_bun"
  fi
  return 0
}

# Bun is not managed by corepack, and its in-major updates come from whichever
# installer provided it. Usage: bun_major_plan <bun executable>
# Sets BUN_CURRENT, BUN_MAJOR and BUN_MAJOR_GAP; returns 1 on failure.
bun_major_plan() {
  BUN_CURRENT=""
  BUN_MAJOR=""
  BUN_MAJOR_GAP=0
  BUN_CURRENT=$("$1" --version 2>/dev/null) || return 1
  [[ "$BUN_CURRENT" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || return 1
  release_plan bun "$BUN_CURRENT" || return 1
  BUN_MAJOR="$RP_MAJOR"
  BUN_MAJOR_GAP="$RP_MAJOR_GAP"
}

# npm releases from this version on honour min-release-age.
NPM_COOLDOWN_SINCE=11.10.0

# Usage: version_at_least <version> <minimum>
version_at_least() {
  [[ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | head -1)" == "$2" ]]
}

# Usage: npm_node_plan <Node version> <installed npm version>
# Sets NP_COMPATIBLE (newest cooldown-eligible npm whose engines accept that
# Node), NP_TARGET (NP_COMPATIBLE when newer than the installed npm) and
# NP_NEWEST (newest eligible npm overall). Only npm 10 and newer are
# considered, so Node older than 18 gets no NP_COMPATIBLE; `npm view` loses the
# engines field for some older ranges. Returns 1 when registry metadata or
# npm's semver module is unavailable.
_NP_CACHE_ENGINES=""
npm_node_plan() {
  local node_version="${1#v}" current="$2" result
  NP_TARGET=""
  NP_COMPATIBLE=""
  NP_NEWEST=""
  release_plan npm "" || return 1
  if [[ -z "$_NP_CACHE_ENGINES" ]]; then
    _NP_CACHE_ENGINES=$(npm view "npm@>=10" version engines.node --json 2>/dev/null) || return 1
  fi
  result=$(printf '%s' "$_RP_CACHE_METADATA" | node -e '
    const fs = require("fs");
    const path = require("path");
    const [nodeVersion, current, cooldown, enginesJson] = process.argv.slice(1);
    // Use the semver copy bundled with the running Node.js npm.
    const npmDir = path.join(path.dirname(fs.realpathSync(process.execPath)), "..", "lib", "node_modules", "npm");
    const semver = require(path.join(npmDir, "node_modules", "semver"));
    let data = JSON.parse(fs.readFileSync(0, "utf8"));
    if (Array.isArray(data)) data = data[0] || {};
    const engines = new Map([].concat(JSON.parse(enginesJson))
      .map((entry) => [entry.version, entry["engines.node"]]));
    const latest = data["dist-tags"] && data["dist-tags"].latest;
    if (!latest || !data.time) process.exit(1);
    const published = new Set([].concat(data.versions || []));
    const cutoff = Date.now() - Number(cooldown) * 60000;
    const eligible = Object.keys(data.time)
      .filter((v) => /^\d+\.\d+\.\d+$/.test(v) && published.has(v))
      .filter((v) => Date.parse(data.time[v]) <= cutoff)
      // npm tags some releases only as next-N; `npm update -g` reverts them.
      .filter((v) => semver.lte(v, latest))
      .sort(semver.compare);
    // Versions without an engines entry are skipped rather than assumed to fit.
    const compatible = eligible.filter((v) => engines.has(v)
      && semver.satisfies(nodeVersion, engines.get(v)));
    const last = (list) => list[list.length - 1] || "";
    const best = last(compatible);
    const target = best && (!semver.valid(current) || semver.gt(best, current)) ? best : "";
    console.log(target + "|" + best + "|" + last(eligible));
  ' "$node_version" "$current" "$(corepack_cooldown_minutes)" "$_NP_CACHE_ENGINES") || return 1
  IFS='|' read -r NP_TARGET NP_COMPATIBLE NP_NEWEST <<< "$result"
  return 0
}
