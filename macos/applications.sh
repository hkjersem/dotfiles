#!/usr/bin/env bash

source "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/scripts/machine-profile.sh" || exit 1
ensure_machine_profile || exit 1
source "$HOME/.dotfiles/scripts/lib/tool-updates.sh" || exit 1

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
brew bundle install --file="$HOME/.dotfiles/Brewfile" || exit 1
update_profile_tools || exit 1

if ! fnm list 2>/dev/null | grep -q 'lts'; then
    eval "$(fnm env --shell bash)"
    bash ~/.dotfiles/scripts/install-node.sh lts
fi

# NPM Essentials
bash ~/.dotfiles/scripts/ensure-pm-config.sh || exit 1
npm i -g diff-so-fancy@latest

# Agent skills
bash ~/.dotfiles/scripts/sync-agent-skills.sh || exit 1
bash ~/.dotfiles/scripts/audit-skills.sh --fix-symlinks --quiet || exit 1

# ZSH
bash ~/.dotfiles/scripts/install-zsh-plugins.sh || exit 1

# Git local profile
bash ~/.dotfiles/scripts/setup-git-local.sh || exit 1

# End script
echo "Done. Applications are installed."
