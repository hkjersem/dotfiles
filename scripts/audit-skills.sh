#!/usr/bin/env bash
#
# Audit check for npx skills (github.com/vercel-labs/skills).
# Verifies that every skill in SKILLS_DIR is correctly symlinked into each
# preferred agent provider's skills directory.
#
# Designed to be sourced by scripts/audit.sh (shares its output helpers and
# ISSUES/WARNINGS counters) or run standalone as a one-off check.
#
# Only runs when SKILLS_DIR exists — i.e. npx skills has been used globally.
#
# Usage (standalone):
#   bash scripts/audit-skills.sh [options]
#
# With no options, runs a read-only audit: checks lockfile consistency and
# symlink state, reports issues and warnings, but makes no changes.
#
# Options:
#   --fix-symlinks   Create or repair missing/wrong symlinks in each provider's
#                    skills directory, pointing back to SKILLS_DIR. Also removes
#                    dangling symlinks left behind by uninstalled skills.
#   --fix-lockfile   Remove ghost entries from the lockfile (.skill-lock.json)
#                    for skills whose folder no longer exists on disk.
#   --clean-stale    Remove listed agent directories only when their skills/
#                    subtree contains verified managed links and empty dirs.
#   --quiet          Suppress per-check output; only print a one-line summary.

# ── CONFIGURATION ─────────────────────────────────────────────────────────────

# Runtime canonical location — contains installed third-party skills and
# symlinks to skills authored in this dotfiles repository.
SKILLS_DIR="$HOME/.agents/skills"
LOCK_FILE="$(dirname "$SKILLS_DIR")/.skill-lock.json"

# Preferred agent providers to check. Format: "agent-id:absolute-path-to-skills-dir"
# A provider is only checked when its PARENT directory exists on disk (i.e. the
# agent is installed). To skip a provider entirely, comment it out or remove it.
PREFERRED_PROVIDERS=(
    "claude-code:$HOME/.claude/skills"
    "codex:$HOME/.codex/skills"
    "gemini-cli:$HOME/.gemini/skills"
    "github-copilot:$HOME/.copilot/skills"
    "universal:$HOME/.config/agents/skills"
)

# Agent home directories to flag when they contain ONLY a skills/ subdirectory.
# If a listed directory exists and has nothing besides skills/, the audit warns
# that the agent may not be installed and the directory is safe to delete.
AUTO_DELETE_CANDIDATES=(
    "$HOME/.codebuddy"
    "$HOME/.commandcode"
    "$HOME/.continue"
    "$HOME/.cline"
    "$HOME/.cursor"
    "$HOME/.factory"
    "$HOME/.junie"
    "$HOME/.kilocode"
    "$HOME/.kiro"
    "$HOME/.kode"
    "$HOME/.mcpjam"
    "$HOME/.neovate"
    "$HOME/.openhands"
    "$HOME/.pi"
    "$HOME/.pochi"
    "$HOME/.qoder"
    "$HOME/.qwen"
    "$HOME/.roo"
    "$HOME/.trae"
    "$HOME/.zencoder"
    "$HOME/.codeium"
    "$HOME/.config/crush"
    "$HOME/.config/goose"
    "$HOME/.config/opencode"
)

# ── STANDALONE VS SOURCED ─────────────────────────────────────────────────────
# When sourced by audit.sh the parent script's helpers and ISSUES/WARNINGS
# counters are already defined. When run directly we set up our own.

