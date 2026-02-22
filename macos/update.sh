#!/usr/bin/env bash

# Ask for the administrator password upfront
sudo -v

# Keep-alive: update existing `sudo` time stamp until the script has finished
while true; do sudo -n true; sleep 60; kill -0 "$$" || exit; done 2>/dev/null &

# Update App Store apps
softwareupdate -i -a

# Update node — install latest LTS, migrate globals if version changed
bash ~/.dotfiles/scripts/install-node.sh lts

# Update npm & packages
npm cache verify -g
npm install npm -g
npm update -g

# Update Homebrew (Cask) & packages
brew update
brew upgrade
brew cleanup

# Update Zsh
ZSH=~/.oh-my-zsh zsh ~/.oh-my-zsh/tools/upgrade.sh

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

# Run settings script
source ./macos/osxdefaults.sh

# End script
echo "Done. Enjoy your updated install."

bash ~/.dotfiles/scripts/audit.sh
