# The dotfiles commands, whose flags zsh completes from their --help output.
# _gnu_generic runs `<command> --help` on the first Tab, so every command listed
# here must print its help for --help without doing anything else, and list its
# flags as indented "  -x, --flag  description" lines. flag-highlight.zsh reads
# the same list.
typeset -ga dotfiles_commands=(
  agent-sessions audit-repo codex-resume copy_clean
  dotfiles-audit dotfiles-defaults dotfiles-install dotfiles-profile dotfiles-update
  files install_node nosync npm_globals_diff npm_release_age
  pma pmc pmi pml pmr pmu prune_branches roadmaps wipe wipe_clean
)
compdef _gnu_generic $dotfiles_commands

_dotfiles_files() {
  if (( CURRENT == 2 )) && [[ ${words[CURRENT]} != -* ]]; then
    local -a commands=(
      'count:Count files and folders'
      'large:Find large files'
      'changed:Show recently modified files'
      'sizes:Show child folder sizes'
      'empty:Find empty files and folders'
      'types:Summarise file extensions'
      'duplicates:Find duplicate content'
    )
    _describe 'report' commands
  elif (( CURRENT > 2 )); then
    local -a options=(
      '(-h --help)'{-h,--help}'[Show help]'
      '(-r --recursive)'{-r,--recursive}'[Inspect nested entries; sizes also lists nested folders]'
      '--clean[Exclude generated artifacts]'
      '--clean-git[Exclude generated artifacts and Git metadata]'
      '*--exclude[Exclude a name or relative-path glob]:glob:_files'
    )
    [[ $words[2] != count ]] && options+=('--limit[Maximum rows or groups; 0 shows all]:limit:')
    case $words[2] in
      large) options+=('--min[Minimum file size]:size:') ;;
      changed) options+=('--since[Modified within this age]:age:') ;;
      sizes) options+=(
        '--direct[Count direct files only]'
        '--logical[Count logical rather than allocated file bytes]'
        '--cached[Reuse the last size snapshot]'
      ) ;;
    esac
    _arguments $options '1:report:' '2:folder:_files -/'
  else
    _gnu_generic
  fi
}
compdef _dotfiles_files files

_dotfiles_profile() {
  _arguments \
    '(- *)--choose[Choose the profile interactively]' \
    '(- *)'{-h,--help}'[Show help]' \
    '1:machine profile:(personal work)'
}
compdef _dotfiles_profile dotfiles-profile
