#!/usr/bin/env bash

# Ask for the administrator password upfront (if not already authenticated)
sudo -n true 2>/dev/null || { echo "Some steps require administrator access. Please enter your password:"; sudo -v; }

# Keep-alive: update existing `sudo` time stamp until the script has finished
while true; do sudo -n true; sleep 60; kill -0 "$$" || exit; done 2>/dev/null &

# Install the Command Line Tools if not already installed (opens interactive dialog on first run)
xcode-select -p &>/dev/null || xcode-select --install

# Homebrew
if ! command -v brew &>/dev/null; then
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    brew analytics off
fi

# Bootstrap brew packages from Brewfile
brew bundle install --file="$HOME/.dotfiles/Brewfile"

if ! fnm list 2>/dev/null | grep -q 'lts'; then
    eval "$(fnm env --shell bash)"
    bash ~/.dotfiles/scripts/install-node.sh lts
fi

# NPM Essentials
bash ~/.dotfiles/scripts/ensure-pm-config.sh
npm i -g diff-so-fancy@latest

# ZSH
bash ~/.dotfiles/scripts/install-zsh-plugins.sh

# End script
echo "Done. Applications are installed."
