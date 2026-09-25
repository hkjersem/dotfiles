# Dotfiles

Shell, Git, development tools, and system preferences for Apple Silicon Macs,
managed by [dotbot](https://github.com/anishathalye/dotbot).

## Setup

Start with Xcode Command Line Tools (`xcode-select --install`) and
[Homebrew](https://brew.sh/). Ensure `git`, `python3`, and `brew` are available
in the current shell. Setup needs network access and may request administrator
access. Intel Macs are not supported.

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
| `dotfiles-update` | Update macOS/App Store apps, Homebrew packages, Node/npm globals, Zsh plugins, and supported installed CLIs; then audit |
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

`pmu` respects configured release-age cooldowns. `--cooldown <days>` overrides
them, including `--cooldown 0` to disable the cooldown. Use `--yes` only when
you intend to skip confirmation. `pma --deep` additionally refreshes pnpm's
lockfile before auditing; it is not a read-only check.

In pnpm workspaces, `pmi` and `pmr` use the current member or infer an existing
dependency's target from the root. Ambiguous targets fail rather than guessing.
For pnpm, `--root` explicitly selects the root package. Unknown installs at the
root stay at the root; catalog entries change only with an explicit version.
Use `pmi --help`, `pmr --help`, or `pmu --help` for details.

Related Node helpers: `install_node [version]` installs through fnm and migrates
global packages; `npm_globals_diff <version_a> [version_b]` compares globals;
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

## Repository triage

`audit-repo` reports Git activity, ownership concentration, frequently changed
files, source sizes, test-file conventions, and build configuration. Run it
inside a repository or select one explicitly; a subdirectory is resolved to
the Git root.

```sh
audit-repo
audit-repo --dir ../project --since "6 months ago"
audit-repo --skip-security
```

Source-file statistics use tracked files, excluding known generated artifacts,
symlinks, and missing files. With a root `package.json`, it also checks package metadata,
cooldowns, and JS/TS tooling; these checks require jq. npm and pnpm provide
structured vulnerability and outdated-package results. Bun and other managers'
audit/outdated results are not parsed by this tool.

The script does not install dependencies, run project tests, apply fixes, or
edit project files. **It is not offline:** npm/pnpm audit and outdated queries
can contact registries and write package-manager caches. `--skip-security`
skips only the vulnerability query, not the outdated query.

Findings are triage indicators, not a security verdict or measured test
coverage. Ownership concentration may be expected in a solo project.
`--since` controls churn and fix/revert history; ownership uses all history,
and velocity uses the last 12 complete calendar months, including inactive
months. JSONC and inherited TypeScript settings are reported as unevaluated.
Exit status is 1 for reported issues or operational failures, and 0 otherwise;
warnings alone do not fail the command.

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

GitHub Actions runs shell syntax checks and the full regression suite on an
Apple Silicon macOS runner for pushes and pull requests. The workflow uses the
runner's preinstalled tools and does not run machine setup or live updates.

## Agent skills

Skills authored in this repository live under `agents/skills/`.
`scripts/sync-agent-skills.sh` links them into the runtime canonical directory
at `~/.agents/skills/` without overwriting name collisions.

`agents/skills/skills.txt` lists third-party global skills that should be
installed on every machine. Machine-specific skills remain local and are not
removed by the sync. Prefix a manifest entry with `# disabled:` to uninstall
that managed skill on each machine while keeping the declaration available to
re-enable later. Sources use GitHub `owner/repository` names. Skill names must
start with a lowercase letter or digit and contain only lowercase letters,
digits, dots, underscores, or hyphens; duplicate names are rejected.

```text
source/repository|enabled-skill
# disabled: source/repository|disabled-skill
```

```sh
bash scripts/sync-agent-skills.sh --dry-run
bash scripts/sync-agent-skills.sh
```

Afterward, `scripts/audit-skills.sh --fix-symlinks` exposes the canonical skills
to installed agent providers.

Without repair flags, the skills audit is read-only. `--fix-symlinks` creates
missing links and repairs links whose targets are inside the canonical store;
real files, directories, and foreign links are reported as conflicts and
preserved. `--fix-lockfile` removes entries for missing skill directories but
does not replace a symlinked lockfile.

`--clean-stale` only removes listed agent directories whose `skills/` subtree
contains verified managed links and empty directories. Real content, foreign
links, and sibling directories such as `skills-backup` are preserved.
`--quiet` changes output only; it does not disable requested repairs or hide
warnings as successful checks. Python 3 is required for link-target checks,
and jq is required for manifest/lockfile verification.

Setup and update stop if synchronization or repair fails. Resolve conflicts
manually rather than deleting local skills to make the audit pass.

## iTerm2

Settings → enable *Load preferences from a custom folder or URL* → `~/.dotfiles/iterm/com.googlecode.iterm2.plist`
