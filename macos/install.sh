#!/usr/bin/env bash

# Set up Dropbox file structure
if [ ! -d ~/Dropbox ]; then
    mkdir ~/Dropbox
    ln -s ~/Documents ~/Dropbox/Dokumenter
    ln -s ~/Music ~/Dropbox/Musikk
    ln -s ~/Pictures ~/Dropbox/Photos
    ln -s ~/Desktop ~/Dropbox/Skrivebord
fi

# Run install scripts
source ./macos/osxdefaults.sh
source ./macos/applications.sh

# End script
echo "Done. Enjoy your fresh install."
