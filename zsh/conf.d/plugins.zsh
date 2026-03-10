# zsh-autosuggestions — appearance and completion strategy
ZSH_AUTOSUGGEST_HIGHLIGHT_STYLE='fg=214'
ZSH_AUTOSUGGEST_STRATEGY=(history completion)

# Clear autosuggestions when pasting to avoid confusion
zstyle ':bracketed-paste-magic' active-widgets '.self-*'

# history-substring-search — bind up/down arrows
bindkey '^[[A' history-substring-search-up
bindkey '^[[B' history-substring-search-down
