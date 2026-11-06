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
to plan nights. Chats a temporary error stopped, such as the model being at
capacity, continue at the next check. Each tier gets separate Codex usage
windows, even after the check removes its `!` marks. The three-chat cap resets with Codex's five-hour limit,
not five hours after each resume. The 24-hour unattended clock resets on
activity, quota or scheduling barriers, and missed checks—not just because a
stop is old. The scan covers chats updated in the last eight days.

```sh
codex-resume --time 15.03 --chat "explore pwa"
codex-resume --reset 30 --project local-relay --message "Carry on with the plan"
codex-resume --recurring 15
codex-resume --list
```

Use `--status`, `--cancel`, or `--help`. See
[docs/codex-resume.md](docs/codex-resume.md) for targeting chats, how
messages are delivered, and requirements.

## Keep the Mac awake for agents

`agent-awake` keeps a MacBook awake, also with the lid closed, while Copilot
CLI, Claude Code or Codex is working on a turn, and lets it sleep again when
the work is done. Open sessions that are not working don't count. It also
wakes the Mac for scheduled `codex-resume` runs. It is opt-in per Mac and needs
an administrator password once.

```sh
agent-awake --install
agent-awake --status
agent-awake --uninstall
```

See [docs/agent-awake.md](docs/agent-awake.md) for how it decides, its
limits, and testing.

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

`git amend-date 1d` sets HEAD's author and committer dates to 24 hours ago;
`git amend-date --shift 1d` moves each existing date back by 24 hours.
It reuses the `m`, `h`, `d`, `w` duration syntax and zsh completion, and also
accepts a quoted explicit Git date. Append a hash or revision, such as
`git amend-date 1d HEAD~1`, to change an older commit: descendants on the current
branch get new parent hashes but keep their dates; other branches stay put.
Older-commit rewrites refuse merge histories and require an attached branch.
It previews the dates, warns if either predates a parent or the commit appears
in local remote-tracking refs, and asks `[y/N]`. It never fetches, so the pushed
warning depends on the last fetch. Staged files, commit contents, messages and
identities stay intact. No commit hooks run; signed rewrites are refused.
Coordinate before rewriting published commits.

The setup writes `~/.gitconfig_local`, `~/.gitconfig_local_work`, and
`~/.gitconfig_local_personal` as needed. These files stay outside the repository.
Re-running asks before replacing the managed block and preserves other settings
in `~/.gitconfig_local`. Declining work setup removes the managed directory
overrides, without deleting the separate identity files.

## Find a command

`dotfiles-help` is a read-only terminal cheat sheet for custom commands, grouped
by purpose. It prints examples rather than executing them, and marks commands
that can change state or run tools with `!`. The overview stays compact;
`shell` covers every alias in `bash/aliases` and its everyday helper functions.
The `git` and `zsh` topics add selected configured Git aliases and interactive
tips, not an exhaustive list of plugin features.
Their links sit at the bottom of the overview's shell group. Individual sheets
keep each subgroup together and separate entries, with wrapped descriptions,
muted cautions and highlighted examples on colour terminals.

```sh
dotfiles-help                # Compact overview
dotfiles-help cleanup        # Topic, with examples and cautions
dotfiles-help pml            # Remember how to try a local library
dotfiles-help disk space     # Search names, descriptions and examples
dotfiles-help shell          # Full grouped shell-shortcut reference
dotfiles-help git            # Useful Git aliases and their cautions
dotfiles-help git unstage    # One Git alias
dotfiles-help zsh            # History search, fuzzy finding and completion
pmh                         # Package-manager cheat sheet
pmh update                  # Search within package-manager commands
```

Zsh completes topic and command names with Tab, including command names after
a topic (`dotfiles-help packages <Tab>`). `pmh` completes only package-manager
commands. Each script command's own `--help` remains the reference for all its
flags. Run `reload` in existing terminals to load the new commands and completions.
Destructive and legacy aliases are documented with warnings, not recommended
as routine maintenance. The reference does not change any alias or key binding.
Descriptions, cautions and examples live in `scripts/lib/command_help.py`, shared
by the cheat sheet, its completion candidates and individual command help.
Usage syntax, flags and detailed behavior stay with each command; documentation
keeps the longer workflows.

In zsh, `G`, `H`, `T` and `L` are global aliases: use `files sizes G cache`
to filter output, `files sizes H` or `files sizes T` for the first/last lines,
and `dotfiles-help shell L` to page it. Quote these letters when you mean them
as literal arguments (`'G'`); they expand anywhere on a command line.
They are not defined in Bash. The `g` shortcut is documented in the Git topic.

`search` shows concise snippets for long matching lines, excluding `.git` and
shared clean targets (dependencies, builds and caches). `-g` filters paths without disabling
those exclusions; `--no-clean` includes clean targets, `--no-trim` preserves
indentation, and `-M 0` shows full lines. See
`dotfiles-help search` for examples.

