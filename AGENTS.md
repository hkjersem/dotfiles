# Agent Instructions

## What this repo is

A dotfiles repository managed by [dotbot](https://github.com/anishathalye/dotbot). Its purpose is to set up an Apple Silicon Mac for development and general use — bootstrapping tools, shell config, git, SSH, and system preferences. Running `./install` reads `install.conf.yaml` and creates symlinks from `~` to files in this repo (e.g. `~/.zshrc` → `~/.dotfiles/zsh/zshrc`). The repo is shared across multiple macOS machines via git.

## Structure

| Path | Purpose |
|------|---------|
| `install` | Dotbot entry point — run this to apply the dotfiles |
| `install.conf.yaml` | Dotbot symlink map |
| `zsh/zshrc` | Main zsh config — sources conf.d modules, installer-managed tool blocks, and local overrides |
| `zsh/conf.d/` | Zsh config modules (sourced in order by zshrc): `omz.zsh`, `plugins.zsh`, `history.zsh`, `env.zsh`, `aliases.zsh`, `prompt.zsh`, `fnm.zsh`, `fzf.zsh`, `agent-sessions.zsh` |
| `zsh/zlogin` | Login shell config (runs after zshrc) — compiles compdump, propagates PATH to launchd |
| `Brewfile` | Homebrew formula/cask declarations — source of truth for brew packages; used by `applications.sh` and `update.sh` via `brew bundle --file` |
| `scripts/machine-profile.sh` | Read, choose, or change the machine-local personal/work profile; also sourced by setup, updates, and audit |
| `scripts/codex-resume.py` | Send `continue` or a custom message to a Codex desktop chat on this Mac now, at a time or at the next usage-limit reset (optionally earlier when periodic checks find an early reset), in a chosen chat, a project's latest chat or the latest chat, through the Codex queue via the app server and a user LaunchAgent; `--recurring` adds a second LaunchAgent that resumes chats a usage limit stopped; invoked by the `codex-resume` shell function |
| `docs/codex-resume.md` | Full `codex-resume` usage: scheduling, chat targeting, queue delivery, `--recurring`, requirements; the README keeps a short summary |
| `scripts/roadmaps.py` | Read-only summary of due, awaiting-acceptance and in-progress items across roadmap-skill roadmaps listed in machine-local `~/.config/dotfiles/roadmap-paths`; imports the skill's `roadmap.py`. Invoked by the `roadmaps` shell function |
| `docs/` (other pages) | Detailed reference that would make the README too long: `audit-repo.md`, `prune-branches.md`, `package-managers.md`, `agent-skills.md`. The README keeps a summary of each and links to it; update both when behaviour changes |
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
| `scripts/audit-repo.sh` | Repository triage — Git, stale-branch, file, documentation-naming, test, and build indicators; extra checks for a root `package.json`. Aliased as `audit-repo`. npm/pnpm audit and outdated queries can use the network; `--skip-security` skips only audit. See [docs/audit-repo.md](docs/audit-repo.md). |
| `scripts/audit-skills.sh` | Skills drift detection — read-only under `audit.sh`; explicit repair flags preserve real content and foreign links. `--clean-stale` removes only verified managed links and empty skills-only agent directories. See [docs/agent-skills.md](docs/agent-skills.md). |
| `agents/AGENTS.md` | Personal global agent instructions — symlinked into each installed tool's global instruction file |
| `scripts/sync-agent-instructions.sh` | Links `agents/AGENTS.md` for Claude Code, Codex and Copilot CLI when their home directory exists; existing content is a conflict and never overwritten. Run by setup and update, checked by `audit.sh` |
| `scripts/lib/agent-instructions.sh` | Shared target list and link-state detection for the instruction sync and audit |
| `agents/skills/` | Skills authored in this repository — synced into `~/.agents/skills/` and then linked to installed agent providers |
| `agents/skills/skills.txt` | Third-party global skills installed on every machine; machine-specific skills stay local and unlisted |
| `agents/skills/sources.txt` | Optional `~/path` directories owned by other repositories (e.g. Minne); each `*/SKILL.md` is linked like a repo-authored skill, and missing directories are skipped |
| `scripts/sync-agent-skills.sh` | Collision-safe agent skill sync — links repo-authored skills, unlinks removed ones, and installs missing entries from `agents/skills/skills.txt` without overwriting existing names |
| `scripts/lib/skills-utils.sh` | Shared skills name/manifest validation and conservative link/cleanup helpers |
| `scripts/ensure-pm-config.sh` | Ensures package manager release-age cooldowns are set globally — `min-release-age` in `~/.npmrc`, pnpm `minimumReleaseAge` (if pnpm installed), and `minimumReleaseAge` in `~/.bunfig.toml [install]` (if bun installed) |
| `scripts/corepack-defaults.sh` | Updates corepack's global pnpm/Yarn defaults within their current major, honouring the `~/.npmrc` release-age cooldown; new pnpm/Yarn/Bun majors are only reported (warning when >1 major behind), and Bun is checked in every fnm Node version. Called by `update.sh`; `audit.sh` reports the same state, plus per-Node corepack presence and version and a warning for Node majors with more than one installed version, and a note for end-of-life Node majors, via `scripts/lib/corepack-defaults.sh` (queries the npm registry and Node's release schedule) |
| `scripts/install-node.sh` | Install a Node version via fnm, migrate globals (except npm/corepack), install corepack when not bundled (Node 25+), enable it, clean up old same-major versions |
| `scripts/update-node-versions.sh` | Update every other installed Node major to its newest patch, then give each fnm installation the newest cooldown-eligible npm its Node supports and update its globals; skips global updates where npm predates `min-release-age`. Called by `update.sh` |
| `scripts/npm-globals-diff.sh` | Diff global npm packages between two node versions |
| `scripts/npm-release-age.sh` | Check days since release for an npm package or version |
| `scripts/package-manager/` | Package manager scripts — invoked via `pm`, `pmx`, `pmu`, `pmi`, `pmr`, `pml`, `pmc`, `pma` shell commands |
| `scripts/package-manager/_detect-pm.sh` | Sourced by `run.sh` and `bash/aliases` — detects bun, pnpm or npm for the current project |
| `scripts/package-manager/audit.sh` | Audit/fix wrapper — `pma`; pnpm uses `--fix` on v10 and `--fix=update` on v11+, and only does a deep lockfile refresh with `--deep` |
| `scripts/package-manager/clean.sh` | Cleanup wrapper — removes unused default pnpm catalog entries when no named catalogs exist, removes empty manifest sections, and reports pnpm catalog suggestions |
| `scripts/package-manager/install.sh` | Install wrapper — runs npm/bun installs directly and makes `pmi` workspace-aware for pnpm roots; unknown packages fall back to root add and catalog rewrites only happen on explicit versions |
| `scripts/package-manager/remove.sh` | Remove wrapper — runs npm/bun removals directly and makes `pmr` workspace-aware for pnpm roots, erroring on ambiguous declarations instead of guessing |
| `scripts/package-manager/link.sh` | Link wrapper — `pml` links a local library into an npm or pnpm 11+ project and `--undo` restores the installed version without leaving file changes; Bun and Yarn are refused |
| `scripts/package-manager/run.sh` | Entry point — auto-detects bun, pnpm or npm from `packageManager` field / lockfile; dispatches to the appropriate update script |
| `scripts/package-manager/_update-lib.sh` | Shared logic: semver, cooldown, display, write-back, workspace scanning |
| `scripts/package-manager/pnpm-update.sh` | pnpm wrapper — catalog read/write, `pnpm outdated`, augmentation |
| `scripts/package-manager/npm-update.sh` | npm wrapper — workspace detection, `npm outdated`, augmentation, reads cooldown from project and global `~/.npmrc` |
| `scripts/package-manager/bun-update.sh` | bun wrapper — `bun outdated`, augmentation, reads cooldown from `bunfig.toml` or `~/.bunfig.toml` |
| `scripts/tests/` | Isolated shell-script regression tests, plus tests for scripts shipped by repo-authored skills — run with `python3 -B -m unittest discover -s scripts/tests` |
| `scripts/lib/clean-targets.sh` | Shared generated-artifact lists and matching patterns for cleanup, clean-copy, repository-audit, and branch-pruning scripts |
| `scripts/prune-branches.sh` | Delete local branches whose remote branch was deleted once their commits are confirmed on a remote, removing clean linked worktrees wherever they live. Dry run by default; `--apply` deletes; `--include-merged` also deletes branches merged into the default branch whose remote branch still exists; never touches never-pushed branches or repositories without a remote. Works in one repository or recursively. Aliased as `prune_branches`. See [docs/prune-branches.md](docs/prune-branches.md). |
| `scripts/lib/stale-branches.sh` | Shared stale-branch and worktree classification for `prune-branches.sh` and the read-only section in `audit-repo.sh` |
| `scripts/wipe-clean.sh` | Delete all known build artifacts and dependency folders from the current directory. Aliased as `wipe_clean` |
| `scripts/nosync.sh` | Mark folders to keep out of iCloud sync, or inspect their File Provider ignore attribute; `-r`/`--recursive` finds matching folder names below the current directory without creating folders, following symlinks, or descending into matches; invoked by the `nosync` shell function |
| `scripts/copy-clean.sh` | Clone-copy or ZIP a source directory, defaulting to the current directory, excluding the same artifacts as `wipe_clean`; supports `--ignore-git` and `--keep-name`. Aliased as `copy_clean` |
| `scripts/lib/command-line-tools.sh` | Command Line Tools check (via `brew doctor`) and update through Software Update; used by `update.sh` and `audit.sh` |
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
- **All changes must be portable** — must work across Apple Silicon Macs running macOS 26.7 or newer, not just the current machine. Intel support is out of scope. Bash scripts must work with macOS's bundled Bash 3.2.
- **When adding a new zsh config** — add it to a file in `zsh/conf.d/` and add a `source` line in `zsh/zshrc`
- **The `pm*` commands support every pnpm version from 10.29**, the oldest one a project still pins, not just the newest release of each major. Raise the floor only when the owner agrees. A feature that needs a newer pnpm must be opt-in, check `pnpm --version`, and fail with a clear message on older pnpm, as `pmu --actions` does. When behaviour differs between versions, test each one.
- **When adding a new brew tool**: add it to `Brewfile` — this is the single source of truth for brew packages. `applications.sh` and `update.sh` both use `brew bundle` to install from it
- **Machine-local settings for this repository's scripts** go in `~/.config/dotfiles/` (e.g. `profile`, `roadmap-paths`, `audit-ignore`), never in Git. The exceptions are files whose location a tool dictates (`~/.zshrc_local`, `~/.gitconfig_local*`).
- **Machine profile** lives in `~/.config/dotfiles/profile`, never in Git. Load it through `scripts/machine-profile.sh`; conditional Brewfile entries use the exported `DOTFILES_PROFILE` for automatic installation only. Update and sync installed tools on either profile, regardless of their intended profile; never uninstall them automatically.
- **Local machine overrides** belong in `~/.zshrc_local` (shell) and `~/.gitconfig_local` (git) — these files are intentionally untracked. Run `scripts/setup-git-local.sh` to generate the git local config interactively.
- **Git identity is directory-based** — global default is the personal email; `~/.gitconfig_local` applies work identity under the work directory, with optional personal exceptions. Never hardcode emails in tracked files — `setup-git-local.sh` prompts for work email and reads the personal identity from the effective global Git configuration (`git config --global user.email` and `user.name`).
- **After making changes** that affect symlinks, brew formulae, plugins, or npm globals — run `bash scripts/audit.sh` to verify the repo and installed state are consistent
- **After installing or removing skills** via `npx skills` — run `bash scripts/audit-skills.sh --fix-symlinks` to sync symlinks across all preferred agent providers
- **Repo-authored skills** belong in `agents/skills/` and are linked with `bash scripts/sync-agent-skills.sh`. Existing names are conflicts and must never be overwritten automatically.
- **This repository's commands for other repositories** go in the single `agents/skills/dotfiles/` skill, one reference file per command; it is the only repo-authored skill that may reference this repository. It loads only when the user says "dotfiles", and runs a command only on a direct request, previewing before any change.
- **Repo-authored skills must support repositories with and without a remote** — where a skill compares against the default branch, use the remote-tracking ref (e.g. `origin/main`) when a remote exists, and the local default branch in local-only repositories.
- **Global agent instructions** belong in `agents/AGENTS.md`, never in a tool-specific global file. Keep it short and free of repository-specific rules.
- **Skills owned by another repository** stay in that repository; list its skill directory as `~/path` in `agents/skills/sources.txt` instead of copying the skill here.
- **Cross-machine third-party skills** belong in `agents/skills/skills.txt`; machine-specific skills remain installed locally and must not be added to the manifest.
- **To suppress audit warnings** — add entries to `~/.config/dotfiles/audit-ignore` with format `category:name` (e.g. `brew:tool`, `home:.config/dir`, `skills:dirname`). The file is machine-specific and outside Git.

