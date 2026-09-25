#!/usr/bin/env bash
# Usage: update.sh [--no-defaults]
#   --no-defaults  Skip running osxdefaults.sh

source "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/scripts/machine-profile.sh" || exit 1
ensure_machine_profile || exit 1
source "$HOME/.dotfiles/scripts/lib/tool-updates.sh" || exit 1

SKIP_DEFAULTS=false
for arg in "$@"; do
  [[ "$arg" == "--no-defaults" ]] && SKIP_DEFAULTS=true
done

_first_header=true
header() {
  [[ "$_first_header" == true ]] && _first_header=false || printf "\n"
  printf "\033[34m────────────────────────────────────────\033[0m\n\033[1;34m  %s\033[0m\n\033[34m────────────────────────────────────────\033[0m\n" "$1"
}
subheader() {
    printf "\n\033[1;36m  ▸ %s\033[0m\n\033[2m  ────────────────────────\033[0m\n" "$1"
}

header "System & App Store"
# Update macOS
UPDATE_OUT=$(softwareupdate -i -a 2>&1)
echo "$UPDATE_OUT"
if echo "$UPDATE_OUT" | grep -qi "restart"; then
    read -r -p "A restart is required to complete installation. Restart now? [y/N] " _response
    if [[ "$_response" =~ ^[Yy]$ ]]; then
        sudo -n true 2>/dev/null && echo "Restarting..." || { echo "Administrator access required to restart. Please enter your password:"; sudo -v; echo "Restarting..."; }
        sudo shutdown -r now
    fi
fi
# Update App Store apps
command -v mas &>/dev/null && mas upgrade

header "Node"
# Update node — install latest LTS, migrate globals if version changed
bash ~/.dotfiles/scripts/install-node.sh lts
# install-node.sh may uninstall the version this shell's fnm symlink still
# points at, leaving node/npm/npx/pnpm unresolved until the next `cd`.
# Re-point this shell to the (possibly new) default now.
command -v fnm &>/dev/null && fnm use --install-if-missing lts-latest >/dev/null
# Update npm & packages
bash ~/.dotfiles/scripts/ensure-pm-config.sh || exit 1
npm cache verify -g > /dev/null
npm install npm -g --no-fund
_npm_outdated=$(npm outdated -g 2>/dev/null || true)
[[ -n "$_npm_outdated" ]] && echo "Outdated global npm packages:" && echo "$_npm_outdated"
unset _npm_outdated
npm update -g --no-fund

header "Homebrew"
# Update Homebrew (Cask) & packages
brew analytics off
brew update --quiet
HOMEBREW_NO_ASK=1 brew bundle install --file="$HOME/.dotfiles/Brewfile" --quiet || exit 1
brew upgrade --yes || exit 1
brew cleanup

header "Zsh"
# Update Zsh plugins
ZSH=~/.oh-my-zsh DISABLE_UPDATE_PROMPT=true zsh ~/.oh-my-zsh/tools/upgrade.sh 2>&1 \
    | grep -v -E "^[[:space:]]*$|______|/ __ |/ /_/ |\\____|\\.git|ohmyzsh\.com|discord|CommitGoods|follow us|@ohmyzsh"
bash ~/.dotfiles/scripts/install-zsh-plugins.sh || exit 1

# Run settings script (skip with --no-defaults)
if [[ "$SKIP_DEFAULTS" == false ]]; then
    header "macOS defaults"
    bash ~/.dotfiles/macos/osxdefaults.sh
fi

header "Tools & integrations"
update_installed_tool claude subheader || exit 1
update_installed_tool copilot subheader || exit 1
update_installed_tool codex subheader || exit 1
update_profile_tools subheader || exit 1

header "Cleanup"
# Cleanup
rm -rf ~/.npm/_npx
pnpm store prune

# End script
echo ""
echo "Done. Enjoy your updated install."
header "Audit"
bash ~/.dotfiles/scripts/audit.sh
