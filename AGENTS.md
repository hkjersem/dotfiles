# Agent Instructions

## What this repo is

A dotfiles repository managed by [dotbot](https://github.com/anishathalye/dotbot). Its purpose is to set up a macOS machine for development and general use — bootstrapping tools, shell config, git, SSH, and system preferences. Running `./install` reads `install.conf.yaml` and creates symlinks from `~` to files in this repo (e.g. `~/.zshrc` → `~/.dotfiles/zsh/zshrc`). The repo is shared across multiple macOS machines via git.

## Structure

| Path | Purpose |
|------|---------|
| `install` | Dotbot entry point — run this to apply the dotfiles |
| `install.conf.yaml` | Dotbot symlink map |
| `zsh/zshrc` | Main zsh config |
| `zsh/zlogin` | Login shell config (runs after zshrc) |
| `bash/aliases` | Shell aliases (sourced by zshrc) |
| `bash/bashrc` | Bash config |
| `git/gitconfig` | Git aliases, settings, user config |
| `git/gitignore_global` | Global gitignore |
| `ssh/config` | SSH host config |
| `scripts/audit.sh` | Read-only drift detection — compares repo declarations vs installed state |
| `audit.ignore` | Machine-local audit suppressions (gitignored) — silence known-safe warnings per machine |
| `scripts/install-node.sh` | Install a Node version via fnm, migrate globals, clean up old same-major versions |
| `scripts/npm-globals-diff.sh` | Diff global npm packages between two node versions |
| `macos/applications.sh` | Bootstrap script — installs brew formulae and tools |
| `macos/install.sh` | Full machine setup entry point (calls applications.sh etc.) |
| `macos/update.sh` | Update installed tools |
| `macos/osxdefaults.sh` | macOS system preference defaults |
| `iterm/com.googlecode.iterm2.plist` | iTerm2 preferences |
| `hushlogin` | Suppresses the "last login" message in terminal |
| `fonts.zip` | Fonts used by terminal/editor |

## Active toolchain

- **Shell**: zsh + oh-my-zsh (theme: agnoster)
- **Plugins**: zsh-syntax-highlighting, zsh-autosuggestions, history-substring-search, fzf-tab
- **Node**: fnm (Fast Node Manager) — installed via brew
- **Fuzzy finder**: fzf

## Rules

- **Never hardcode paths** — always use `$HOME` or `~`, never `/Users/<username>/...`
- **Never commit generated or cache files** — `.zcompdump`, `.zwc` and similar are machine-specific and gitignored
- **All changes must be portable** — must work on any macOS machine, not just the current one
- **When adding a new tool**: update `applications.sh` (install command), `install.conf.yaml` (symlink if needed), and add an update command to `macos/update.sh` if the tool can be updated
- **Local machine overrides** belong in `~/.zshrc_local` — this file is intentionally untracked and should not be created or modified
- **After making changes** that affect symlinks, brew formulae, plugins, or npm globals — run `bash scripts/audit.sh` to verify the repo and installed state are consistent
