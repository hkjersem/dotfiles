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
| `roadmaps` | Show due items, items awaiting acceptance, work in progress and errors across `roadmap` skill roadmaps in the repositories, or folders whose direct subfolders are repositories, listed one per line in the machine-local `~/.config/dotfiles/roadmap-paths`; or pass paths directly. Finds `roadmap.md` or `roadmaps.md` (any casing) at the root or in `docs/`, `tasks/` or `planning/`, with briefs in `roadmap/` beside the index or at the repository root. Add `--check` for detailed validation |

Use `roadmaps --check .` (or `roadmaps . --check`) to validate the current
repository without changing files. `roadmaps --check` validates all configured
locations. Check mode exits 1 for invalid or unreadable roadmaps, missing paths
and paths without a managed roadmap. Progress appears only on a terminal;
Ctrl+C stops the command without a traceback and exits 130.

Add `--strict` to check brief quality, for example `roadmaps --check --strict .`
before handing work to an agent. It warns about missing summaries, acceptance
criteria, progress/log sections, backlinks and nonexistent code pointers.
These heuristic warnings do not fail the check or change files.

Use `roadmaps --sync .` (or `roadmaps . --sync`) to refresh the generated index
lists from the briefs, then validate. It preserves briefs, statuses and
handwritten sections. Sync requires explicit paths, never the configured list;
a collection path updates every discovered roadmap in that collection. Invalid
brief front matter or missing brief directories block the affected index update.

The commands from this repository, including `dotfiles-*`, print their options
with `--help`, and zsh completes them: type `pmu --` and press Tab. Flags a
command does not accept turn red as you type.

Use `dotfiles-update --skip-os-updates` to skip macOS updates and their restart
prompt while still updating App Store apps, Command Line Tools, and other tools.

`dotfiles-update` updates installed software, not this Git checkout. To get new
dotfiles and reapply their symlinks:

```sh
git -C ~/.dotfiles pull --ff-only
bash ~/.dotfiles/install
```

Audit reports missing declarations, unexpected installed items, and broken
links. Machine-local suppressions go in `~/.config/dotfiles/audit-ignore`, one
per line, such as `brew:tool` or `home:.config/dir`.

## Resume Codex later

`codex-resume` sends `continue`, or a custom message, to a Codex desktop chat
on this Mac now, at a set time or when a usage limit resets, even while the
screen is locked. `--recurring` keeps resuming chats a usage limit stopped,
with `!` in a title to stagger chats behind unmarked ones and `--morning`
to plan nights. Each tier gets separate five-hour windows, even after the
check removes its `!` marks.

