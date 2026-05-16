# Show time instead of username in prompt (when not SSH and not root)
prompt_context() {
  if [[ "$USER" != "$DEFAULT_USER" || -n "$SSH_CLIENT" ]]; then
    prompt_segment black 15 "%(!.%{%F{yellow}%}.)%T"
  fi
}
