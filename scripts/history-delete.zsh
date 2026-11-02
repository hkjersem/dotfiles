# Sourced by history-delete so the replacement history stays in the calling shell.
if [[ " $* " == *" --help "* || " $* " == *" -h "* ]]; then
  python3 "$HOME/.dotfiles/scripts/dotfiles-help.py" --render-help history-delete <<'EOF'
Usage: history-delete <event-number>

{summary}

  -h, --help  Show help

{examples}
EOF
  return $?
fi

if (( $# != 1 )) || [[ $1 != <-> || $1 == 0 ]]; then
  print -u2 -r -- 'Error: expected a positive history event number. Usage: history-delete <event-number>'
  return 2
fi
if [[ ! -o interactive ]]; then
  print -u2 -r -- 'Error: history-delete must run in an interactive zsh.'
  return 2
fi

zmodload zsh/parameter || return
local event=$1
if [[ -z ${history[$event]+present} ]]; then
  print -u2 -r -- "Error: history event $event does not exist in this shell."
  return 1
fi
if [[ -z ${HISTFILE:-} || $SAVEHIST -le 0 ]]; then
  print -u2 -r -- 'Error: saved history is not configured; nothing was deleted.'
  return 1
fi
if [[ -o histfcntl_lock ]]; then
  print -u2 -r -- 'Error: HIST_FCNTL_LOCK is not supported; nothing was deleted.'
  return 1
fi

local temporary saved_file=$HISTFILE
local saved_size=$SAVEHIST memory_size=$HISTSIZE
temporary=$(mktemp -d "${TMPDIR:-/tmp}/history-delete.XXXXXXXX") || return
{
  print -rn -- "${history[$event]}" > "$temporary/target" || return
  python3 "$HOME/.dotfiles/scripts/history-delete.py" \
    --confirm "$event" "$temporary/target"
  local confirmation=$?
  (( confirmation == 1 )) && return 0
  (( confirmation == 0 )) || return "$confirmation"
  # Include all in-memory events, not only the normal saved-history limit.
  () {
    setopt localoptions extendedhistory
    local SAVEHIST=$(( HISTSIZE + 1 ))
    builtin fc -W "$temporary/history"
  } || return
  python3 "$HOME/.dotfiles/scripts/history-delete.py" \
    "$saved_file" "$temporary/history" "$temporary/target" || return

  # A new active history list is necessary: fc -R only adds, never deletes.
  builtin fc -p "$temporary/history" "$memory_size" "$saved_size" || {
    print -u2 -r -- 'Error: saved history was updated, but this shell could not reload it.'
    return 1
  }
  HISTFILE=$saved_file
  python3 "$HOME/.dotfiles/scripts/history-delete.py" --report "$event"
} always {
  command rm -f -- "$temporary/target" "$temporary/history"
  command rmdir -- "$temporary"
}
