#!/usr/bin/env bash

# Ask for the administrator password upfront
sudo -v

# Keep-alive: update existing `sudo` time stamp until the script has finished
while true; do sudo -n true; sleep 60; kill -0 "$$" || exit; done 2>/dev/null &

###############################################################################
# Update OSX                                                                  #
###############################################################################

# Install the Command Line Tools (so it works without Xcode installed)
xcode-select --install

# Install software updates
# softwareupdate -i -a

# Wait for install before we continue
# echo "Press [Enter] key after Command Line Tools are installed..."
# read -s

###############################################################################
# NVM                                                                         #
###############################################################################

if [ ! -d ~/.nvm ];
then
    curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.35.0/install.sh | bash
    export NVM_DIR="$HOME/.nvm"
    [ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"
fi

if [ ! -d ~/.nvm/versions ];
then
    nvm install node
    nvm use node
    # nvm alias default node
    # cd ~/ && ln -sf $(npm get prefix) .nvm_default
fi

# NPM Essentials
npm install -g eslint
npm install -g diff-so-fancy

###############################################################################
# Homebrew and Homebrew Cask                                                  #
###############################################################################

yes '' | ruby -e "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/master/install)"
brew tap caskroom/cask
brew install brew-cask
brew tap caskroom/versions

###############################################################################
# Install Applications                                                        #
###############################################################################

# Essentials
brew cask install dropbox
# brew cask install evernote
# brew cask install spotify
brew cask install tidal

# Browsers
# brew cask install google-chrome
# brew cask install google-chrome-canary
# brew cask install firefox
# brew cask install firefoxdeveloperedition
# brew cask install safari-technology-preview

# Utils
# brew cask install the-unarchiver
# brew cask install spectacle
# brew cask install viscosity
# brew cask install clipmenu
# brew cask install ccmenu

# Coding
# brew cask install iterm2
# brew install tmux
# brew install z
# brew cask install visual-studio-code
# brew cask install atom
# brew cask install sublime-text
# brew cask install cyberduck
# brew cask install docker
# brew cask install kitematic
# brew cask install mamp
# brew cask install sequel-pro
# brew cask install sourcetree
# brew cask install virtualbox
# brew cask install xcode
# brew cask install android-studio
# brew cask install genymotion

# Design
# brew cask install sketch
# brew cask install sketch-beta
# brew cask install sketch-toolbox
# brew cask install nudgit
# brew cask install craftmanager
# brew cask install sketch-runner
# brew cask install adobe-photoshop-cc

# Misc
# brew cask install reeder
# brew cask install tweetbot
# brew cask install slack
# brew cask install transmission
# brew cask install vlc
# brew cask install skype
# brew cask install skype-for-business
# brew cask install microsoft-office

# ZSH
[ -d ~/.oh-my-zsh ] || git clone https://github.com/robbyrussell/oh-my-zsh.git ~/.oh-my-zsh
[ -d ~/.oh-my-zsh/custom/plugins/zsh-syntax-highlighting ] || git clone https://github.com/zsh-users/zsh-syntax-highlighting.git ~/.oh-my-zsh/custom/plugins/zsh-syntax-highlighting

###############################################################################
# End script                                                                  #
###############################################################################

echo "Done. Applications are installed."
