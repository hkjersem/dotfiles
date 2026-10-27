#!/usr/bin/env bash
#
# Dotfiles audit script — read-only drift detection.
# Compares what the repo declares against what's actually installed on this machine.

for arg in "$@"; do
    case "$arg" in
        -h|--help)
            cat <<'EOF'
Usage: dotfiles-audit

Check for drift between repository declarations and installed state.

  -h, --help  Show this help
EOF
            exit 0
            ;;
        *) echo "Error: unknown option: $arg (see --help)" >&2; exit 2 ;;
    esac
done

DOTFILES="$HOME/.dotfiles"
source "$DOTFILES/scripts/machine-profile.sh" || exit 1
source "$DOTFILES/scripts/lib/command-line-tools.sh" || exit 1
source "$DOTFILES/scripts/lib/output.sh" || exit 1

# Load the machine-local ignore list (~/.config/dotfiles/audit-ignore)
# Format: one entry per line, prefixed by category:
#   brew:<formula>       — suppress brew package warnings
#   home:<dirname>       — suppress home directory warnings
#   skills:<dirname>     — suppress stale agent dir warnings
# Example:
#   brew:fzf
#   home:.npmrc
#   skills:opencode
LOCAL_IGNORE=()
AUDIT_IGNORE_FILE="$HOME/.config/dotfiles/audit-ignore"
if [ -f "$AUDIT_IGNORE_FILE" ]; then
    while IFS= read -r line; do
        [[ -z "$line" || "$line" == \#* ]] && continue
        LOCAL_IGNORE+=("$line")
    done < "$AUDIT_IGNORE_FILE"
fi

is_ignored() {
    local key="$1"
    printf '%s\n' "${LOCAL_IGNORE[@]}" | grep -qFx "$key"
}

ISSUES=0
WARNINGS=0

# Section buffering — collapse to one line if all green
SECTION_OUTPUT=()
SECTION_ISSUES=0
SECTION_WARNINGS=0
SECTION_INFOS=0
CURRENT_SECTION=""

section_start() {
    CURRENT_SECTION="$1"
    SECTION_OUTPUT=()
    SECTION_ISSUES=0
    SECTION_WARNINGS=0
    SECTION_INFOS=0
}

section_end() {
    out_section "$CURRENT_SECTION"
    if [[ $SECTION_ISSUES -eq 0 && $SECTION_WARNINGS -eq 0 && $SECTION_INFOS -eq 0 && ${#SECTION_OUTPUT[@]} -gt 0 ]]; then
        echo "  $OUT_OK All checks passed"
    else
        printf '%s\n' "${SECTION_OUTPUT[@]}"
    fi
}

ok()   { SECTION_OUTPUT+=("  $OUT_OK $1"); }
info() { SECTION_OUTPUT+=("  $OUT_INFO $1"); SECTION_INFOS=$((SECTION_INFOS + 1)); }
warn() { SECTION_OUTPUT+=("  $OUT_WARN $1"); ((WARNINGS++)); ((SECTION_WARNINGS++)); }
fail() { SECTION_OUTPUT+=("  $OUT_FAIL $1"); ((ISSUES++)); ((SECTION_ISSUES++)); }

section_start "Machine profile"
if load_machine_profile; then
    info "Profile: $DOTFILES_PROFILE"
    for tool in nav-pilot gcloud; do
        label="$tool"
        [[ "$tool" == nav-pilot ]] && label="Nav Pilot"
        if command -v "$tool" >/dev/null; then
            if [[ "$DOTFILES_PROFILE" == personal ]]; then
                info "$label is installed on this personal machine; it will still be updated and synced where supported."
            fi
        elif [[ "$DOTFILES_PROFILE" == work ]]; then
            warn "$label is missing (run dotfiles-update to install it)"
        fi
    done
else
    profile_status=$?
    if [[ "$profile_status" -eq 1 ]]; then
        warn "Machine profile is not set (run dotfiles-profile personal or dotfiles-profile work)"
    else
        fail "Machine profile could not be loaded (run dotfiles-profile personal or dotfiles-profile work)"
    fi
fi
section_end

# ──────────────────────────────────────────────────────
# 1. SYMLINKS
# ──────────────────────────────────────────────────────
section_start "Symlinks"

while IFS= read -r line; do
    if [[ "$line" =~ ^[[:space:]]+(~/\.[^:]+):[[:space:]]*(.+)$ ]]; then
        symlink="${BASH_REMATCH[1]}"
        target="$DOTFILES/${BASH_REMATCH[2]}"

        # shellcheck disable=SC2088  # literal key from install.conf.yaml
        [[ "$symlink" == "~/.dotfiles" ]] && continue

        symlink_path="${symlink/#\~/$HOME}"

        # Check symlink target exists in the repo
        if [ ! -e "$target" ]; then
            fail "$symlink → source missing in repo ($target)"
            continue
        fi

        if [ ! -e "$symlink_path" ] && [ ! -L "$symlink_path" ]; then
            fail "$symlink → not found (run ./install)"
        elif [ -L "$symlink_path" ] && [ ! -e "$symlink_path" ]; then
            fail "$symlink → broken symlink"
        elif [ ! -L "$symlink_path" ]; then
            fail "$symlink → exists but is not a symlink (run ./install to replace)"
        else
            ok "$symlink"
        fi
    fi
done < "$DOTFILES/install.conf.yaml"

# Git submodules
if [ ! -f "$DOTFILES/dotbot/bin/dotbot" ]; then
    fail "dotbot submodule not initialised (run: git submodule update --init)"
else
    ok "dotbot submodule"
fi

# Orphaned dotfiles — removed from repo but may still exist on disk
ORPHANS=(.vimrc .eslintrc .sass-lint.yml .scss-lint.yml .ackrc)
for f in "${ORPHANS[@]}"; do
    [ -e "$HOME/$f" ] && warn "Orphaned: ~/$f (removed from repo, safe to delete)"
done

section_end

# ──────────────────────────────────────────────────────
# 2. BREW FORMULAE
# ──────────────────────────────────────────────────────
section_start "Brew Formulae"

if ! command -v brew &>/dev/null; then
    fail "Homebrew is not installed"
else
    BREW_INSTALLED=$(brew list --formula 2>/dev/null)
    BREW_LEAVES=$(brew leaves 2>/dev/null)
    BREW_PYTHON=$(brew list --versions python 2>/dev/null | awk 'NR == 1 {print $1}')
    if ! DECLARED=$(HOMEBREW_NO_AUTO_UPDATE=1 brew bundle list --file="$DOTFILES/Brewfile" --formula); then
        echo "Error: could not read the profile-aware Brewfile." >&2
        exit 1
    fi
    DECLARED=$(printf '%s\n' "$DECLARED" | sed 's|.*/||')

    # Declared but not installed
    while IFS= read -r formula; do
        [[ -z "$formula" ]] && continue
        [[ "$formula" == nav-pilot ]] && continue
        if echo "$BREW_INSTALLED" | grep -qx "$formula"; then
            ok "$formula"
        elif [[ "$formula" == python && -n "$BREW_PYTHON" ]] \
            && echo "$BREW_INSTALLED" | grep -qx "$BREW_PYTHON"; then
            installed_python="$BREW_PYTHON"
            ok "$formula ($installed_python)"
        else
            fail "$formula (in Brewfile but not installed)"
        fi
    done < <(printf '%s\n' "$DECLARED")

    # Installed but not declared — only check leaves (explicit installs, not dependencies)
    while IFS= read -r installed; do
        [[ -z "$installed" ]] && continue
        # Leaves from third-party taps carry a tap prefix; DECLARED has it stripped.
        installed_short="${installed##*/}"
        # Nav Pilot's optional personal installation is covered by the profile section.
        [[ "$installed_short" == nav-pilot ]] && command -v nav-pilot >/dev/null && continue
        # Homebrew resolves the unversioned python alias to a versioned formula.
        [[ "$installed" == "$BREW_PYTHON" ]] && echo "$DECLARED" | grep -qx python && continue
        if ! echo "$DECLARED" | grep -qwF "$installed_short"; then
            is_ignored "brew:$installed" && continue
            warn "$installed (installed but not in Brewfile)"
        fi
    done < <(echo "$BREW_LEAVES")
fi

section_end

section_start "Command Line Tools"
clt_finding=$(command_line_tools_outdated 2>/dev/null)
case $? in
    0) warn "$clt_finding (run dotfiles-update)" ;;
    1) ok "Command Line Tools are current" ;;
    *) warn "Could not check the Command Line Tools with Homebrew" ;;
esac
section_end

# ──────────────────────────────────────────────────────
# 3. OH-MY-ZSH PLUGINS
# ──────────────────────────────────────────────────────
section_start "Oh-my-zsh Plugins"

if [ ! -d "$HOME/.oh-my-zsh" ]; then
    fail "oh-my-zsh is not installed"
else
    PLUGINS=$(grep -E '^plugins=\(' "$DOTFILES/zsh/conf.d/omz.zsh" | sed 's/plugins=(\(.*\))/\1/' | tr ' ' '\n' | grep -v '^$')
    BUILTIN_PLUGINS=$(ls "$HOME/.oh-my-zsh/plugins/" 2>/dev/null)

    # Check each loaded plugin is actually installed
    while IFS= read -r plugin; do
        [[ -z "$plugin" ]] && continue
        if echo "$BUILTIN_PLUGINS" | grep -qx "$plugin"; then
            if [ -d "$HOME/.oh-my-zsh/custom/plugins/$plugin" ]; then
                warn "$plugin (a copy in ~/.oh-my-zsh/custom/plugins overrides the built-in one)"
            else
                ok "$plugin (built-in)"
            fi
        elif [ -d "$HOME/.oh-my-zsh/custom/plugins/$plugin" ]; then
            ok "$plugin (custom)"
        else
            fail "$plugin (in plugins=() but not installed)"
        fi
    done < <(echo "$PLUGINS")

    # Check for plugins sourced directly (e.g. fzf-tab which must load after compinit)
    SOURCED_PLUGINS=$(grep -rE "source.*oh-my-zsh/custom/plugins" "$DOTFILES/zsh/" \
        | sed 's|.*custom/plugins/\([^/]*\)/.*|\1|')
    while IFS= read -r plugin; do
        [[ -z "$plugin" ]] && continue
        if [ ! -d "$HOME/.oh-my-zsh/custom/plugins/$plugin" ]; then
            fail "$plugin (sourced in zshrc but not installed at ~/.oh-my-zsh/custom/plugins/$plugin)"
        else
            ok "$plugin (sourced directly)"
        fi
    done < <(echo "$SOURCED_PLUGINS")

    # Check for cloned custom plugins not loaded in plugins=() or sourced directly
    ALL_LOADED=$(printf '%s\n' "$PLUGINS" "$SOURCED_PLUGINS")
    if [ -d "$HOME/.oh-my-zsh/custom/plugins" ]; then
        for dir in "$HOME/.oh-my-zsh/custom/plugins"/*/; do
            plugin=$(basename "$dir")
            [[ "$plugin" == "example" ]] && continue
            if ! echo "$ALL_LOADED" | grep -qx "$plugin"; then
                warn "$plugin (cloned but not loaded in plugins=() or sourced)"
            fi
        done
    fi
fi

section_end

# ──────────────────────────────────────────────────────
# 4. GLOBAL NPM PACKAGES
# ──────────────────────────────────────────────────────
section_start "Global npm Packages"

if ! command -v npm &>/dev/null; then
    warn "npm is not available (NVM/fnm not loaded?)"
else
    NPM_GLOBALS=$(npm list -g --depth=0 2>/dev/null | grep -E '├──|└──' | sed 's/.*── //' | sed 's/@.*//')

    # corepack — bundled with Node, should always be present
    if echo "$NPM_GLOBALS" | grep -qx "corepack"; then
        ok "corepack"
    else
        warn "corepack missing"
    fi

    DECLARED_NPM=$(grep -E '^\s*npm i(nstall)? -g ' "$DOTFILES/macos/applications.sh" \
        | sed 's/.*-g //' \
        | tr ' ' '\n' \
        | sed 's/@.*//' \
        | grep -v '^$')

    while IFS= read -r pkg; do
        [[ -z "$pkg" ]] && continue
        if echo "$NPM_GLOBALS" | grep -qx "$pkg"; then
            ok "$pkg"
        else
            fail "$pkg (in applications.sh but not installed globally)"
        fi
    done < <(echo "$DECLARED_NPM")

    # Installed but not declared
    while IFS= read -r installed; do
        [[ -z "$installed" || "$installed" == "npm" || "$installed" == "corepack" || "$installed" == "bun" ]] && continue
        if ! echo "$DECLARED_NPM" | grep -qx "$installed"; then
            warn "$installed (installed globally but not in applications.sh)"
        fi
    done < <(echo "$NPM_GLOBALS")
fi

section_end

# ──────────────────────────────────────────────────────
# 5. PACKAGE MANAGER VERSIONS (corepack defaults and bun; queries the npm registry)
# ──────────────────────────────────────────────────────
section_start "Package Manager Versions"

# One major behind is informational; further behind is a warning.
report_major() {
    local subject="$1" major="$2" gap="$3" action="$4"
    if [[ "$gap" -gt 1 ]]; then
        warn "$subject is $gap majors behind $major — $action"
    else
        info "$subject: $major is a new major — $action"
    fi
}

if ! command -v node &>/dev/null; then
    warn "node is not available (fnm not loaded?)"
else
    # shellcheck source=scripts/lib/corepack-defaults.sh
    source "$DOTFILES/scripts/lib/corepack-defaults.sh"

    # install-node.sh keeps one installed version per major
    while IFS='|' read -r dup_major dup_versions; do
        [[ -z "$dup_major" ]] && continue
        warn "Node $dup_major: multiple versions installed ($dup_versions) — run: install_node $dup_major (installs the newest $dup_major.x and removes the other installed versions)"
    done < <(fnm_duplicate_majors)

    # Informational only: a project may still need an end-of-life Node.
    if end_of_life=$(fnm_end_of_life_majors); then
        while IFS='|' read -r eol_major eol_end; do
            [[ -z "$eol_major" ]] && continue
            info "Node $eol_major: end-of-life since $eol_end; keep it only while a project still needs it"
        done <<< "$end_of_life"
    else
        info "Node end-of-life dates could not be checked (offline?)"
    fi
    # corepack itself and its pnpm/yarn shims are per Node version
    while IFS= read -r inst; do
        [[ -z "$inst" ]] && continue
        node_label=$(fnm_installation_label "$inst")
        if [[ ! -x "$inst/bin/corepack" ]]; then
            warn "Node $node_label: corepack missing — run: fnm exec --using=$node_label npm install -g corepack"
        elif ! corepack_ver=$(corepack_version_for "$inst") || [[ -z "$corepack_ver" ]]; then
            warn "Node $node_label: corepack is installed but does not run"
        elif [[ ! -e "$inst/bin/pnpm" || ! -e "$inst/bin/yarn" ]]; then
            warn "Node $node_label: corepack $corepack_ver is not enabled — run: fnm exec --using=$node_label corepack enable"
        elif ! corepack_update_plan "$corepack_ver"; then
            info "Node $node_label: corepack $corepack_ver — could not check the npm registry (offline?)"
        elif [[ -n "$COREPACK_LATEST" ]]; then
            warn "Node $node_label: corepack $corepack_ver is outdated; $COREPACK_LATEST is available — run: fnm exec --using=$node_label npm install -g corepack@$COREPACK_LATEST"
        else
            ok "Node $node_label: corepack $corepack_ver"
        fi
    done < <(fnm_node_installations)

    # corepack defaults are shared by every Node version
    if command -v corepack &>/dev/null; then
        for pm in $COREPACK_DEFAULT_MANAGERS; do
            if ! corepack_default_plan "$pm"; then
                info "$pm — could not check the npm registry (offline?)"
                continue
            fi
            if [[ -z "$CP_CURRENT" ]]; then
                warn "$pm has no corepack default; corepack would fetch the latest release without a cooldown (run dotfiles-update)"
            elif [[ -n "$CP_CURRENT_AGE_HOURS" ]]; then
                warn "$pm default $CP_CURRENT was published ${CP_CURRENT_AGE_HOURS}h ago, inside the release-age cooldown"
            elif [[ -n "$CP_TARGET" ]]; then
                warn "$pm default $CP_CURRENT is outdated; $CP_TARGET is available (run dotfiles-update)"
            else
                ok "$pm $CP_CURRENT (corepack default)"
            fi
            if [[ -n "$CP_MAJOR" ]]; then
                report_major "$pm $CP_CURRENT" "$CP_MAJOR" "$CP_MAJOR_GAP" "switch manually: corepack install -g $pm@$CP_MAJOR"
            fi
        done
    fi

    while IFS='|' read -r bun_label bun_executable; do
        [[ -z "$bun_executable" ]] && continue
        if ! bun_major_plan "$bun_executable"; then
            info "bun ($bun_label) — could not check the npm registry (offline?)"
        elif [[ -n "$BUN_MAJOR" ]]; then
            report_major "bun $BUN_CURRENT ($bun_label)" "$BUN_MAJOR" "$BUN_MAJOR_GAP" "upgrade manually"
        else
            ok "bun $BUN_CURRENT ($bun_label)"
        fi
    done < <(bun_installations)
fi

section_end

# ──────────────────────────────────────────────────────
# 6. HOME DIRECTORY
# ──────────────────────────────────────────────────────
section_start "Home Directory"

# zcompdump duplicates — any ~/.zcompdump-<hostname>-<version> files mean
# ZSH_COMPDUMP isn't being honoured and two caches are being written
shopt -s nullglob
ZDUMP_EXTRAS=("$HOME"/.zcompdump-*)
shopt -u nullglob
if [ "${#ZDUMP_EXTRAS[@]}" -gt 0 ]; then
    for f in "${ZDUMP_EXTRAS[@]}"; do
        fail "Duplicate zcompdump: $(basename "$f") — run: rm '$f'"
    done
else
    ok "No duplicate zcompdump files"
fi

# NVM directory — orphan now that fnm is in use
if [ -d "$HOME/.nvm" ]; then
    # shellcheck disable=SC2088  # display text
    if command -v fnm &>/dev/null; then
        warn "~/.nvm/ exists but fnm is active — safe to remove: rm -rf ~/.nvm"
    else
        warn "~/.nvm/ exists (consider migrating to fnm)"
    fi
fi

# Untracked dotfiles — scan ~ for hidden files/dirs not managed by dotbot
# Skips: .local files (intentional per-machine overrides), known app/system items
DOTBOT_MANAGED=()
while IFS= read -r line; do
    if [[ "$line" =~ ^[[:space:]]+(~/\.[^:]+):[[:space:]] ]]; then
        DOTBOT_MANAGED+=("$(basename "${BASH_REMATCH[1]}")")
    fi
done < "$DOTFILES/install.conf.yaml"

# App- and system-generated items that are not user config
KNOWN_GENERATED=(
    # macOS system
    ".CFUserTextEncoding" ".Trash" ".DS_Store" ".localized"
    # Shell
    ".bash_history" ".zsh_history" ".zsh_sessions" ".zsh_favlist"
    ".zcompdump" ".zcompdump.zwc"
    # XDG dirs
    ".config" ".cache" ".local"
    # Common CLI tools
    ".lesshst" ".wget-hsts" ".netrc" ".gnupg"
    # macOS ships with vim — viminfo gets created on first use
    ".viminfo"
    # SSH — system-managed; dotbot links only the config inside it
    ".ssh"
    # Node — managed by this dotfiles setup
    ".oh-my-zsh" ".fnm" ".npm" ".npmrc" ".node_repl_history"
    # Bun — managed by ensure-pm-config.sh
    ".bunfig.toml" ".bun"
    # Homebrew - managed by homebrew itself
    .homebrew
    # npx skills (vercel-labs/skills) — canonical skills store
    ".agents"
)

for f in "$HOME"/.*; do
    name=$(basename "$f")
    [[ "$name" == "." || "$name" == ".." ]] && continue

    # .local / _local files are intentional per-machine overrides — skip
    [[ "$name" == *.local || "$name" == *_local || "$name" == *_local_* ]] && continue

    # Managed by dotbot — skip
    printf '%s\n' "${DOTBOT_MANAGED[@]}" | grep -qx "$name" && continue

    # Known app/system generated — skip
    printf '%s\n' "${KNOWN_GENERATED[@]}" | grep -qx "$name" && continue

    # Symlink into dotfiles repo (e.g. added manually, not via yaml) — skip
    if [ -L "$f" ]; then
        link=$(readlink "$f")
        [[ "$link" == "$DOTFILES"* ]] && continue
        is_ignored "home:$name" && continue
        warn "Untracked symlink: ~/$name → $link"
    else
        is_ignored "home:$name" && continue
        warn "Untracked dotfile: ~/$name (not managed by dotbot)"
    fi
done

section_end

# ──────────────────────────────────────────────────────
# 7. AGENT INSTRUCTIONS
# ──────────────────────────────────────────────────────
section_start "Agent instructions"
source "$DOTFILES/scripts/lib/agent-instructions.sh"
if [ ! -f "$AGENT_INSTRUCTIONS_SOURCE" ]; then
    fail "agents/AGENTS.md → source missing in repo"
else
    for entry in "${AGENT_INSTRUCTION_TARGETS[@]}"; do
        label="${entry%%:*}"
        rest="${entry#*:}"
        tool_home="${rest%:*}"
        path="$tool_home/${rest##*:}"
        display="${path/#$HOME/~}"
        case "$(agent_instruction_state "$tool_home" "$path")" in
            absent) continue ;;
            linked) ok "$label → $display" ;;
            missing|empty|stale) warn "$label → $display is not linked (run dotfiles-update)" ;;
            foreign|file) warn "$label → $display exists; move its rules into agents/AGENTS.md, then remove it" ;;
        esac
    done
    [ ${#SECTION_OUTPUT[@]} -gt 0 ] || info "No supported agent tools installed"
fi
section_end

# ──────────────────────────────────────────────────────
# 8. SKILLS (npx skills — github.com/vercel-labs/skills)
# ──────────────────────────────────────────────────────
# shellcheck source=scripts/audit-skills.sh
source "$DOTFILES/scripts/audit-skills.sh"

# ──────────────────────────────────────────────────────
# SUMMARY
# ──────────────────────────────────────────────────────
out_summary "$ISSUES" "$WARNINGS" "All good — no issues found"
echo ""

[ "$ISSUES" -gt 0 ] && exit 1
exit 0
