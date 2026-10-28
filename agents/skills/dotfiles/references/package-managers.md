# pmu and pma

`pmu` and `pma` are the user's wrappers around the project's package manager.
Both detect bun, pnpm or npm from the nearest `packageManager` field or
lockfile at or above the current directory. `pmu` then works from that project
root; `pma` runs in the current directory, so run it from the project or
workspace root.
Details: `~/.dotfiles/docs/package-managers.md`.

## pmu: update dependencies

Run as `bash ~/.dotfiles/scripts/package-manager/run.sh [options] [package]`.

Unlike `npm update` or `pnpm update`, it:

- skips versions released more recently than the configured release-age
  cooldown, so brand-new releases are not installed;
- stays within the current compatible range (the major version, or the minor
  for 0.x) unless `--major` is given;
- writes the new versions into every `package.json` in a workspace and into
  pnpm catalogs (`pnpm-workspace.yaml`);
- then runs the package manager's install to update the lockfile and
  `node_modules`.

With pnpm 12.7 or newer, `--actions` updates the GitHub Actions in
`.github/workflows` instead, pinned to commit hashes.

1. Run with `--dry-run` and show the planned updates.
2. After the user approves them, run the same options with `--yes`. Without
   `--yes` it asks for confirmation, and with no answer it aborts.

It respects the release-age cooldown configured for the project or user. Add
`--major` or `--cooldown AGE` only when the user asks for it. `--help`
lists the options.

## pma: audit dependencies

Run as `bash ~/.dotfiles/scripts/package-manager/audit.sh [options]`.

It runs the package manager's own audit with the right fix flags for its
version:

- npm: `npm audit fix --package-lock-only --ignore-scripts`, which changes
  only the lockfile.
- pnpm 10: `pnpm audit --fix`, which adds overrides, then
  `pnpm install --lockfile-only`.
- pnpm 11 and newer: `pnpm audit --fix=update`, which updates the vulnerable
  dependencies and can change `node_modules`, then
  `pnpm install --lockfile-only`.

For npm and pnpm, `pma` without options applies fixes and updates the
lockfile. When the user asks to check or report vulnerabilities, run
`--dry-run`, which only reports. Run without it only when the user asks to fix
them, and add `--deep` (pnpm, refreshes the whole lockfile) only when they ask
for that too. For bun it only reports, with or without `--dry-run`: say that
bun vulnerabilities must be fixed by hand.

## Report

Show what changed in `package.json` files, `pnpm-workspace.yaml` and the
lockfile. After `pma` fixes, tell the user to run the install before testing,
since the fixes may only be in the lockfile; `pmu` already installed. Do not
commit.