## Delete a history entry

In zsh, `h` lists native history; `history-delete 222` deletes the event numbered
222 from the current shell and its saved history, after showing the selected
command and asking `[y/N]` (only `y` or `Y` confirms). Numbers may change afterward.
Other open shells can retain and later re-save the entry, so this is not a
secure-erasure guarantee. Ambiguous duplicate matches are refused rather than
deleting the wrong occurrence. `history-delete --help` and flag completion are
available; native `h` flags are unchanged.

## Package-manager commands

These commands detect npm, pnpm, or Bun from the project's `packageManager`
field or lockfile. The selected manager must already be installed.

| Command | What it does |
|---|---|
| `pmh` | Show the package-manager cheat sheet; `pmh pml` shows a command's examples |
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

## Inspect files and folders

`files <command> [options] [path]` reports on the current folder by default.
Reports are read-only and never follow symlinks. Hidden files are included.
Except for the global `caches` report, commands support `-r`/`--recursive`
and repeatable `--exclude GLOB`. Listings default to 20 rows; `--limit 0` shows all.

| Command | What it does |
|---|---|
| `files count -r` | Count files, folders and symlinks |
| `files large -r --min 100M` | Largest files first |
| `files changed -r --since 7d` | Recently modified files |
| `files sizes` | Allocated bytes per child folder, including subfolders |
| `files empty -r` | Zero-byte files and genuinely empty folders |
| `files types -r` | Counts and bytes by extension |
| `files duplicates -r` | Group identical nonempty files |
| `files caches` | Rank known developer caches |
| `files caches --include-configured` | Also query configured global locations, with source labels |
| `files leftovers -r` | Possible agent/temporary leftovers; not deletion recommendations |

Use `--clean` to exclude generated artifacts, or `--clean-git` to exclude
Git metadata too. Warnings are summarized; `--verbose` shows affected paths
and hidden tool-managed leftover matches. Cloud-only paths are marked
`[cloud-only]`; `--local-only` excludes them without excluding downloaded
iCloud files. Reports do not trigger cloud downloads.

See [file reports](docs/files.md) for all options, size caching, iCloud
behaviour, leftover matching and examples.

## Clean and copy projects

`wipe_clean` removes generated project artifacts after a preview and confirmation.
`copy_clean` copies or ZIPs a project without those artifacts.

```sh
wipe_clean --dry-run --sort size
copy_clean ../project-copy
```

