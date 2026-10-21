# pmu and pma

Both detect bun, pnpm or npm from the project's `packageManager` field or
lockfile, and run in the current directory. Details:
`~/.dotfiles/docs/package-managers.md`.

## pmu: update dependencies

Run as `bash ~/.dotfiles/scripts/package-manager/run.sh [options] [package]`.

1. Run with `--dry-run` and show the planned updates.
2. After the user approves them, run the same options with `--yes`. Without
   `--yes` it asks for confirmation, and with no answer it aborts.

It respects the release-age cooldown configured for the project or user. Add
`--major` or `--cooldown <days>` only when the user asks for it. `--help`
lists the options.

## pma: audit dependencies

Run as `bash ~/.dotfiles/scripts/package-manager/audit.sh [options]`.

For npm and pnpm, `pma` without options applies fixes and updates the
lockfile. When the user asks to check or report vulnerabilities, run
`--dry-run`, which only reports. Run without it only when the user asks to fix
them, and add `--deep` (pnpm, refreshes the whole lockfile) only when they ask
for that too. For bun it only reports, with or without `--dry-run`: say that
bun vulnerabilities must be fixed by hand.

## Report

Show what changed in `package.json` files and the lockfile, and remind the
user to install and run the project's tests. Do not commit.
