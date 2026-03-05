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
npm cache verify -g
npm install npm -g
npm update -g

# Update Homebrew (Cask) & packages
brew analytics off
brew update
brew upgrade
brew cleanup

# Update Zsh
ZSH=~/.oh-my-zsh DISABLE_UPDATE_PROMPT=true zsh ~/.oh-my-zsh/tools/upgrade.sh

# Install or update zsh-syntax-highlighting
if [ -d ~/.oh-my-zsh/custom/plugins/zsh-syntax-highlighting ];
then
    git -C ~/.oh-my-zsh/custom/plugins/zsh-syntax-highlighting pull
else
    git clone https://github.com/zsh-users/zsh-syntax-highlighting.git ~/.oh-my-zsh/custom/plugins/zsh-syntax-highlighting
fi

# Install or update zsh-autosuggestions
if [ -d ~/.oh-my-zsh/custom/plugins/zsh-autosuggestions ];
then
    git -C ~/.oh-my-zsh/custom/plugins/zsh-autosuggestions pull
else
    git clone https://github.com/zsh-users/zsh-autosuggestions.git ~/.oh-my-zsh/custom/plugins/zsh-autosuggestions
fi

# Install or update fzf-tab
if [ -d ~/.oh-my-zsh/custom/plugins/fzf-tab ];
then
    git -C ~/.oh-my-zsh/custom/plugins/fzf-tab pull
else
    git clone https://github.com/Aloxaf/fzf-tab.git ~/.oh-my-zsh/custom/plugins/fzf-tab
fi

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
