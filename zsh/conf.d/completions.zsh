# The dotfiles commands, whose flags zsh completes from their --help output.
# _gnu_generic runs `<command> --help` on the first Tab, so every command listed
# here must print its help for --help without doing anything else, and list its
# flags as indented "  -x, --flag  description" lines. flag-highlight.zsh reads
# the same list.
typeset -ga dotfiles_commands=(
  agent-awake agent-sessions audit-repo clean_caches clean_docker codex-resume copy_clean
  dotfiles-audit dotfiles-defaults dotfiles-help dotfiles-install dotfiles-profile dotfiles-update
  files history-delete install_node nosync npm_globals_diff npm_release_age
  pma pmc pmh pmi pml pmr pmu prune_branches roadmaps search wipe wipe_clean
)
compdef _gnu_generic $dotfiles_commands

# Durations, shared by commands that take an age

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
compdef _dotfiles_duration_flags prune_branches audit-repo

# Help and search

_dotfiles_help() {
  if [[ $PREFIX == -* ]]; then
    _gnu_generic
    return
  fi
  local matches
  local -a candidates scope
  [[ $words[1] == pmh ]] && scope+=(--packages)
  if (( CURRENT > 2 )); then
    scope+=("${(@)words[2,CURRENT-1]}")
  fi
  matches=$(python3 "$HOME/.dotfiles/scripts/dotfiles-help.py" "${scope[@]}" --list) || return
  [[ -n $matches ]] || return 1
  candidates=("${(@f)matches}")
  _describe 'topic or command' candidates
}
compdef _dotfiles_help dotfiles-help pmh

_dotfiles_search() {
  if [[ $PREFIX == - ]]; then
    local -a flags labels
    flags=(-h -i -g -M -n --no-trim --no-clean)
    labels=(
      '-h, --help          Show help'
      '-i, --ignore-case   Match case-insensitively'
      '-g, --glob          Include or exclude paths'
      '-M, --max-columns   Snippet width; 0 shows full lines'
      '-n, --line-number   Show line numbers'
      '--no-trim           Preserve leading spaces and tabs'
      '--no-clean          Include shared clean targets'
    )
    local expl
    _wanted options expl 'search option' compadd -d labels -a flags
    return
  fi
  _arguments \
    '(-h --help)'{-h,--help}'[Show help]' \
    '(-i --ignore-case)'{-i,--ignore-case}'[Match case-insensitively]' \
    '!(-S --smart-case)'{-S,--smart-case}'[Use smart case matching]' \
    '!(-w --word-regexp)'{-w,--word-regexp}'[Match whole words]' \
    '!(-F --fixed-strings)'{-F,--fixed-strings}'[Treat the pattern literally]' \
    '*'{-g,--glob}'[Include or exclude paths]:path pattern:' \
    '!*'{-t,--type}'[Search files of this type]:file type:' \
    '!*'{-T,--type-not}'[Exclude files of this type]:file type:' \
    '!*'{-A,--after-context}'[Show following lines]:lines:' \
    '!*'{-B,--before-context}'[Show preceding lines]:lines:' \
    '!*'{-C,--context}'[Show surrounding lines]:lines:' \
    '!--hidden[Search hidden files and directories]' \
    '!(-L --follow)'{-L,--follow}'[Follow symbolic links]' \
    '(-M --max-columns)'{-M,--max-columns}'[Snippet width; 0 shows full lines]:width:' \
    '(-n --line-number)'{-n,--line-number}'[Show line numbers]' \
    '--no-trim[Preserve leading spaces and tabs]' \
    '--no-clean[Include shared clean targets]' \
    '1:search pattern:' \
    '*:path:_files'
}
compdef _dotfiles_search search

# Package managers