See [project cleanup and copying](docs/cleanup.md#clean-and-copy-projects)
for age filters, exclusions, ZIP options and space measurements.

## Clean Docker storage

`clean_docker` inspects storage or cleans explicitly selected build cache and
untagged dangling images after confirmation. Containers, volumes and tagged
images are kept.

```sh
clean_docker --inspect
clean_docker --only build-cache --older-than 30d --dry-run
```

See [Docker cleanup](docs/cleanup.md#clean-docker-storage) for context and
builder selection, age filtering and safety boundaries.

## Clean global caches

`clean_caches` previews global developer caches. Cleaning requires explicit
`--only` selection and confirmation; `--deep` permits expensive cleaners.
Sizes show disk usage, not guaranteed savings.

```sh
clean_caches --dry-run --sort size
clean_caches --only uv,pip
```

See [global cache cleanup](docs/cleanup.md#clean-global-caches) for tool discovery,
report-only storage, orphan cleanup and per-tool safety boundaries.

## Prune stale branches

`prune_branches` finds local branches whose upstream branch was deleted on its
remote, typically after a pull request was merged. Inside a repository or
worktree it checks that repository; elsewhere it searches subdirectories
(`--depth`, default 4), skipping generated folders and submodules.

```sh
prune_branches --dry-run          # fetch, then show the plan without prompting
prune_branches                    # show the plan, ask [y/N], then delete
prune_branches --dry-run --include-merged   # preview merged branches whose remote branch still exists
prune_branches --dry-run --include-detached # preview eligible detached linked worktrees
prune_branches --dry-run --older-than 7d    # retain recent tips and worktree contents
prune_branches --dry-run --no-fetch ~/Developer   # read-only, uses the last fetch
```

A branch is deleted only when its remote branch is gone and its commits are
confirmed on the remote, for example through a merged pull request. Anything
uncertain is listed for review instead. With `--include-merged`, local
branches already merged into the default branch are deleted too, even when
their remote branch still exists; the remote branch is kept. The flag also
includes branches tracking another local branch when their commits are
confirmed on a remote, including pull request heads. Never-pushed
branches, repositories without a remote, and protected names such as `main`
are never deleted. A
branch's linked worktree is removed first, and only when it has no
uncommitted changes or unique commits; ignored generated artifacts such as
`node_modules/` are discarded with it.
Deleted branches are logged in `.git/prune-branches.log` for recovery. Exit
status is 1 when a fetch or deletion fails.
`--include-detached` optionally removes clean detached linked worktrees
whose HEAD is contained in a freshly confirmed remote branch. Main/invoking
worktrees, locks, unfinished Git operations and worktree-only history/refs
are protected. `--older-than 7d` adds a retention guard to all removal
candidates: the tip commit and, for worktrees, every modification outside
`.git` must be older. It scans metadata and rechecks before removal; it is
not proof that an agent or process has stopped using the worktree.

See [docs/prune-branches.md](docs/prune-branches.md) for the exact deletion
rules, worktree handling, and recovery.

## Repository triage

The default audit lists registered worktrees and branches.
Add `audit-repo --workspaces` for allocated sizes and keep/review assessments
of this repository's registered worktrees, including linked paths outside it.
This opt-in report reuses `prune_branches` safety checks and the native size
scanner; it never deletes workspaces or discovers unrelated clones.

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
audit-repo --no-package-manager    # local checks without package-manager or Node execution
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
copy/archive tools. No Python test packages need to be installed.

```sh
TZ=UTC python3 -B -m unittest discover -s scripts/tests
# One suite:
TZ=UTC python3 -B -m unittest discover -s scripts/tests -p 'test_package_updates.py'
```

CI runs in UTC, so `TZ=UTC` makes local runs catch tests that only pass in the
Mac's own time zone. Tests that depend on local time pin their zone themselves.

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
terminal opened in the same directory and terminal app resumes it; changing
into the directory with `cd` does not. Switching session with
`/new`, `/clear` or `/resume` is followed, so the session in use when the
terminal closed comes back, even if you only resumed it without writing
anything. A session you never wrote to cannot be resumed. Several terminals
in one repository each get one session back, oldest first; which tab gets
which is not guaranteed. A session comes back at most once: a record is
dropped when another terminal has its session open or used it later.
VS Code can revive terminals it never shows, so
there a hidden terminal waits until it gets a real size, checking while its
prompt is empty. Entering a shell command cancels that wait. Exiting the
agent normally means it is not resumed. Records are dropped after 7 days
without tracking activity, and never while their shell is still running.
Failed automatic launches keep their record for the next terminal to retry.
`CPLT_NO_RESUME=1` turns resuming off.

To set a machine-local default model for new Copilot sessions, create
`~/.config/dotfiles/copilot-models.zsh`, using your chosen model's CLI identifier:

```zsh
DOTFILES_COPILOT_MODEL='<model-id>'
typeset -A DOTFILES_COPILOT_MODELS=(
  "$HOME/Developer/project-a" '<model-id>'
  "$HOME/Developer/project-b" '<another-model-id>'
)
```

Both `copilot` and `cplt` (when running Copilot) read this file on each new
session launch; changes need no shell restart. Resume selectors such as
`--resume`, `--continue` and an existing `--session-id` leave model selection
to the saved session. The file is sourced as zsh configuration,
so keep only trusted settings in it. A missing file leaves Copilot's own default
unchanged; a file that fails to load aborts the launch with an error.
The map matches the current shell directory and its descendants, with the
most specific folder winning over `DOTFILES_COPILOT_MODEL`. Paths are literal,
not patterns; use absolute paths as above. An empty mapped value uses Copilot's
own default for that folder. Directory flags such as `-C` or `cplt -d` do not
change which entry matches. Defaults are passed with `--model`, so they win
over an organization's default model, which overrides `COPILOT_MODEL`.
Copilot also reapplies that model when `/resume` switches session in the same
window. An explicit `--model` takes precedence; subcommands
and other agents are unchanged. Leave the variable unset or empty to use
Copilot's own default. `command copilot` and `command cplt` bypass this too.

Run `agent-sessions` for a read-only table of recorded sessions: project,
session ID prefix, title, terminal app, launcher, owning shell PID and restoration status.
Live sessions come first, followed by waiting sessions and other records;
empty sections are omitted. Each section is sorted by project path, session
ID and shell PID.
Titles use the same lookup as `agent-awake --status`. `Live, tracked` means
the record has a matching live shell and saved conversation; `Waiting to
restore` means its shell has gone and the next terminal in that project can
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
When the optional `agent-awake` hooks are installed, submitted prompts also
publish the session ID immediately in `~/.cache/agent-sessions`. These
observations do not depend on an awake hold or the root helper; log following
still works without the hooks or when no prompt was submitted.
The hook can fill in the selected session for an already tracked launch.
It cannot register a session launched before tracking was loaded or through
`command copilot`, `command claude` or `command cplt`: those processes lack
the wrapper's launch tag. Resume such a chat through a wrapper in a new
terminal to start tracking it.
The cplt wrapper explicitly forwards only the two session-tag environment
variables through its environment filter. If a process-local source is gone,
the newest valid hook or follower observation is used.
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