```sh
codex-resume --time 15.03 --chat "explore pwa"
codex-resume --reset 30 --project local-relay --message "Carry on with the plan"
codex-resume --recurring 15
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
| `pml ../lib` | Try a local library instead of the installed one; `pml --undo ../lib` restores it |
| `pmu --dry-run` | Preview updates within the current compatible version range |
| `pmu --major` | Allow breaking version updates; still asks before applying |
| `pmu --actions` | Update and pin GitHub Actions in a pnpm 12.7+ project |
| `pmc --dry-run` | Preview manifest/catalog cleanup |
| `pma` | Audit and apply supported fixes; Bun only reports findings |
| `pma --dry-run` | Report findings without changing files; npm also lists the fixes it would make |

`pmu` respects configured release-age cooldowns; `pma --deep` is not a
read-only check. In zsh, `pnpm` and `yarn` run through corepack, as do `npm`
and `npx` in projects with a `packageManager` field. See
[docs/package-managers.md](docs/package-managers.md) for cooldowns, pnpm
workspaces, local libraries, and how `dotfiles-update` manages package-manager
and Node versions.

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
## Inspect files and folders

`files <command> [options] [path]` reports on the current folder by default.
Every command supports `-r`/`--recursive` and repeatable `--exclude GLOB`
(matching an entry name or relative path). Hidden and Git-ignored entries are
included unless explicitly excluded. Symlinks are never followed, including
the target folder itself. Nothing is deleted or modified. Zsh completes
subcommand names and flags from the command's help.
Folder names use the shell's BSD `ls` colours (`LSCOLORS`), without trailing
slashes. Mixed empty-entry results keep explicit `File` and `Folder` labels;
redirected output and `NO_COLOR` remain plain. Colours use the terminal palette,
so light and dark themes apply just as they do to `ls`.

Add `--clean` to any subcommand to exclude the generated directories and file
patterns shared with `wipe_clean` and `copy_clean` (such as `node_modules`,
`dist`, `.venv` and `*.tsbuildinfo`). The report marks when this filter is active.
It does not exclude Git metadata or apply `.gitignore`.
Use `--clean-git` for the same generated-artifact exclusions plus `.git`
directories and worktree pointer files at every depth. It does not hide
`.gitignore`, `.github`, or other Git-ignored project files.

| Command | What it does |
|---|---|
| `files count -r` | Separate totals for files, folders, symlinks and other entries; excludes the root itself |
| `files large -r --min 100M` | Largest files first; size suffixes use powers of 1024 |
| `files changed -r --since 7d` | Files by modification time, newest first, with local timestamps; ages accept `m`, `h`, `d`, `w` |
| `files sizes` | Allocated file bytes per immediate child folder, including all its subfolders; `-r` also lists nested folders, `--direct` counts only direct files per listed folder, and `--logical` reports file lengths instead |
| `files empty -r` | Zero-byte files and genuinely empty folders, not folders made empty by exclusions |
| `files types -r` | Counts and bytes by final extension, case-insensitive; dotfiles without a suffix have no extension |
| `files duplicates -r` | SHA-256 content matches, grouped by size first; skips empty files and counts hard links once |

Listings show up to 20 rows or duplicate groups; `--limit 0` shows all.
`sizes` always scans nested contents for totals unless `--direct` is set;
its `-r` flag expands the listing rather than changing how totals are calculated.
Parent and nested folder totals overlap, so do not sum rows from `sizes -r`.
Size scans use macOS's bulk metadata API and retain folder totals rather than
the whole file list. No compiler or additional dependency is needed; normal
runs read current metadata.
Fresh size runs atomically save the full snapshot, including warnings, under
`${XDG_CACHE_HOME:-~/.cache}/dotfiles/files-sizes/`. Use `files sizes --cached`
to reuse it with a different `--limit` without inspecting the target folder.
Cached output shows its age; snapshots expire after 24 hours and are replaced
by fresh runs with the same folder and scan options (including recursion,
direct mode and exclusions). Missing, invalid or expired caches produce an
error, never an automatic rescan. Each `files sizes` invocation, including
`--cached`, deletes snapshot files last written at least 24 hours ago from this
cache directory. No background service runs; files remain while the command
is unused. Cached results do not reflect added, removed or changed files.
On a terminal, size scans lasting over two seconds show an updating progress
line with the current folder and entry count. It clears before the report or
on interruption; redirected output stays plain.
`count` always shows all totals. `sizes` reports allocated file-data bytes by
default, so sparse files count only their allocated data. Use `--logical` to
report full file lengths instead. Shared APFS blocks can be counted more than
once; outside duplicate detection, hard-linked paths count separately.
Duplicate savings are estimates excluding hard-linked copies; APFS sharing may
reduce actual savings. Permission-denied entries are skipped with path-specific
warnings and an incomplete-report marker; affected child folder sizes show
`Unknown`, not zero. An unreadable target folder, other I/O errors, or detected
changes during duplicate hashing still fail explicitly. Filesystem reports are snapshots, not a
transactional view of concurrently changing folders.

```sh
files large -r --exclude node_modules --exclude .git ~/Developer
files count -r --clean
files sizes -r --clean-git
files sizes -r --clean-git --cached --limit 0
files duplicates -r --limit 0 ~/Downloads
files changed --help
```

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
prune_branches --include-merged   # also merged branches whose remote branch still exists
prune_branches --no-fetch ~/Developer   # read-only, uses the last fetch
```

A branch is deleted only when its remote branch is gone and its commits are
confirmed on the remote, for example through a merged pull request. Anything
uncertain is listed for review instead. With `--include-merged`, local
branches already merged into the default branch are deleted too, even when
their remote branch still exists; the remote branch is kept. Never-pushed
branches, repositories without a remote, and protected names such as `main`
are never deleted. A
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

