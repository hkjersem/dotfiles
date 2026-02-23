#!/usr/bin/env bash

# Ask for the administrator password upfront
sudo -v

# Keep-alive: update existing `sudo` time stamp until the script has finished
while true; do sudo -n true; sleep 60; kill -0 "$$" || exit; done 2>/dev/null &

# Install the Command Line Tools if not already installed (opens interactive dialog on first run)
xcode-select -p &>/dev/null || xcode-select --install

# Homebrew
if ! command -v brew &>/dev/null; then
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    brew analytics off
fi

# Node (fnm)
brew install fnm
if ! fnm list 2>/dev/null | grep -q 'lts'; then
    eval "$(fnm env --shell bash)"
    bash ~/.dotfiles/scripts/install-node.sh lts
fi

# NPM Essentials
npm i -g diff-so-fancy@latest

# Homebrew packages
brew install fzf   # fuzzy finder
brew install bat   # better cat with syntax highlighting
brew install tree  # directory tree viewer

# ZSH
[ -d ~/.oh-my-zsh ] || git clone https://github.com/robbyrussell/oh-my-zsh.git ~/.oh-my-zsh
[ -d ~/.oh-my-zsh/custom/plugins/zsh-syntax-highlighting ] || git clone https://github.com/zsh-users/zsh-syntax-highlighting.git ~/.oh-my-zsh/custom/plugins/zsh-syntax-highlighting
[ -d ~/.oh-my-zsh/custom/plugins/zsh-autosuggestions ] || git clone https://github.com/zsh-users/zsh-autosuggestions.git ~/.oh-my-zsh/custom/plugins/zsh-autosuggestions
[ -d ~/.oh-my-zsh/custom/plugins/fzf-tab ] || git clone https://github.com/Aloxaf/fzf-tab.git ~/.oh-my-zsh/custom/plugins/fzf-tab

# End script
echo "Done. Applications are installed."
