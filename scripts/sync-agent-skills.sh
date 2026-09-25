#!/usr/bin/env bash
#
# Sync Git-tracked skills and the global third-party skills manifest.
#
# Usage:
#   bash scripts/sync-agent-skills.sh [--dry-run] [--quiet]
#
# Repo-authored skills are linked into ~/.agents/skills. Existing paths are
# never overwritten: conflicting names are reported and left unchanged.
#
# agents/skills/skills.txt declares third-party skills installed on every machine:
#   source|skill-name
# Disable and uninstall a managed skill by commenting it with:
#   # disabled: source|skill-name

set -u

DOTFILES="${DOTFILES:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
AUTHORED_DIR="$DOTFILES/agents/skills"
MANIFEST="$DOTFILES/agents/skills/skills.txt"
CANONICAL_DIR="$HOME/.agents/skills"
LOCK_FILE="$HOME/.agents/.skill-lock.json"
source "$DOTFILES/scripts/lib/skills-utils.sh" || exit 1

DRY_RUN=0
QUIET=0
CHANGES=0
CONFLICTS=0
ERRORS=0

for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=1 ;;
        --quiet) QUIET=1 ;;
        *)
            echo "Unknown option: $arg" >&2
            exit 2
            ;;
    esac
done

info() {
    [ "$QUIET" -eq 1 ] || printf '  %s\n' "$1"
}

changed() {
    CHANGES=$((CHANGES + 1))
    info "linked: $1"
}

conflict() {
    CONFLICTS=$((CONFLICTS + 1))
    printf '  warning: %s\n' "$1" >&2
}

error() {
    ERRORS=$((ERRORS + 1))
    printf '  error: %s\n' "$1" >&2
}

