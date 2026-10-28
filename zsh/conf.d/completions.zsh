# The dotfiles commands, whose flags zsh completes from their --help output.
# _gnu_generic runs `<command> --help` on the first Tab, so every command listed
# here must print its help for --help without doing anything else, and list its
# flags as indented "  -x, --flag  description" lines. flag-highlight.zsh reads
# the same list.
typeset -ga dotfiles_commands=(
  agent-sessions audit-repo clean_caches codex-resume copy_clean
  dotfiles-audit dotfiles-defaults dotfiles-install dotfiles-profile dotfiles-update
  files install_node nosync npm_globals_diff npm_release_age
  pma pmc pmi pml pmr pmu prune_branches roadmaps wipe wipe_clean
)
compdef _gnu_generic $dotfiles_commands

_dotfiles_age() {
  local number=$PREFIX
  local -a ages
  if [[ -n $number && $number != *[^0-9]* ]]; then
    ages=("$number""m:Minutes" "$number""h:Hours" "$number""d:Days" "$number""w:Weeks")
  else
    ages=('30m:30 minutes' '1h:1 hour' '24h:24 hours' '7d:7 days'
          '14d:14 days' '30d:30 days' '90d:90 days' '2w:2 weeks')
  fi
  if [[ ${1:-} == cooldown && ( -z $PREFIX || $PREFIX == 0 ) ]]; then
    ages+=('0:Disable cooldown')
  fi
  _describe 'duration (m minutes, h hours, d days, w weeks)' ages
}

_dotfiles_duration_flags() {
  local previous=${words[CURRENT-1]}
  case "$words[1]:$previous" in
    pmu:--cooldown)
      _dotfiles_age cooldown
      return ;;
    prune_branches:--older-than|prune_branches:--stale-after|audit-repo:--since)
      _dotfiles_age
      return ;;
  esac
  if [[ $words[1] == pmu && $PREFIX == --cooldown=* ]]; then
    compset -P '*='
    _dotfiles_age cooldown
    return
  fi
  _gnu_generic
}
compdef _dotfiles_duration_flags prune_branches pmu audit-repo

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
      'caches:Show developer cache sizes'
      'leftovers:List possible agent and temporary leftovers'
    )
    _describe 'report' commands
  elif [[ $words[2] == caches ]]; then
    _arguments \
      '(-h --help)'{-h,--help}'[Show help]' \
      '--limit[Maximum rows; 0 shows all]:limit:' \
      '1:report:'
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
      changed) options+=('--since[Modified within this age]:age:_dotfiles_age') ;;
      leftovers) options+=('--older-than[No modification within this age, including descendants]:age:_dotfiles_age') ;;
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

_dotfiles_wipe_clean() {
  _arguments \
    '(-h --help)'{-h,--help}'[Show help]' \
    '(-n --dry-run)'{-n,--dry-run}'[Preview without prompting or deleting]' \
    '--sort[Preview order]:order:(path size)' \
    '--older-than[No modification within this age, including descendants]:age:_dotfiles_age'
}
compdef _dotfiles_wipe_clean wipe_clean

_dotfiles_cache_names() {
  _values -s , 'cleaner' npm pnpm brew bun yarn pip uv go-build go-modules gradle playwright xcode
}
_dotfiles_clean_caches() {
  _arguments \
    '(-h --help)'{-h,--help}'[Show help]' \
    '--only[Select comma-separated cleaners]:cleaners:_dotfiles_cache_names' \
    '--sort[Show largest caches first]:order:(size)' \
    '--deep[Allow expensive download and build cache cleaners]' \
    '(-n --dry-run)'{-n,--dry-run}'[Preview without prompting or cleaning]'
}
compdef _dotfiles_clean_caches clean_caches

_dotfiles_profile() {
  _arguments \
    '(- *)--choose[Choose the profile interactively]' \
    '(- *)'{-h,--help}'[Show help]' \
    '1:machine profile:(personal work)'
}
compdef _dotfiles_profile dotfiles-profile
