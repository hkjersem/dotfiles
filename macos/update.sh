#!/usr/bin/env bash
# Usage: update.sh [--no-defaults]
#   --no-defaults  Skip running osxdefaults.sh

SKIP_DEFAULTS=false
for arg in "$@"; do
  [[ "$arg" == "--no-defaults" ]] && SKIP_DEFAULTS=true
done

# Update App Store apps
UPDATE_OUT=$(softwareupdate -i -a 2>&1)
echo "$UPDATE_OUT"
if echo "$UPDATE_OUT" | grep -qi "restart"; then
    read -r -p "A restart is required to complete installation. Restart now? [y/N] " _response
    if [[ "$_response" =~ ^[Yy]$ ]]; then
        sudo -n true 2>/dev/null && echo "Restarting..." || { echo "Administrator access required to restart. Please enter your password:"; sudo -v; echo "Restarting..."; }
        sudo shutdown -r now
    fi
fi
command -v mas &>/dev/null && mas upgrade

# Update node — install latest LTS, migrate globals if version changed
bash ~/.dotfiles/scripts/install-node.sh lts

# Update npm & packages
bash ~/.dotfiles/scripts/ensure-pm-config.sh
npm cache verify -g
npm install npm -g
npm update -g

# Update Homebrew (Cask) & packages
brew analytics off
brew update
HOMEBREW_NO_ASK=1 brew bundle install --file="$HOME/.dotfiles/Brewfile" --quiet  # ensure any new Brewfile entries are installed
brew upgrade
brew cleanup

# Update Zsh
ZSH=~/.oh-my-zsh DISABLE_UPDATE_PROMPT=true zsh ~/.oh-my-zsh/tools/upgrade.sh
bash ~/.dotfiles/scripts/install-zsh-plugins.sh

# Run settings script (skip with --no-defaults)
if [[ "$SKIP_DEFAULTS" == false ]]; then
    bash ~/.dotfiles/macos/osxdefaults.sh
fi

# Cleanup
rm -rf ~/.npm/_npx
pnpm store prune

# End script
echo "Done. Enjoy your updated install."

bash ~/.dotfiles/scripts/audit.sh