_dotfiles_pm_package_scripts() {
  local dir=$PWD manifest scripts
  while [[ $dir != / ]]; do
    if [[ -f $dir/package.json ]]; then
      manifest=$dir/package.json
      break
    fi
    dir=${dir:h}
  done
  [[ -n $manifest ]] || return 1

  scripts=$(python3 -I -c '
import json
import sys

with open(sys.argv[1], encoding="utf-8") as manifest:
    scripts = json.load(manifest).get("scripts", {})
if isinstance(scripts, dict):
    print("\n".join(name for name in scripts if isinstance(name, str)))
' "$manifest" 2>/dev/null) || return 1
  [[ -n $scripts ]] || return 1

  local -a candidates
  candidates=("${(@f)scripts}")
  local expl
  _wanted package-scripts expl 'package script' compadd -a candidates
}

_dotfiles_pm() {
  if (( CURRENT == 3 )) && [[ ${words[2]} == run ]]; then
    _dotfiles_pm_package_scripts || _default
  else
    _default
  fi
}
compdef _dotfiles_pm pm

# Dependency names the wrapper can act on, read from the workspace's package.json
# files without running a package manager. Skips names already on the line.
_dotfiles_pm_dependencies() {
  local output
  output=$(bash "$HOME/.dotfiles/scripts/package-manager/deps.sh" "$1" 2>/dev/null) || return 1
  [[ -n $output ]] || return 1
  local -a names typed
  names=("${(@f)output}")
  typed=("${(@)words[2,CURRENT-1]}" "${(@)words[CURRENT+1,-1]}")
  names=(${names:|typed})
  (( $#names )) || return 1
  local expl
  _wanted packages expl 'dependency' compadd -a names
}

# pmu takes one package, except with --actions, where it names an action.
_dotfiles_pmu() {
  if [[ $PREFIX != -* && ${words[CURRENT-1]} != --cooldown ]] && (( ! ${words[(I)--actions]} )); then
    local word previous=
    for word in "${(@)words[2,CURRENT-1]}"; do
      [[ $word != -* && $previous != --cooldown ]] && { _dotfiles_duration_flags; return; }
      previous=$word
    done
    _dotfiles_pm_dependencies update && return
  fi
  _dotfiles_duration_flags
}
compdef _dotfiles_pmu pmu

_dotfiles_pmr() {
  [[ $PREFIX != -* ]] && _dotfiles_pm_dependencies remove && return
  _gnu_generic
}
compdef _dotfiles_pmr pmr

_dotfiles_npm_release_age() {
  (( CURRENT == 2 )) && [[ $PREFIX != -* ]] && _dotfiles_pm_dependencies update && return
  _gnu_generic
}
compdef _dotfiles_npm_release_age npm_release_age

# Files and cleanup

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
      '(-v --verbose)'{-v,--verbose}'[Show warning paths]' \
      '--include-configured[Also query installed tools for configured global cache locations]' \
      '--limit[Maximum rows; 0 shows all]:limit:' \
      '1:report:'
  elif (( CURRENT > 2 )); then
    local -a options=(
      '(-h --help)'{-h,--help}'[Show help]'
      '(-v --verbose)'{-v,--verbose}'[Show warning paths; leftovers also includes tool-managed matches]'
      '--local-only[Exclude cloud-only files and folders]'
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
  _values -s , 'cleaner' npm pnpm brew bun yarn pip uv go-build go-modules gradle playwright xcode mise
}
_dotfiles_clean_caches() {
  _arguments \
    '(-h --help)'{-h,--help}'[Show help]' \
    '--only[Select comma-separated cleaners]:cleaners:_dotfiles_cache_names' \
    '--sort[Show largest caches first]:order:(size)' \
    '--deep[Allow expensive download and build cache cleaners]' \
    '--orphaned[Remove recognised conventional cache artifacts only when the tool is unavailable]' \
    '(-n --dry-run)'{-n,--dry-run}'[Preview without prompting or cleaning]'
}
compdef _dotfiles_clean_caches clean_caches

_dotfiles_docker_cleaners() {
  _values -s , 'cleaner' build-cache dangling-images
}
_dotfiles_clean_docker() {
  _arguments \
    '(-h --help)'{-h,--help}'[Show help]' \
    '(--inspect)--only[Select comma-separated cleaners]:cleaners:_dotfiles_docker_cleaners' \
    '(--inspect)--older-than[Retain recently used cache or recently created images]:age:_dotfiles_age' \
    '--context[Docker context]:context:' \
    '--builder[Buildx builder; defaults to context name]:builder:' \
    '(-n --dry-run --only --older-than)--inspect[Show storage and inspection guidance]' \
    '(--inspect -n --dry-run)'{-n,--dry-run}'[Show the cleanup plan without prompting or cleaning]'
}
compdef _dotfiles_clean_docker clean_docker

# Git and machine setup

_git-amend-date() {
  _arguments \
    '(-h --help)'{-h,--help}'[Show help]' \
    '--shift[Subtract from each existing date instead of now]' \
    '1:age or quoted date:_dotfiles_age' \
    '2:commit:__git_commits'
}
zstyle ':completion:*:*:git:*' user-commands 'amend-date:Change commit dates'

_dotfiles_profile() {
  _arguments \
    '(- *)--choose[Choose the profile interactively]' \
    '(- *)'{-h,--help}'[Show help]' \
    '1:machine profile:(personal work)'
}
compdef _dotfiles_profile dotfiles-profile
