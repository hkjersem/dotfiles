#!/usr/bin/env bash
#
# Dotfiles audit script — read-only drift detection.
# Compares what the repo declares against what's actually installed on this machine.

DOTFILES="$HOME/.dotfiles"

BOLD=$'\033[1m'
RED=$'\033[0;31m'
YELLOW=$'\033[1;33m'
GREEN=$'\033[0;32m'
RESET=$'\033[0m'

ISSUES=0
WARNINGS=0

# Section buffering — collapse to one line if all green
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
    echo ""
    echo "${BOLD}── ${CURRENT_SECTION} ──────────────────────────────────${RESET}"
    if [[ $SECTION_ISSUES -eq 0 && $SECTION_WARNINGS -eq 0 && ${#SECTION_OUTPUT[@]} -gt 0 ]]; then
        echo "  ${GREEN}✅${RESET} All checks passed"
    else
        printf '%s\n' "${SECTION_OUTPUT[@]}"
    fi
}

ok()   { SECTION_OUTPUT+=("  ${GREEN}✅${RESET} $1"); }
warn() { SECTION_OUTPUT+=("  ${YELLOW}🟡${RESET} $1"); ((WARNINGS++)); ((SECTION_WARNINGS++)); }
fail() { SECTION_OUTPUT+=("  ${RED}🔴${RESET} $1"); ((ISSUES++)); ((SECTION_ISSUES++)); }

# ──────────────────────────────────────────────────────
# 1. SYMLINKS
# ──────────────────────────────────────────────────────
section_start "Symlinks"

while IFS= read -r line; do
    if [[ "$line" =~ ^[[:space:]]+(~/\.[^:]+):[[:space:]]*(.+)$ ]]; then
        symlink="${BASH_REMATCH[1]}"
        target="$DOTFILES/${BASH_REMATCH[2]}"

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
    DECLARED=$(grep -E '^\s*brew install ' "$DOTFILES/macos/applications.sh" | awk '{print $3}' | sed 's/--[a-z-]*//g' | xargs)

    # Declared but not installed
    while IFS= read -r formula; do
        [[ -z "$formula" ]] && continue
        if echo "$BREW_INSTALLED" | grep -qx "$formula"; then
            ok "$formula"
        else
            fail "$formula (in applications.sh but not installed)"
        fi
    done < <(echo "$DECLARED" | tr ' ' '\n')

    # Installed but not declared — only check leaves (explicit installs, not dependencies)
    while IFS= read -r installed; do
        [[ -z "$installed" ]] && continue
        if ! echo "$DECLARED" | grep -qw "$installed"; then
            warn "$installed (installed but not in applications.sh)"
        fi
    done < <(echo "$BREW_LEAVES")
fi

section_end

# ──────────────────────────────────────────────────────
# 3. OH-MY-ZSH PLUGINS
# ──────────────────────────────────────────────────────
section_start "Oh-my-zsh Plugins"

if [ ! -d "$HOME/.oh-my-zsh" ]; then
    fail "oh-my-zsh is not installed"
else
    PLUGINS=$(grep -E '^plugins=\(' "$DOTFILES/zsh/zshrc" | sed 's/plugins=(\(.*\))/\1/' | tr ' ' '\n' | grep -v '^$')
    BUILTIN_PLUGINS=$(ls "$HOME/.oh-my-zsh/plugins/" 2>/dev/null)

    # Check each loaded plugin is actually installed
    while IFS= read -r plugin; do
        [[ -z "$plugin" ]] && continue
        if echo "$BUILTIN_PLUGINS" | grep -qx "$plugin"; then
            ok "$plugin (built-in)"
        elif [ -d "$HOME/.oh-my-zsh/custom/plugins/$plugin" ]; then
            ok "$plugin (custom)"
        else
            fail "$plugin (in plugins=() but not installed)"
        fi
    done < <(echo "$PLUGINS")

    # Check for plugins sourced directly (e.g. fzf-tab which must load after compinit)
    SOURCED_PLUGINS=$(grep -E "source.*oh-my-zsh/custom/plugins" "$DOTFILES/zsh/zshrc" \
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
        [[ -z "$installed" || "$installed" == "npm" ]] && continue
        if ! echo "$DECLARED_NPM" | grep -qx "$installed"; then
            warn "$installed (installed globally but not in applications.sh)"
        fi
    done < <(echo "$NPM_GLOBALS")
fi

section_end

# ──────────────────────────────────────────────────────
# 5. HOME DIRECTORY
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

# Vim artifacts (vimrc removed from repo, but these may linger)
[ -e "$HOME/.viminfo" ] && warn "~/.viminfo exists (vim artifact, safe to delete)"
[ -d "$HOME/.vim" ]     && warn "~/.vim/ directory exists (vim artifact, safe to delete)"

# NVM directory — orphan now that fnm is in use
if [ -d "$HOME/.nvm" ]; then
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
    ".bash_history" ".zsh_history" ".zsh_sessions" ".zsh_favlist"
    ".CFUserTextEncoding" ".Trash" ".DS_Store" ".localized"
    ".config" ".cache" ".local"
    ".oh-my-zsh" ".nvm" ".npm" ".node_repl_history"
    ".lesshst" ".wget-hsts" ".netrc"
    ".vscode" ".docker" ".kube" ".gnupg"
    ".colima" ".lima" ".orbstack"
    ".gem" ".bundle" ".rbenv" ".pyenv"
    ".cargo" ".rustup"
    ".asdf" ".fnm" ".bun"
    ".aws" ".azure" ".gcloud" ".boto"
    ".cocoapods" ".gradle" ".m2"
    ".gitk" ".cups"
    ".zcompdump" ".zcompdump.zwc"
    ".ssh"        # directory is system-managed; dotbot links the config inside it
    ".viminfo" ".vim"  # already caught by specific check above
    # AI coding assistants — app-managed, not user config
    ".agents" ".claude" ".cline" ".codebuddy" ".codeium" ".codemod" ".codex"
    ".commandcode" ".continue" ".copilot" ".copilot_here.sh" ".cursor"
    ".enonic" ".factory" ".gemini" ".junie" ".kilocode" ".kiro" ".kode"
    ".mcpjam" ".moltbot" ".neovate" ".openhands" ".pi" ".pochi"
    ".qoder" ".qwen" ".roo" ".trae" ".zencoder"
    # Local AI/ML tools
    ".lmstudio" ".lmstudio-home-pointer" ".ollama"
)

for f in "$HOME"/.*; do
    name=$(basename "$f")
    [[ "$name" == "." || "$name" == ".." ]] && continue

    # .local files are intentional per-machine overrides — skip
    [[ "$name" == *.local ]] && continue

    # Managed by dotbot — skip
    printf '%s\n' "${DOTBOT_MANAGED[@]}" | grep -qx "$name" && continue

    # Known app/system generated — skip
    printf '%s\n' "${KNOWN_GENERATED[@]}" | grep -qx "$name" && continue

    # Symlink into dotfiles repo (e.g. added manually, not via yaml) — skip
    if [ -L "$f" ]; then
        link=$(readlink "$f")
        [[ "$link" == "$DOTFILES"* ]] && continue
        warn "Untracked symlink: ~/$name → $link"
    else
        warn "Untracked dotfile: ~/$name (not managed by dotbot)"
    fi
done

section_end

# ──────────────────────────────────────────────────────
# SUMMARY
# ──────────────────────────────────────────────────────
echo ""
echo "${BOLD}────────────────────────────────────────${RESET}"
if [ "$ISSUES" -eq 0 ] && [ "$WARNINGS" -eq 0 ]; then
    echo "  ✅  All good — no issues found"
else
    [ "$ISSUES" -gt 0 ]   && echo "  ${RED}🔴  $ISSUES issue(s) found${RESET}"
    [ "$WARNINGS" -gt 0 ] && echo "  ${YELLOW}🟡  $WARNINGS warning(s) found${RESET}"
fi
echo "${BOLD}────────────────────────────────────────${RESET}"
echo ""

[ "$ISSUES" -gt 0 ] && exit 1
exit 0