# --fix-symlinks is only meaningful when run standalone; sourced runs are always read-only.
FIX_MODE=0
FIX_LOCKFILE_MODE=0
CLEAN_STALE_MODE=0
QUIET_MODE=0
if ! (return 0 2>/dev/null); then
    # Running standalone
    for _arg in "$@"; do
        case "$_arg" in
            --fix-symlinks) FIX_MODE=1 ;;
            --fix-lockfile) FIX_LOCKFILE_MODE=1 ;;
            --clean-stale) CLEAN_STALE_MODE=1 ;;
            --quiet) QUIET_MODE=1 ;;
            *) echo "Unknown option: $_arg" >&2; exit 2 ;;
        esac
    done
    unset _arg

    BOLD=$'\033[1m'
    RED=$'\033[0;31m'
    YELLOW=$'\033[1;33m'
    GREEN=$'\033[0;32m'
    RESET=$'\033[0m'

    ISSUES=0
    WARNINGS=0
    CHANGES=0

    SECTION_OUTPUT=()
    SECTION_ISSUES=0
    SECTION_WARNINGS=0
    CURRENT_SECTION=""

    section_start() {
        CURRENT_SECTION="$1"
        SECTION_OUTPUT=()
        SECTION_ISSUES=0
        SECTION_WARNINGS=0
    }

    section_end() {
        [ "$QUIET_MODE" -eq 1 ] && return
        echo ""
        echo "${BOLD}── ${CURRENT_SECTION} ──────────────────────────────────${RESET}"
        if [[ $SECTION_ISSUES -eq 0 && $SECTION_WARNINGS -eq 0 && ${#SECTION_OUTPUT[@]} -gt 0 ]]; then
            echo "  ${GREEN}✅${RESET} All checks passed"
        else
            printf '%s\n' "${SECTION_OUTPUT[@]}"
        fi
    }

    ORANGE=$'\033[0;33m'

    ok()   { SECTION_OUTPUT+=("  ${GREEN}✅${RESET} $1"); }
    warn() { SECTION_OUTPUT+=("  ${YELLOW}🟡${RESET} $1"); ((WARNINGS++)); ((SECTION_WARNINGS++)); }
    attn() { SECTION_OUTPUT+=("  ${ORANGE}🟠${RESET} $1"); ((WARNINGS++)); ((SECTION_WARNINGS++)); }
    fail() { SECTION_OUTPUT+=("  ${RED}🔴${RESET} $1"); ((ISSUES++)); ((SECTION_ISSUES++)); }
    fixed() { ok "$1"; CHANGES=$((CHANGES + 1)); }

    _SKILLS_STANDALONE=1
else
    _SKILLS_STANDALONE=0
fi

# ── LOAD IGNORE LIST ───────────────────────────────────────────────────────────

SKILLS_IGNORE=()
DOTFILES="${DOTFILES:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
source "$DOTFILES/scripts/lib/skills-utils.sh" || { return 1 2>/dev/null || exit 1; }
REPO_SKILLS_DIR="$DOTFILES/agents/skills"
SKILLS_MANIFEST="$REPO_SKILLS_DIR/skills.txt"
if [ -f "$DOTFILES/audit.ignore" ]; then
    while IFS= read -r line; do
        [[ -z "$line" || "$line" == \#* ]] && continue
        [[ "$line" == skills:* ]] && SKILLS_IGNORE+=("${line#skills:}")
    done < "$DOTFILES/audit.ignore"
fi

is_skills_ignored() {
    local name="$1"
    [[ ${#SKILLS_IGNORE[@]} -gt 0 ]] || return 1
    printf '%s\n' "${SKILLS_IGNORE[@]}" | grep -qFx "$name"
}

if ! skills_check_manifest "$SKILLS_MANIFEST" "$REPO_SKILLS_DIR"; then
    section_start "Skills — Manifest"
    fail "invalid or conflicting manifest; no repairs performed"
    section_end
    [[ "$_SKILLS_STANDALONE" -eq 1 ]] && exit 1 || return 1
fi

# ── COLLECT REPO-AUTHORED SKILLS ───────────────────────────────────────────────

REPO_SKILL_NAMES=()
if [ -d "$REPO_SKILLS_DIR" ]; then
    for skill_path in "$REPO_SKILLS_DIR"/*/; do
        [ -f "$skill_path/SKILL.md" ] || continue
        REPO_SKILL_NAMES+=("$(basename "$skill_path")")
    done
fi

# ── SKIP IF NO SKILLS ARE CONFIGURED ───────────────────────────────────────────

HAS_MANIFEST_SKILLS=0
if [ -f "$SKILLS_MANIFEST" ] \
    && grep -Eq '^[[:space:]]*([^#[:space:]]|#[[:space:]]disabled:)' "$SKILLS_MANIFEST"; then
    HAS_MANIFEST_SKILLS=1
fi

# ── COLLECT SKILLS ─────────────────────────────────────────────────────────────

SKILL_NAMES=()
for skill_path in "$SKILLS_DIR"/*/; do
    [ -d "$skill_path" ] || continue
    SKILL_NAMES+=("$(basename "$skill_path")")
done

# ── REPO-AUTHORED SKILLS ───────────────────────────────────────────────────────

if [ "${#REPO_SKILL_NAMES[@]}" -gt 0 ]; then
    section_start "Skills — Repo"

    for skill_name in "${REPO_SKILL_NAMES[@]}"; do
        repo_skill="$REPO_SKILLS_DIR/$skill_name"
        canonical="$SKILLS_DIR/$skill_name"

        if [ -L "$canonical" ] \
            && skills_same_path "$canonical" "$repo_skill"; then
            continue
        fi

        if [ -e "$canonical" ] || [ -L "$canonical" ]; then
            fail "$skill_name (name conflict at $canonical — left unchanged)"
        else
            fail "$skill_name (not linked — run: bash scripts/sync-agent-skills.sh)"
        fi
    done

    [[ $SECTION_ISSUES -eq 0 && $SECTION_WARNINGS -eq 0 ]] && ok "all repo-authored skills linked"
    section_end
fi

# ── CROSS-MACHINE THIRD-PARTY SKILLS ───────────────────────────────────────────

if [ "$HAS_MANIFEST_SKILLS" -eq 1 ]; then
    section_start "Skills — Manifest"

    MANIFEST_SOURCE_CHECK=1
    if [ ! -f "$LOCK_FILE" ]; then
        MANIFEST_SOURCE_CHECK=0
        warn "installed skill sources could not be verified (lockfile missing)"
    elif ! command -v jq >/dev/null 2>&1; then
        MANIFEST_SOURCE_CHECK=0
        warn "installed skill sources could not be verified (jq not installed)"
    elif ! skills_valid_lockfile "$LOCK_FILE" 2>/dev/null; then
        MANIFEST_SOURCE_CHECK=0
        warn "installed skill sources could not be verified (invalid lockfile)"
    fi

    while IFS= read -r line || [ -n "$line" ]; do
        line=$(skills_trim "$line")
        [ -z "$line" ] && continue

        disabled=0
        if [[ "$line" == "# disabled:"* ]]; then
            disabled=1
            entry=$(skills_trim "${line#\# disabled:}")
        elif [[ "$line" == \#* ]]; then
            continue
        else
            entry="$line"
        fi

        if [[ "$entry" != *"|"* ]]; then
            fail "invalid manifest entry: $line"
            continue
        fi

        expected_source=$(skills_trim "${entry%%|*}")
        skill_name=$(skills_trim "${entry#*|}")
        if ! skills_valid_source "$expected_source" || ! skills_valid_name "$skill_name"; then
            fail "invalid manifest entry: $line"
            continue
        fi

        canonical="$SKILLS_DIR/$skill_name"
        if [ "$disabled" -eq 1 ]; then
            if [ ! -e "$canonical" ] && [ ! -L "$canonical" ]; then
                continue
            fi

            if [ "$MANIFEST_SOURCE_CHECK" -eq 0 ]; then
                warn "$skill_name (disabled but installed; source could not be verified)"
                continue
            fi

            installed_source=$(jq -r --arg skill "$skill_name" \
                '.skills[$skill].source // empty' "$LOCK_FILE" 2>/dev/null)
            if [ "$installed_source" = "$expected_source" ]; then
                warn "$skill_name (disabled but installed — run: bash scripts/sync-agent-skills.sh)"
            elif [ -n "$installed_source" ]; then
                fail "$skill_name (disabled entry expects $expected_source, but installed from $installed_source)"
            else
                fail "$skill_name (disabled entry expects $expected_source, but installed source is unknown)"
            fi
            continue
        fi

        if [ ! -e "$canonical" ] && [ ! -L "$canonical" ]; then
            warn "$skill_name (missing — run: bash scripts/sync-agent-skills.sh)"
            continue
        fi

        if [ "$MANIFEST_SOURCE_CHECK" -eq 0 ]; then
            continue
        fi

        installed_source=$(jq -r --arg skill "$skill_name" \
            '.skills[$skill].source // empty' "$LOCK_FILE" 2>/dev/null)

        if [ "$installed_source" = "$expected_source" ]; then
            continue
        fi

        if [ -n "$installed_source" ]; then
            fail "$skill_name (source conflict: installed from $installed_source, expected $expected_source)"
        else
            fail "$skill_name (installed from an unknown source, expected $expected_source)"
        fi
    done < "$SKILLS_MANIFEST"

    [[ $SECTION_ISSUES -eq 0 && $SECTION_WARNINGS -eq 0 ]] && ok "all manifest skills installed"
    section_end
fi

# ── LOCKFILE CONSISTENCY ───────────────────────────────────────────────────────

if [ -f "$LOCK_FILE" ]; then
    section_start "Skills — Lockfile"

    if ! command -v jq >/dev/null 2>&1; then
        warn "lockfile could not be verified (jq not installed)"
    elif ! skills_valid_lockfile "$LOCK_FILE" 2>/dev/null; then
        fail "lockfile could not be verified (invalid JSON, skill names, or sources); left unchanged"
    else
        # Extract skill names from lockfile
        LOCKED_SKILLS=()
        while IFS= read -r _key; do
            LOCKED_SKILLS+=("$_key")
        done < <(jq -r '.skills | keys[]' "$LOCK_FILE")
        unset _key

        # Ghost entries: in lockfile but folder missing on disk
        for ((locked_i = 0; locked_i < ${#LOCKED_SKILLS[@]}; locked_i++)); do
            locked="${LOCKED_SKILLS[$locked_i]}"
            if [ ! -d "$SKILLS_DIR/$locked" ]; then
                if [ "$FIX_LOCKFILE_MODE" -eq 1 ]; then
                    if [[ -L "$LOCK_FILE" ]]; then
                        fail "$locked (lockfile is a symlink; left unchanged)"
                    elif lock_tmp=$(mktemp "${LOCK_FILE}.tmp.XXXXXX"); then
                        if jq --arg key "$locked" 'del(.skills[$key])' "$LOCK_FILE" > "$lock_tmp" \
                            && mv "$lock_tmp" "$LOCK_FILE"; then
                            fixed "$locked (removed ghost entry from lockfile)"
                        else
                            rm -f "$lock_tmp"
                            fail "$locked (failed to remove ghost entry from lockfile)"
                        fi
                    else
                        fail "$locked (could not create temporary lockfile)"
                    fi
                else
                    fail "$locked (in lockfile but folder missing — run: npx skills remove \"$locked\")"
                fi
            fi
        done

        # Orphaned folders: on disk but not in lockfile
        for ((skill_i = 0; skill_i < ${#SKILL_NAMES[@]}; skill_i++)); do
            skill_name="${SKILL_NAMES[$skill_i]}"
            found=0
            for ((locked_i = 0; locked_i < ${#LOCKED_SKILLS[@]}; locked_i++)); do
                locked="${LOCKED_SKILLS[$locked_i]}"
                [ "$locked" = "$skill_name" ] && found=1 && break
            done
            if [ "$found" -eq 0 ]; then
                canonical_path="$SKILLS_DIR/$skill_name"
                repo_skill="$REPO_SKILLS_DIR/$skill_name"
                if [ -L "$canonical_path" ] \
                    && [ -d "$repo_skill" ] \
                    && skills_same_path "$canonical_path" "$repo_skill"; then
                    continue
                fi
                warn "$skill_name (folder exists but not in lockfile — installed outside npx skills?)"
            fi
        done
    fi

    [[ $SECTION_ISSUES -eq 0 && $SECTION_WARNINGS -eq 0 ]] && ok "lockfile consistent with disk"

    section_end
fi



section_start "Skills"

for entry in "${PREFERRED_PROVIDERS[@]}"; do
    agent_id="${entry%%:*}"
    skills_dir="${entry#*:}"
    parent_dir="$(dirname "$skills_dir")"

    # Skip providers whose parent directory doesn't exist (agent not installed)
    [ -d "$parent_dir" ] || continue
    if [[ -L "$skills_dir" ]]; then
        if skills_same_path "$skills_dir" "$SKILLS_DIR"; then
            continue
        fi
        fail "$agent_id: skills directory is an external symlink; left unchanged"
        continue
    fi

    # Check for dangling symlinks — skills removed from SKILLS_DIR but still linked
    if [ -d "$skills_dir" ]; then
        for link in "$skills_dir"/*; do
            [ -L "$link" ] || continue
            [ -e "$link" ] && continue  # target exists, not dangling
            skill_name="$(basename "$link")"
            if ! skills_managed_link "$link" "$SKILLS_DIR"; then
                warn "$agent_id: $skill_name (unmanaged dangling symlink; left unchanged)"
                continue
            fi
            if [ "$FIX_MODE" -eq 1 ]; then
                rm "$link" \
                    && fixed "$agent_id: $skill_name (removed dangling symlink)" \
                    || fail "$agent_id: $skill_name (failed to remove dangling symlink)"
            else
                fail "$agent_id: $skill_name (dangling symlink — skill was removed; run --fix-symlinks to clean up)"
            fi
        done
    fi

    for ((skill_i = 0; skill_i < ${#SKILL_NAMES[@]}; skill_i++)); do
        skill_name="${SKILL_NAMES[$skill_i]}"
        canonical="$SKILLS_DIR/$skill_name"
        link="$skills_dir/$skill_name"

        if [ ! -e "$link" ] && [ ! -L "$link" ]; then
            if [ "$FIX_MODE" -eq 1 ]; then
                mkdir -p "$skills_dir" && ln -s "$canonical" "$link" \
                    && fixed "$agent_id: $skill_name (linked)" \
                    || fail "$agent_id: $skill_name (failed to create symlink)"
            else
                fail "$agent_id: $skill_name (not linked)"
            fi
        elif [ ! -L "$link" ]; then
            fail "$agent_id: $skill_name (real file or folder conflicts with canonical skill; left unchanged)"
        elif ! skills_same_path "$link" "$canonical"; then
            if ! skills_managed_link "$link" "$SKILLS_DIR"; then
                fail "$agent_id: $skill_name (unmanaged symlink conflict; left unchanged)"
            elif [ "$FIX_MODE" -eq 1 ]; then
                rm "$link" && ln -s "$canonical" "$link" \
                    && fixed "$agent_id: $skill_name (fixed symlink target)" \
                    || fail "$agent_id: $skill_name (failed to fix symlink target)"
            else
                fail "$agent_id: $skill_name (wrong symlink target: $(readlink "$link"))"
            fi
        fi
        # No output for correctly linked skills
    done
done

# If everything passed silently, emit one confirmation so section_end has something to show
[[ $SECTION_ISSUES -eq 0 && $SECTION_WARNINGS -eq 0 ]] && ok "all skills correctly linked"

section_end

# ── AUTO-DELETE CANDIDATES ─────────────────────────────────────────────────────

if [ "${#AUTO_DELETE_CANDIDATES[@]}" -gt 0 ]; then
    _stale_section_open=0

    for candidate in "${AUTO_DELETE_CANDIDATES[@]}"; do
        [ -d "$candidate" ] || continue

        skills_stale_plan "$candidate" "$SKILLS_DIR"
        plan_status=$?

        _will_output=0
        if [ "$plan_status" -eq 0 ] || [ "$plan_status" -eq 2 ]; then
            _will_output=1
        else
            dir_name=$(basename "$candidate")
            ! is_skills_ignored "$dir_name" && _will_output=1
        fi

        if [ "$_will_output" -eq 1 ] && [ "$_stale_section_open" -eq 0 ]; then
            section_start "Skills — Stale Agent Dirs"
            _stale_section_open=1
        fi

        if [ "$plan_status" -eq 2 ]; then
            fail "$candidate (could not inspect contents; left unchanged)"
        elif [ "$plan_status" -eq 0 ]; then
            if [ "$CLEAN_STALE_MODE" -eq 1 ]; then
                skills_clean_stale "$candidate" "$SKILLS_DIR" \
                    && fixed "$candidate (removed managed links and empty directories)" \
                    || fail "$candidate (failed to remove)"
            else
                warn "$candidate — only managed links and empty directories; use --clean-stale to remove"
            fi
        else
            dir_name=$(basename "$candidate")
            if ! is_skills_ignored "$dir_name"; then
                attn "$candidate — real content or unmanaged links preserved"
            fi
        fi
    done

    [ "$_stale_section_open" -eq 1 ] && section_end
    unset _stale_section_open
fi

# ── SUMMARY (standalone only) ──────────────────────────────────────────────────

if [ "$_SKILLS_STANDALONE" -eq 1 ]; then
    if [ "$QUIET_MODE" -eq 1 ]; then
        if [ "$ISSUES" -gt 0 ]; then
            echo "  ${RED}🔴${RESET} Skills: $ISSUES issue(s) — run without --quiet to see details"
        elif [ "$WARNINGS" -gt 0 ]; then
            echo "  ${YELLOW}🟡${RESET} Skills: $WARNINGS warning(s), $CHANGES change(s) — run without --quiet to see details"
        elif [ "$CHANGES" -gt 0 ]; then
            echo "  ${GREEN}✅${RESET} Skills: $CHANGES change(s)"
        else
            echo "  ${GREEN}✅${RESET} Skills ok"
        fi
    else
        echo ""
        echo "${BOLD}────────────────────────────────────────${RESET}"
        if [ "$ISSUES" -eq 0 ] && [ "$WARNINGS" -eq 0 ]; then
            echo "  ✅  All skills linked — no issues found"
        else
            [ "$ISSUES" -gt 0 ]   && echo "  ${RED}🔴  $ISSUES issue(s) found${RESET}"
            [ "$WARNINGS" -gt 0 ] && echo "  ${YELLOW}🟡  $WARNINGS warning(s) found${RESET}"
        fi
        echo "${BOLD}────────────────────────────────────────${RESET}"
        echo ""
    fi

    [ "$ISSUES" -gt 0 ] && exit 1
    exit 0
fi
