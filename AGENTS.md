# Agent Instructions

## What this repo is

A dotfiles repository managed by [dotbot](https://github.com/anishathalye/dotbot). Its purpose is to set up an Apple Silicon Mac for development and general use — bootstrapping tools, shell config, git, SSH, and system preferences. Running `./install` reads `install.conf.yaml` and creates symlinks from `~` to files in this repo (e.g. `~/.zshrc` → `~/.dotfiles/zsh/zshrc`). The repo is shared across multiple macOS machines via git.

## Structure

| Path | Purpose |
|------|---------|
| `install` | Dotbot entry point — run this to apply the dotfiles |
| `install.conf.yaml` | Dotbot symlink map |
| `zsh/zshrc` | Main zsh config — sources conf.d modules, installer-managed tool blocks, and local overrides |
| `zsh/conf.d/` | Zsh config modules (sourced in order by zshrc): `omz.zsh`, `plugins.zsh`, `history.zsh`, `env.zsh`, `aliases.zsh`, `prompt.zsh`, `fnm.zsh`, `fzf.zsh` |
| `zsh/zlogin` | Login shell config (runs after zshrc) — compiles compdump, propagates PATH to launchd |
| `Brewfile` | Homebrew formula/cask declarations — source of truth for brew packages; used by `applications.sh` and `update.sh` via `brew bundle --file` |
| `scripts/machine-profile.sh` | Read, choose, or change the machine-local personal/work profile; also sourced by setup, updates, and audit |
| `scripts/lib/tool-updates.sh` | Shared custom update/sync routines for installed tools on either profile; standard Homebrew tools only need a Brewfile declaration |
| `bash/aliases` | Shell aliases (sourced by zshrc) |
| `bash/bashrc` | Bash config |
| `bash/inputrc` | Readline config |
| `git/gitconfig` | Git aliases, settings, user config — global default email is personal; machine-local overrides via `~/.gitconfig_local` |
| `git/gitignore_global` | Global gitignore |
| `ssh/config` | SSH host config |
| `scripts/setup-git-local.sh` | Interactive setup for machine-local git identity — generates `~/.gitconfig_local`, `~/.gitconfig_local_work`, `~/.gitconfig_local_personal` (all untracked). Called by `applications.sh` during install. |
| `scripts/install-zsh-plugins.sh` | Clone or pull oh-my-zsh and custom plugins — called by both `applications.sh` and `update.sh` |
| `scripts/audit.sh` | Read-only drift detection — compares repo declarations vs installed state |
| `scripts/audit-repo.sh` | Repository triage — Git, file, test, and build indicators; extra checks for a root `package.json`. Aliased as `audit-repo`. npm/pnpm audit and outdated queries can use the network; `--skip-security` skips only audit. See [README](README.md#repository-triage). |
| `audit.ignore` | Machine-local audit suppressions (gitignored) — silence known-safe warnings per machine |
| `scripts/ensure-pm-config.sh` | Ensures package manager release-age cooldowns are set globally — `min-release-age` in `~/.npmrc`, pnpm `minimumReleaseAge` (if pnpm installed), and `minimumReleaseAge` in `~/.bunfig.toml [install]` (if bun installed) |
| `scripts/install-node.sh` | Install a Node version via fnm, migrate globals, clean up old same-major versions |
| `scripts/npm-globals-diff.sh` | Diff global npm packages between two node versions |
| `scripts/npm-release-age.sh` | Check days since release for an npm package or version |
| `scripts/package-manager/` | Package manager scripts — invoked via `pm`, `pmx`, `pmu`, `pmi`, `pmr`, `pmc`, `pma` shell commands |
| `scripts/package-manager/_detect-pm.sh` | Sourced by `run.sh` and `bash/aliases` — detects bun, pnpm or npm for the current project |
| `scripts/package-manager/audit.sh` | Audit/fix wrapper — `pma`; pnpm uses `--fix` on v10 and `--fix=update` on v11+, and only does a deep lockfile refresh with `--deep` |
| `scripts/package-manager/clean.sh` | Cleanup wrapper — removes unused default pnpm catalog entries when no named catalogs exist, removes empty manifest sections, and reports pnpm catalog suggestions |
| `scripts/package-manager/install.sh` | Install wrapper — runs npm/bun installs directly and makes `pmi` workspace-aware for pnpm roots; unknown packages fall back to root add and catalog rewrites only happen on explicit versions |
| `scripts/package-manager/remove.sh` | Remove wrapper — runs npm/bun removals directly and makes `pmr` workspace-aware for pnpm roots, erroring on ambiguous declarations instead of guessing |
| `scripts/package-manager/run.sh` | Entry point — auto-detects bun, pnpm or npm from `packageManager` field / lockfile; dispatches to the appropriate update script |
| `scripts/package-manager/_update-lib.sh` | Shared logic: semver, cooldown, display, write-back, workspace scanning |
| `scripts/package-manager/pnpm-update.sh` | pnpm wrapper — catalog read/write, `pnpm outdated`, augmentation |
| `scripts/package-manager/npm-update.sh` | npm wrapper — workspace detection, `npm outdated`, augmentation, reads cooldown from project and global `~/.npmrc` |
| `scripts/package-manager/bun-update.sh` | bun wrapper — `bun outdated`, augmentation, reads cooldown from `bunfig.toml` or `~/.bunfig.toml` |
| `scripts/tests/` | Isolated shell-script regression tests — run with `python3 -B -m unittest discover -s scripts/tests` |
| `scripts/lib/clean-targets.sh` | Shared generated-artifact lists and matching patterns for cleanup, clean-copy, and repository-audit scripts |
| `scripts/wipe-clean.sh` | Delete all known build artifacts and dependency folders from the current directory. Aliased as `wipe_clean` |
| `scripts/copy-clean.sh` | Clone-copy or ZIP a source directory, defaulting to the current directory, excluding the same artifacts as `wipe_clean`; supports `--ignore-git` and `--keep-name`. Aliased as `copy_clean` |
| `scripts/lib/pm-utils.sh` | Shared package-manager duration and configuration parsing helpers, also used by repository audit |
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

- **Symlinks are live** — editing a linked file in this checkout changes the machine's configuration before staging or committing. Run Bash entry points with `bash`, not `source` from zsh.
- **Never hardcode paths** — always use `$HOME` or `~`, never `/Users/<username>/...`
- **Never commit generated or cache files** — `.zcompdump`, `.zwc` and similar are machine-specific and gitignored
- **All changes must be portable** — must work across Apple Silicon Macs, not just the current machine. Intel support is out of scope. Bash scripts must work with macOS's bundled Bash 3.2.
- **When adding a new zsh config** — add it to a file in `zsh/conf.d/` and add a `source` line in `zsh/zshrc`
- **When adding a new brew tool**: add it to `Brewfile` — this is the single source of truth for brew packages. `applications.sh` and `update.sh` both use `brew bundle` to install from it
- **Machine profile** lives in `~/.config/dotfiles/profile`, never in Git. Load it through `scripts/machine-profile.sh`; conditional Brewfile entries use the exported `DOTFILES_PROFILE` for automatic installation only. Update and sync installed tools on either profile, regardless of their intended profile; never uninstall them automatically.
- **Local machine overrides** belong in `~/.zshrc_local` (shell) and `~/.gitconfig_local` (git) — these files are intentionally untracked. Run `scripts/setup-git-local.sh` to generate the git local config interactively.
- **Git identity is directory-based** — global default is the personal email; `~/.gitconfig_local` applies work identity under the work directory, with optional personal exceptions. Never hardcode emails in tracked files — `setup-git-local.sh` prompts for work email and reads the personal identity from the effective global Git configuration (`git config --global user.email` and `user.name`).
- **After making changes** that affect symlinks, brew formulae, plugins, or npm globals — run `bash scripts/audit.sh` to verify the repo and installed state are consistent
- **To suppress audit warnings** — add entries to `audit.ignore` with format `category:name` (e.g. `brew:tool`, `home:.config/dir`). This file is gitignored and machine-specific.

## Testing

- See [README testing instructions](README.md#testing) for prerequisites and suite commands. Run relevant suites from the repository root; shell regression tests must exercise `/bin/bash`, not only a newer Homebrew Bash.
- For configuration-writing tests, isolate `HOME`, XDG configuration, and Git configuration as needed. Mock package-manager/network calls and destructive operations; do not use live setup, update, defaults, or cleanup commands as automated test runners.
- Keep success, error, and cancellation cases together. A sandbox- or PTY-related skip is an unverified case, not a passing check; report it without bypassing restrictions.
