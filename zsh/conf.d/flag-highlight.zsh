# Highlight flags that a dotfiles command's --help does not list, in
# zsh-syntax-highlighting's unknown-token style. The flags of each command are
# read from its --help the first time one is checked, and cached for the shell.
# Set ZSH_HIGHLIGHT_STYLES[dotfiles-unknown-flag] to give them a style of their own.

# These pass other flags through to the package manager, so any flag may be valid.
typeset -ga dotfiles_flags_unchecked=(pmi pmr)
typeset -gA _dotfiles_known_flags

_dotfiles_help_flags() {
  local line piece MATCH MBEGIN MEND
  local -a match mbegin mend
  for line in ${(f)"$("$1" --help 2>/dev/null </dev/null)"}; do
    [[ $line =~ '^ {1,4}-' ]] || continue
    for piece in "${(@s:, :)line}"; do
      [[ $piece =~ '^ *(--?[[:alnum:]][-[:alnum:]]*)' ]] && print -r -- "$match[1]"
    done
  done
}

_dotfiles_flag_is_known() {
  local command=$1 flag=${2%%=*} letter
  (( ${+_dotfiles_known_flags[$command]} )) ||
    _dotfiles_known_flags[$command]=" ${(j: :)${(f)"$(_dotfiles_help_flags "$command")"}} "
  local known=$_dotfiles_known_flags[$command]
  # Without readable help there is nothing to check against.
  [[ -z ${known// } || $known == *" $flag "* ]] && return 0
  # The Python scripts use argparse, which accepts bundled short flags such as -rh.
  [[ ${functions[$command]} == *python3* && $flag == -[^-]?* ]] || return 1
  for letter in ${(s::)flag#-}; do
    [[ $known == *" -$letter "* ]] || return 1
  done
}

_zsh_highlight_highlighter_dotfiles_flags_predicate() {
  _zsh_highlight_buffer_modified
}

_zsh_highlight_highlighter_dotfiles_flags_paint() {
  local word command start=0 pos=1 command_position=1 MATCH MBEGIN MEND
  local -a match mbegin mend
  for word in ${(z)BUFFER}; do
    case $word in
      ';'|';;'|'&'|'&&'|'|'|'||'|'|&'|'('|')'|'{'|'}')
        command_position=1 command=
        continue ;;
    esac
    start=${BUFFER[(ib:pos:)${(b)word}]}
    pos=$(( start + $#word ))
    if (( command_position )); then
      [[ $word == nocorrect || $word == noglob || $word =~ '^[[:alpha:]_][[:alnum:]_]*=' ]] && continue
      command_position=0 command=
      (( ${dotfiles_commands[(Ie)$word]} && ! ${dotfiles_flags_unchecked[(Ie)$word]} )) && command=$word
      continue
    fi
    [[ -n $command && $word == -?* ]] || continue
    if [[ $word == -- ]]; then
      command=
    elif ! _dotfiles_flag_is_known "$command" "$word"; then
      _zsh_highlight_add_highlight $(( start - 1 )) $(( pos - 1 )) dotfiles-unknown-flag unknown-token
    fi
  done
}

if (( ${+functions[_zsh_highlight]} )); then
  ZSH_HIGHLIGHT_HIGHLIGHTERS+=(dotfiles_flags)
fi
