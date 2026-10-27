#!/usr/bin/env bash
# Install or update oh-my-zsh and its custom plugins.
# Safe to run on both fresh installs and existing setups — clones if missing, pulls if present.
set -e

clone_or_pull() {
  local repo="$1" dest="$2"
  if [ -d "$dest" ]; then
    git -C "$dest" pull
  else
    git clone "$repo" "$dest"
  fi
}

[ -d ~/.oh-my-zsh ] || git clone https://github.com/robbyrussell/oh-my-zsh.git ~/.oh-my-zsh

clone_or_pull https://github.com/Aloxaf/fzf-tab.git \
  ~/.oh-my-zsh/custom/plugins/fzf-tab
