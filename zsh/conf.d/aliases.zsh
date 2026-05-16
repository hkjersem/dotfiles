# Disable autocorrect for package manager commands (corepack wrappers)
alias bun='nocorrect bun'
alias yarn='nocorrect corepack yarn'
alias npm='nocorrect corepack npm'
alias npx='nocorrect corepack npx'
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