sync_authored_skills() {
    local source skill_name destination

    [ -d "$AUTHORED_DIR" ] || return

    if [ "$DRY_RUN" -eq 0 ]; then
        mkdir -p "$CANONICAL_DIR" || {
            error "could not create $CANONICAL_DIR"
            return
        }
    fi

    for source in "$AUTHORED_DIR"/*; do
        [ -d "$source" ] || continue
        [ -f "$source/SKILL.md" ] || {
            error "$(basename "$source") has no SKILL.md"
            continue
        }

        skill_name=$(basename "$source")
        skills_valid_name "$skill_name" || {
            error "invalid authored skill name: $skill_name"
            continue
        }
        destination="$CANONICAL_DIR/$skill_name"

        if [ -L "$destination" ] && skills_same_path "$destination" "$source"; then
            info "ok: $skill_name"
            continue
        fi

        if [ -e "$destination" ] || [ -L "$destination" ]; then
            conflict "$skill_name already exists at $destination; left unchanged"
            continue
        fi

        if [ "$DRY_RUN" -eq 1 ]; then
            changed "$skill_name -> $source (dry run)"
        elif ln -s "$source" "$destination"; then
            changed "$skill_name -> $source"
        else
            error "could not link $skill_name"
        fi
    done
}

sync_third_party_skills() {
    local line entry source skill_name installed_source disabled

    [ -f "$MANIFEST" ] || return

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
            error "invalid manifest entry: $line"
            continue
        fi

        source=$(skills_trim "${entry%%|*}")
        skill_name=$(skills_trim "${entry#*|}")
        if ! skills_valid_source "$source" || ! skills_valid_name "$skill_name"; then
            error "invalid manifest entry: $line"
            continue
        fi
        if [[ -d "$AUTHORED_DIR/$skill_name" ]]; then
            conflict "$skill_name is repo-authored; manifest entry left unchanged"
            continue
        fi

        if [ "$disabled" -eq 1 ]; then
            if [ ! -e "$CANONICAL_DIR/$skill_name" ] && [ ! -L "$CANONICAL_DIR/$skill_name" ]; then
                info "disabled: $skill_name"
                continue
            fi
            if [[ -L "$CANONICAL_DIR/$skill_name" ]]; then
                conflict "$skill_name is a canonical symlink; left unchanged"
                continue
            fi

            if [ ! -f "$LOCK_FILE" ] || ! command -v jq >/dev/null 2>&1; then
                error "cannot safely uninstall $skill_name without a readable lockfile and jq"
                continue
            fi

            installed_source=$(jq -r --arg skill "$skill_name" \
                '.skills[$skill].source // empty' "$LOCK_FILE" 2>/dev/null)
            if [ "$installed_source" != "$source" ]; then
                conflict "$skill_name exists from ${installed_source:-an unknown source}; disabled entry expects $source; left unchanged"
                continue
            fi

            if ! command -v npx >/dev/null 2>&1; then
                error "npx is required to uninstall $skill_name"
                continue
            fi

            if [ "$DRY_RUN" -eq 1 ]; then
                info "would uninstall: $skill_name from $source"
            elif npx -y skills remove "$skill_name" -g -y </dev/null; then
                if [ -e "$CANONICAL_DIR/$skill_name" ] || [ -L "$CANONICAL_DIR/$skill_name" ]; then
                    error "$skill_name still exists after uninstall"
                else
                    CHANGES=$((CHANGES + 1))
                    info "uninstalled: $skill_name ($source)"
                fi
            else
                error "could not uninstall $skill_name from $source"
            fi
            continue
        fi

        if [ -e "$CANONICAL_DIR/$skill_name" ] || [ -L "$CANONICAL_DIR/$skill_name" ]; then
            installed_source=""
            if [ -f "$LOCK_FILE" ] && command -v jq >/dev/null 2>&1; then
                installed_source=$(jq -r --arg skill "$skill_name" \
                    '.skills[$skill].source // empty' "$LOCK_FILE" 2>/dev/null)
            fi

            if [ "$installed_source" = "$source" ]; then
                if [[ -f "$CANONICAL_DIR/$skill_name/SKILL.md" ]]; then
                    info "ok: $skill_name ($source)"
                else
                    error "$skill_name has no readable SKILL.md; left unchanged"
                fi
            else
                conflict "$skill_name already exists from ${installed_source:-an unknown source}; wanted $source; left unchanged"
            fi
            continue
        fi

        if ! command -v npx >/dev/null 2>&1; then
            error "npx is required to install $skill_name"
            continue
        fi

        if [ "$DRY_RUN" -eq 1 ]; then
            info "would install: $skill_name from $source"
        # The loop reads the manifest from stdin, so child commands must not
        # inherit it and consume entries that have not been processed yet.
        elif npx -y skills add "$source" --skill "$skill_name" -g -y </dev/null; then
            if [[ -f "$CANONICAL_DIR/$skill_name/SKILL.md" && -f "$LOCK_FILE" ]] \
                && skills_valid_lockfile "$LOCK_FILE" \
                && [[ "$(jq -r --arg skill "$skill_name" '.skills[$skill].source // empty' "$LOCK_FILE")" == "$source" ]]; then
                CHANGES=$((CHANGES + 1))
            else
                error "$skill_name installation could not be verified"
            fi
        else
            error "could not install $skill_name from $source"
        fi
    done < "$MANIFEST"
}

if [[ -f "$MANIFEST" || -f "$LOCK_FILE" ]]; then
    command -v jq >/dev/null 2>&1 || { error "jq is required for skill source verification"; exit 1; }
fi
if [[ -f "$LOCK_FILE" ]] && ! skills_valid_lockfile "$LOCK_FILE"; then
    error "invalid skill lockfile; nothing changed"
    exit 1
fi
skills_check_manifest "$MANIFEST" "$AUTHORED_DIR" || exit 1

sync_authored_skills
sync_third_party_skills

if [ "$QUIET" -eq 1 ]; then
    if [ "$ERRORS" -gt 0 ] || [ "$CONFLICTS" -gt 0 ]; then
        printf '  Agent skills: %d change(s), %d conflict(s), %d error(s)\n' \
            "$CHANGES" "$CONFLICTS" "$ERRORS"
    elif [ "$CHANGES" -gt 0 ]; then
        printf '  Agent skills: %d change(s)\n' "$CHANGES"
    fi
else
    printf '  Agent skills: %d change(s), %d conflict(s), %d error(s)\n' \
        "$CHANGES" "$CONFLICTS" "$ERRORS"
fi

[ "$ERRORS" -eq 0 ] && [ "$CONFLICTS" -eq 0 ]
