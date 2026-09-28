# Disable autocorrect for package manager commands (corepack wrappers)
alias bun='nocorrect bun'
alias yarn='nocorrect corepack yarn'
# Only route npm through corepack when a project pins a package manager;
# otherwise use the npm bundled with the active fnm Node version.
_npm_via_corepack_if_pinned() {
  local dir=$PWD
  while :; do
    if [[ -f $dir/package.json ]] && command grep -q '"packageManager"' "$dir/package.json"; then
      corepack "$@"
      return
    fi
    [[ $dir == / ]] && break
    dir=${dir:h}
  done
  command "$@"
}
alias npm='nocorrect _npm_via_corepack_if_pinned npm'
alias npx='nocorrect _npm_via_corepack_if_pinned npx'
alias pnpm='nocorrect corepack pnpm'
alias pnpx='nocorrect corepack pnpx'

# Personal aliases — overrides oh-my-zsh libs, plugins, and themes
source ~/.aliases

# Disable autocorrect for pm wrapper functions (must be after source ~/.aliases)
alias pm='nocorrect pm'
alias pmx='nocorrect pmx'
alias pmi='nocorrect pmi'
alias pmr='nocorrect pmr'
alias pmu='nocorrect pmu'
