brew "bat"             # better cat with syntax highlighting
brew "coreutils"       # GNU date, sed, etc. (gdate, gsed, ...)
brew "fnm"             # Fast Node Manager
brew "fzf"             # fuzzy finder
brew "git-filter-repo" # rewrite git history (remove secrets, large files, change authors)
brew "jq"              # JSON processor
brew "mas"             # Mac App Store CLI
brew "ripgrep"         # fast recursive search (rg) — respects .gitignore; used by fzf and AI agents
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
