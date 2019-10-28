#!/usr/bin/env bash

# Ask for the administrator password upfront
sudo -v

# Keep-alive: update existing `sudo` time stamp until the script has finished
while true; do sudo -n true; sleep 60; kill -0 "$$" || exit; done 2>/dev/null &

# Update App Store apps
softwareupdate -i -a

# Update nvm
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.35.0/install.sh | bash

# Update npm & packages
npm cache verify -g
npm install npm -g
npm update -g

# Update Homebrew (Cask) & packages
brew update
brew upgrade
brew cleanup

# Update Ruby & gems
gem update —system
gem update
gem cleanup

# Update Zsh
upgrade_oh_my_zsh

# Install or update zsh-syntax-highlighting
if [ -d ~/.oh-my-zsh/custom/plugins/zsh-syntax-highlighting ];
then
    cd ~/.oh-my-zsh/custom/plugins/zsh-syntax-highlighting && ggpull && cd ~/.dotfiles
else
    git clone https://github.com/zsh-users/zsh-syntax-highlighting.git ~/.oh-my-zsh/custom/plugins/zsh-syntax-highlighting
fi

# Install or update zsh-autosuggestions
if [ -d ~/.oh-my-zsh/custom/plugins/zsh-autosuggestions ];
then
    cd ~/.oh-my-zsh/custom/plugins/zsh-autosuggestions && ggpull && cd ~/.dotfiles
else
    git clone https://github.com/zsh-users/zsh-autosuggestions.git ~/.oh-my-zsh/custom/plugins/zsh-autosuggestions
fi

# Install or update zsh-history-substring-search
if [ -d ~/.oh-my-zsh/custom/plugins/zsh-history-substring-search ];
then
    cd ~/.oh-my-zsh/custom/plugins/zsh-history-substring-search && ggpull && cd ~/.dotfiles
else
    git clone https://github.com/zsh-users/zsh-history-substring-search.git ~/.oh-my-zsh/custom/plugins/zsh-history-substring-search
fi

# Run settings script
source ./macos/osxdefaults.sh

# End script
echo "Done. Enjoy your updated install."
