# The dotfiles commands, whose flags zsh completes from their --help output.
# _gnu_generic runs `<command> --help` on the first Tab, so every command listed
# here must print its help for --help without doing anything else, and list its
# flags as indented "  -x, --flag  description" lines. flag-highlight.zsh reads
# the same list.
typeset -ga dotfiles_commands=(
  agent-sessions audit-repo codex-resume copy_clean
  dotfiles-audit dotfiles-defaults dotfiles-install dotfiles-profile dotfiles-update
  install_node nosync npm_globals_diff npm_release_age
  pma pmc pmi pml pmr pmu prune_branches roadmaps wipe wipe_clean
)
compdef _gnu_generic $dotfiles_commands

_dotfiles_profile() {
  _arguments \
    '(- *)--choose[Choose the profile interactively]' \
    '(- *)'{-h,--help}'[Show help]' \
    '1:machine profile:(personal work)'
}
compdef _dotfiles_profile dotfiles-profile