It also checks documentation file names and link case, validates skill-managed
roadmaps (candidate roadmap files require Python 3), and, with a root
`package.json`, checks package metadata, cooldowns, JS/TS tooling, vulnerabilities
and outdated packages (requires jq). Other roadmap formats are left alone.
`.env` files are never read.

The script does not install project dependencies, run project tests, apply fixes,
or edit project files. By default it runs the repository-selected package
manager, which Corepack may download and execute; queries can contact registries
and write caches. Use `--no-package-manager` to skip package-manager and Node
execution while keeping local checks; it also skips branch classification that
can run Git hooks or merge drivers and disables Git lazy fetching.
`--skip-security` skips only the vulnerability query, not outdated or
configuration commands.

Findings are triage indicators, not a security verdict. Exit status is 1 for
concrete issues (such as vulnerabilities or npm's always-failing placeholder
`test` script) or checks that could not run, and 0 otherwise; warnings alone
do not fail the command. See [docs/audit-repo.md](docs/audit-repo.md) for what
each check covers and how to read the results.

## Testing

Run from the repository root on macOS, with Python 3, Node.js, jq, and Perl
available. Some fixtures also use `/usr/bin/python3`, `/usr/bin/ruby`, and macOS
audit-repo --no-package-manager    # local checks without package-manager or Node execution
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
terminal opened in the same directory and terminal app resumes it. Switching session with
`/new`, `/clear` or `/resume` is followed, so the session in use when the
terminal closed comes back, even if you only resumed it without writing
anything. A session you never wrote to cannot be resumed. Several terminals
in one repository each get one session back, oldest first; which tab gets
which is not guaranteed. VS Code can revive terminals it never shows, so
there a hidden terminal waits until it gets a real size, checking while its
prompt is empty. Entering a shell command cancels that wait. Exiting the
agent normally means it is not resumed. Records are dropped after 7 days
without tracking activity, and never while their shell is still running.
Failed automatic launches keep their record for the next terminal to retry.
`CPLT_NO_RESUME=1` turns resuming off.

Run `agent-sessions` for a read-only table of recorded sessions: project,
session ID prefix, title, terminal app, launcher, owning shell PID and restoration status.
Live sessions come first, followed by waiting sessions and other records;
empty sections are omitted. Each section is sorted by project path, session
ID and shell PID.
`Live, tracked` means the record has a matching live shell and saved
conversation; `Waiting to restore` means its shell has gone and the next terminal in that project can
resume it. Unknown sessions, missing history, expired records and restore
claims are shown separately. The command never claims or cleans records.
It reports the latest saved observation, not an instantaneous inspection of
the agent UI, and lists only launches that have a tracking record.
Records live in `${XDG_STATE_HOME:-~/.local/state}/agent-sessions`.
New launches save `TERM_PROGRAM` so VS Code and iTerm cannot claim each other's
sessions, including after a reboot. This matches the app, not individual windows
or tabs within it. Older records without an app show `Unknown` and remain
eligible in any app; their next wrapped launch saves the app. Existing running
shells need to reload the wrapper or start a fresh terminal to save this field.

Sessions launched with `copilot --resume` or `--continue`, including names and
ID prefixes, are tracked once Copilot opens the selected session. The same
applies to `cplt --resume`, `cplt --continue` and agent flags after `--`.
The follower polls rapidly for at most ten unsuccessful startup samples, then
uses its normal five-second interval. Saved process start times and log
creation timestamps prevent a reused PID from selecting an unrelated session;
rotated Copilot logs are included. Process-local files are only reused at
restoration while the original process identity can still be verified.
The wrappers expose a launch tag and agent kind for optional prompt hooks;
cplt explicitly forwards only those two environment variables through its
filter. If a process-local source is gone, the newest valid hook or follower
observation is used.
Once the selected session is observed, its new record replaces the restore
attempt's retry record. A later crash or terminal hangup cannot resurrect that
old record. Only a launch whose selected session was never observed is eligible
for retry.
Failed attempts do not extend the original record's seven-day retention.

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
