# Dotfiles

Shell, Git, development tools, and system preferences for Apple Silicon Macs,
managed by [dotbot](https://github.com/anishathalye/dotbot).

## Setup

Start with Xcode Command Line Tools (`xcode-select --install`) and
[Homebrew](https://brew.sh/). Ensure `git`, `python3`, and `brew` are available
in the current shell. Setup needs network access and may request administrator
access. Requires macOS 26.7 or newer; Intel Macs are not supported.

Review `install.conf.yaml` and back up conflicting dotfiles before applying the
links. Dotbot includes its YAML dependency; no separate pip installation is needed.

```sh
git clone https://github.com/hkjersem/dotfiles.git ~/.dotfiles
cd ~/.dotfiles
./install --no-audit
bash macos/install.sh --no-defaults
exec zsh -l
```

`./install` applies symlinks; the setup script then asks for a machine
profile and installs tools. `--no-defaults` leaves macOS preferences unchanged.
Start a new login shell, as above, before using the commands below. Run the Bash
setup scripts with `bash`, not `source` from zsh.

Most dotfiles are symlinked into this checkout, so edits take effect without a
commit or another install. Shell settings may require a new shell.

## Daily commands

| Command | What it does |
|---|---|
| `dotfiles-install` | Reapply links and bootstrap tools, plugins, and local Git identity; leaves macOS preferences unchanged |
| `dotfiles-update` | Update macOS/App Store apps, Homebrew packages, Command Line Tools, Node/npm globals, Zsh plugins, and supported installed CLIs; then audit |
| `dotfiles-defaults` | Apply macOS system preferences |
| `dotfiles-audit` | Check for drift between repo and installed state (includes skills) |
| `dotfiles-profile` | Show or choose the machine's personal/work profile |

`dotfiles-update` updates installed software, not this Git checkout. To get new
dotfiles and reapply their symlinks:

```sh
git -C ~/.dotfiles pull --ff-only
bash ~/.dotfiles/install
```

Audit reports missing declarations, unexpected installed items, and broken
links. Machine-local suppressions go in the gitignored `audit.ignore`, one per
line, such as `brew:tool` or `home:.config/dir`.

## Resume Codex later

`codex-resume` sends `continue`, or a custom message, to a Codex desktop chat
at a set time or when a usage limit resets, even while the screen is locked
for chats on this Mac.

```sh
codex-resume --time 15.03 --chat "explore pwa"
codex-resume --reset 30 --project local-relay --message "Carry on with the plan"
codex-resume --list
```

Use `--status`, `--cancel`, or `--help`. See
[docs/codex-resume.md](docs/codex-resume.md) for targeting chats, how
messages are delivered, and requirements.

## Machine profile

The first `dotfiles-install` or `dotfiles-update` asks whether this is a `personal`
or `work` machine. The choice is saved in `~/.config/dotfiles/profile`,
outside the repository, so each machine keeps its own setting.

```sh
dotfiles-profile personal       # Set or change the profile
dotfiles-profile work
dotfiles-profile                # Show it (ask if unset)
dotfiles-profile --choose       # Ask again
# Before shell aliases are available:
bash ~/.dotfiles/scripts/machine-profile.sh personal
```

Personal and work profiles can install different tools. The profile only
controls automatic installation: installed tools are updated and synced on
either profile, regardless of which profile normally installs them. Changing
the profile does not uninstall anything or change directory-based Git identities.

Non-interactive installation and updates require a saved profile. The audit
reports a missing or invalid profile without prompting or changing it.
Install/update scripts export the saved choice as `DOTFILES_PROFILE` for
conditional entries in `Brewfile`. When running `brew bundle` manually, set
`DOTFILES_PROFILE=work` explicitly to include work-only packages.

## Git identity

Git uses your global personal identity by default. During installation,
`scripts/setup-git-local.sh` can configure a work identity for a directory,
with personal exceptions inside it. This is independent of the machine profile.

```sh
bash ~/.dotfiles/scripts/setup-git-local.sh
git whoami
```

The setup writes `~/.gitconfig_local`, `~/.gitconfig_local_work`, and
`~/.gitconfig_local_personal` as needed. These files stay outside the repository.
Re-running asks before replacing the managed block and preserves other settings
in `~/.gitconfig_local`. Declining work setup removes the managed directory
overrides, without deleting the separate identity files.

## Package-manager commands

These commands detect npm, pnpm, or Bun from the project's `packageManager`
field or lockfile. The selected manager must already be installed.

| Command | What it does |
|---|---|
| `pm <command>` | Run a command with the detected package manager |
| `pmx <package>` | Execute a package through npx, pnpm dlx, or bunx |
| `pmi [package...]` | Install dependencies or add packages |
| `pmr <package...>` | Remove packages |
| `pmu --dry-run` | Preview updates within the current compatible version range |
| `pmu --major` | Allow breaking version updates; still asks before applying |
| `pmc --dry-run` | Preview manifest/catalog cleanup |
| `pma` | Audit and apply supported fixes; Bun only reports findings |

`pmu` respects configured release-age cooldowns; `pma --deep` is not a
read-only check. In zsh, `pnpm` and `yarn` run through corepack, as do `npm`
and `npx` in projects with a `packageManager` field. See
[docs/package-managers.md](docs/package-managers.md) for cooldowns, pnpm
workspaces, and how `dotfiles-update` manages package-manager versions.

Related Node helpers: `install_node [version]` installs through fnm, migrates
global packages, and installs and enables corepack if the Node version does not
bundle it; `npm_globals_diff <version_a> [version_b]` compares globals;
`npm_release_age <package>[@version]` shows release ages.

## Clean and copy projects

`wipe_clean` previews generated directories and cache files, then asks before
deleting them. Git metadata and submodule directories are preserved.
`copy_clean` copies a project without the same generated artifacts, using
macOS clone-on-write copies where supported. Existing destinations are never
overwritten.

```sh
wipe_clean
copy_clean ../project-copy
copy_clean --ignore-git --keep-name ../copies
copy_clean --zip
copy_clean --zip ../project ../project-clean.zip
```

A single copy argument is the destination; the source defaults to the current
directory. `--keep-name` requires an existing destination directory.
ZIP archives always include the source folder name; without a destination,
the archive is created inside the source. `--ignore-git` excludes `.git`
directories; otherwise Git metadata is copied too.

## Prune stale branches

`prune_branches` finds local branches whose upstream branch was deleted on its
remote, typically after a pull request was merged. Inside a repository or
worktree it checks that repository; elsewhere it searches subdirectories
(`--depth`, default 4), skipping generated folders and submodules.

```sh
prune_branches                    # dry run: fetch, then show the plan
prune_branches --apply            # show the plan, ask, then delete
prune_branches --no-fetch ~/Developer   # read-only, uses the last fetch
```

A branch is deleted only when its remote branch is gone and its commits are
confirmed on the remote, for example through a merged pull request. Anything
uncertain is listed for review instead. Never-pushed branches, repositories
without a remote, and protected names such as `main` are never deleted. A
branch's linked worktree is removed first, and only when it has no
uncommitted changes or unique commits; ignored generated artifacts such as
`node_modules/` are discarded with it.
Deleted branches are logged in `.git/prune-branches.log` for recovery. Exit
status is 1 when a fetch or deletion fails.

See [docs/prune-branches.md](docs/prune-branches.md) for the exact deletion
rules, worktree handling, and recovery.

## Repository triage

`audit-repo` reports Git activity, ownership concentration, frequently changed
files, source sizes, test-file conventions, and build configuration. Run it
inside a repository or select one explicitly; a subdirectory is resolved to
the Git root. It also lists what `prune_branches` would delete, review, or
keep, based on remote-tracking refs from the last fetch; it never fetches or
deletes branches itself. In a repository without a remote, the stale-branch,
CI and knowledge-concentration checks are skipped.

```sh
audit-repo
audit-repo --dir ../project --since "6 months ago"
audit-repo --skip-security
```

It also checks documentation file names and link case, and, with a root
`package.json`, package metadata, cooldowns, JS/TS tooling, vulnerabilities and
outdated packages (requires jq). `.env` files are never read.

The script does not install dependencies, run project tests, apply fixes, or
edit project files. **It is not offline:** npm/pnpm audit and outdated queries
can contact registries and write package-manager caches. `--skip-security`
skips only the vulnerability query, not the outdated query.

Findings are triage indicators, not a security verdict. Exit status is 1 for
concrete issues (such as vulnerabilities or npm's always-failing placeholder
`test` script) or checks that could not run, and 0 otherwise; warnings alone
do not fail the command. See [docs/audit-repo.md](docs/audit-repo.md) for what
each check covers and how to read the results.

## Testing

Run from the repository root on macOS, with Python 3, Node.js, jq, and Perl
available. Some fixtures also use `/usr/bin/python3`, `/usr/bin/ruby`, and macOS
copy/archive tools. No Python test packages need to be installed.

```sh
python3 -B -m unittest discover -s scripts/tests
# One suite:
python3 -B -m unittest discover -s scripts/tests -p 'test_package_updates.py'
```

Tests use disposable fixtures and mocked package-manager/network operations,
not live installations. Shell regressions run under `/bin/bash` to exercise
the bundled Bash 3.2. Interactive-terminal or Git-alias checks can be skipped
when the environment blocks them; skipped checks are not verified passes.
`test_repo_rules.py` adds static checks for repository rules; see
[AGENTS.md](AGENTS.md#enforced-rules).

GitHub Actions runs shell syntax checks, ShellCheck, and the full regression
suite on an Apple Silicon macOS runner for pushes and pull requests. Apart from
installing ShellCheck, the workflow uses the runner's preinstalled tools and
does not run machine setup or live updates. Run the same lint locally:

```sh
git ls-files -z -- install '*.sh' bash/aliases bash/bashrc | xargs -0 shellcheck -S warning -x
```

Warnings must stay at zero. When a warning is a false positive, such as a
variable read by a script that sources the file, silence it on that line with
`# shellcheck disable=SCxxxx  # reason`.

## Global agent instructions

`agents/AGENTS.md` holds personal instructions shared by every agent tool.
`scripts/sync-agent-instructions.sh` symlinks it to each tool's global
instruction file, but only when that tool's home directory exists:

| Tool | Global file |
|------|-------------|
| Claude Code | `~/.claude/CLAUDE.md` |
| Codex | `~/.codex/AGENTS.md` |
| Copilot CLI | `~/.copilot/copilot-instructions.md` |

Missing or empty files, broken links and links to old paths inside `~/.dotfiles`
are replaced. A file with content, or a working link elsewhere, is reported as
a conflict and left unchanged; move its rules into `agents/AGENTS.md`, delete
it, and sync again. Setup and update run the sync; `scripts/audit.sh` reports
the state.

```sh
bash scripts/sync-agent-instructions.sh --dry-run
bash scripts/sync-agent-instructions.sh
```

Keep the file short; it is loaded into every session. Instructions an agent
writes through a tool's memory command land in this tracked file.

## Agent sessions

`zsh/conf.d/agent-sessions.zsh` wraps `copilot`, `claude` and `cplt` when
they are installed, for Copilot CLI and Claude Code sessions. If a session's
terminal closes while it runs, such as when VS Code or iTerm2 quits, the next
terminal opened in the same directory resumes it. Switching session with
`/new`, `/clear` or `/resume` is followed, so the session in use when the
terminal closed comes back, even if you only resumed it without writing
anything. A session you never wrote to cannot be resumed. Several terminals
in one repository each get one session back, oldest first; which tab gets
which is not guaranteed. VS Code can revive terminals it never shows, so
there a session only resumes in a terminal that gets a real size within 2
seconds. Exiting the agent normally means it is not resumed,
and sessions older than 7 days are dropped. `CPLT_NO_RESUME=1` turns resuming
off.

`cplt` also starts Copilot with `--allow-all` and Claude Code with
`--dangerously-skip-permissions`, since cplt has no setting for agent flags;
the sandbox still limits what they can reach. Plain `copilot` and `claude`
keep their permission prompts. cplt subcommands and other agents, chosen with
`--agent` or `sandbox.agent`, run unchanged. `command cplt`, `command copilot`
and `command claude` skip the wrappers.

## Agent skills

Skills authored in this repository live under `agents/skills/`.
`scripts/sync-agent-skills.sh` links them into the runtime canonical directory
at `~/.agents/skills/` without overwriting name collisions. A skill that ships a
script is tested from `scripts/tests/`, so the skill directory itself stays
free of repository-specific tooling.

`agents/skills/skills.txt` lists third-party global skills installed on every
machine, and `agents/skills/sources.txt` lists skill directories owned by other
repositories. Machine-specific skills remain local and are not removed by the
sync.

```sh
bash scripts/sync-agent-skills.sh --dry-run
bash scripts/sync-agent-skills.sh
bash scripts/audit-skills.sh --fix-symlinks
```

The last command exposes the canonical skills to installed agent providers.
Without repair flags, the skills audit is read-only, and repairs never replace
real files or foreign links. See [docs/agent-skills.md](docs/agent-skills.md)
for the manifest format, external sources, and every audit flag.

Setup and update stop if synchronization or repair fails. Resolve conflicts
manually rather than deleting local skills to make the audit pass.

## iTerm2

Settings → enable *Load preferences from a custom folder or URL* → `~/.dotfiles/iterm/com.googlecode.iterm2.plist`
