# Set up fzf key bindings and fuzzy completion
source <(fzf --zsh)

export FZF_DEFAULT_OPTS='--no-height --no-reverse --inline-info'
export FZF_DEFAULT_OPTS=$FZF_DEFAULT_OPTS'
  --color=fg:-1,fg+:#d0d0d0,bg:-1,bg+:-1,gutter:-1
  --color=hl:#63b05f,hl+:#5dc258,info:#afaf87,marker:#87ff00
  --color=prompt:#afaf87,spinner:#af5fff,pointer:#af5fff,header:#87afaf
  --color=border:#262626,label:#aeaeae,query:#d9d9d9
  --color hl:underline,hl+:underline
  --color=fg+:bold,hl+:bold
  --color=fg:regular,hl:regular
  --border="none" --border-label="" --preview-window="border-none"
   --prompt="" --marker="" --pointer="" --separator="" --scrollbar=""'

export FZF_CTRL_T_OPTS="
  --walker-skip .git,node_modules,target
  --select-1 --exit-0
  --preview 'bat -n --color=always {}'
  --bind 'ctrl-/:change-preview-window(down|right|)'
  --bind '?:toggle-preview'"

export FZF_ALT_C_OPTS="
  --walker-skip .git,node_modules,target
  --select-1 --exit-0
  --preview 'tree -C {}'
  --preview-window hidden
  --bind 'ctrl-/:change-preview-window(down|right|)'
  --bind '?:toggle-preview'"

# Fix ALT-C command for listing dirs (or just use CTRL-T to list files and dirs)
bindkey "ç" fzf-cd-widget

# fzf-tab — must be sourced after compinit (handled by omz) and after fzf
source ~/.oh-my-zsh/custom/plugins/fzf-tab/fzf-tab.plugin.zsh
zstyle ':fzf-tab:*' use-fzf-default-opts yes
zstyle ':fzf-tab:*' fzf-flags --layout=default --height=100%