### Enforced rules

These rules have been broken before and are checked by `scripts/tests/test_repo_rules.py`, which also covers new files that aren't committed yet. When a rule keeps getting broken, add a check there rather than more wording here.

| Rule | Check |
|------|-------|
| Every script in `scripts/`, `scripts/lib/`, `scripts/package-manager/` and `macos/` is listed in the structure table | `test_every_script_is_listed_in_agents_md` |
| Paths to `~/.dotfiles/...` in shell config point to files that exist | `test_shell_config_references_existing_repo_files` |
| Bash files avoid Bash 4+ syntax (`bash -n` in CI does not catch it) | `test_bash_scripts_avoid_bash4_only_syntax` |
| No hardcoded `/Users/<name>` paths | `test_no_hardcoded_home_paths` |
| Repo-authored skills, other than `dotfiles`, do not reference this repository; paths to `~/.dotfiles/...` in `dotfiles` point to files that exist | `test_repo_authored_skills_do_not_reference_this_repository` |

## Roadmap

Planned work and recurring reviews live in [ROADMAP.md](ROADMAP.md). Each item's
brief in `roadmap/` is self-contained; read it before working on the item. A
listed item does not authorise implementing it.

## Testing

- See [README testing instructions](README.md#testing) for prerequisites and suite commands. Run relevant suites from the repository root; shell regression tests must exercise `/bin/bash`, not only a newer Homebrew Bash.
- Keep ShellCheck at zero warnings (CI enforces it; command in the [README](README.md#testing)). Fix real findings; silence a false positive only on its line, with a reason.
- For configuration-writing tests, isolate `HOME`, XDG configuration, and Git configuration as needed. Mock package-manager/network calls and destructive operations; do not use live setup, update, defaults, or cleanup commands as automated test runners.
- Keep success, error, and cancellation cases together. A sandbox- or PTY-related skip is an unverified case, not a passing check; report it without bypassing restrictions.
