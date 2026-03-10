# Disable autocorrect for package manager commands (corepack wrappers)
alias bun='nocorrect bun'
alias yarn='nocorrect corepack yarn'
alias npm='nocorrect corepack npm'
alias pnpm='nocorrect corepack pnpm'

# Personal aliases — overrides oh-my-zsh libs, plugins, and themes
source ~/.aliases
