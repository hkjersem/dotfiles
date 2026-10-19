brew "bat"             # better cat with syntax highlighting
brew "coreutils"       # GNU date, sed, etc. (gdate, gsed, ...)
brew "fnm"             # Fast Node Manager
brew "fzf"             # fuzzy finder
brew "gh"              # GitHub CLI — PRs, issues, Actions; also the Git credential helper for github.com
brew "git-filter-repo" # rewrite git history (remove secrets, large files, change authors)
brew "jq"              # JSON processor
brew "mas"             # Mac App Store CLI
brew "poppler"         # PDF tools such as pdftotext and pdfinfo, useful for agent workflows
brew "python"          # latest Python 3 (replaces python.org installers); used to run the repo's tests
brew "ripgrep"         # fast recursive search (rg) — respects .gitignore; used by fzf and AI agents
brew "shellcheck"      # static analysis for shell scripts
brew "tree"            # directory tree viewer

brew "navikt/tap/nav-pilot" if ENV["DOTFILES_PROFILE"] == "work"

if ENV["DOTFILES_PROFILE"] == "work"
  # Keep an existing SDK instead of installing a second copy.
  gcloud_installed = ENV.fetch("PATH", "").split(File::PATH_SEPARATOR).any? do |directory|
    executable = File.join(directory, "gcloud")
    File.file?(executable) && File.executable?(executable)
  end
  cask "gcloud-cli" unless gcloud_installed
end
