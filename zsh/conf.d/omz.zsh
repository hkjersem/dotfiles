# Path to your oh-my-zsh installation.
export ZSH=$HOME/.oh-my-zsh

# Set the theme.
ZSH_THEME="agnoster"

# Change how often to auto-update (in days).
export UPDATE_ZSH_DAYS=100

# Enable command auto-correction.
ENABLE_CORRECTION="true"

# Display red dots whilst waiting for completion.
COMPLETION_WAITING_DOTS="true"

# Load plugins
plugins=(git zsh-syntax-highlighting zsh-autosuggestions history-substring-search)

export ZSH_COMPDUMP="$HOME/.zcompdump"
export ZSH_DISABLE_COMPFIX=true

# Intercept omz's compinit call: use -C (skip compaudit) if dump is from today.
# This ensures fpath is complete (omz adds plugins before calling compinit)
# and avoids a redundant second compinit call.
autoload -Uz compinit
function compinit() {
    unfunction compinit
    autoload -Uz compinit
    if [[ $(date +'%j') == $(stat -f '%Sm' -t '%j' "${ZSH_COMPDUMP}" 2>/dev/null) ]]; then
        compinit -C -d "${ZSH_COMPDUMP}"
    else
        compinit -d "${ZSH_COMPDUMP}"
    fi
}

source $ZSH/oh-my-zsh.sh
