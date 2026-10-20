# Package-manager commands

[README](../README.md#package-manager-commands)

The README lists the commands. This page covers updates, workspaces and how
npm, pnpm, Yarn and Bun versions are chosen.

## Updates and audits

`pmu` respects configured release-age cooldowns. `--cooldown <days>` overrides
them, including `--cooldown 0` to disable the cooldown. Use `--yes` only when
you intend to skip confirmation. `pma --deep` additionally refreshes pnpm's
lockfile before auditing; it is not a read-only check.

## pnpm workspaces

In pnpm workspaces, `pmi` and `pmr` use the current member or infer an existing
dependency's target from the root. Ambiguous targets fail rather than guessing.
For pnpm, `--root` explicitly selects the root package. Unknown installs at the
root stay at the root; catalog entries change only with an explicit version.
Use `pmi --help`, `pmr --help`, or `pmu --help` for details.

## Package-manager versions

In zsh, `npm` and `npx` use the npm bundled with the active fnm Node version,
unless a parent `package.json` has a `packageManager` field; then they run
through corepack. `pnpm` and `yarn` always run through corepack. Outside pinned
projects, corepack uses its saved default version.

`dotfiles-update` moves the pnpm and Yarn defaults to the newest release in
their current major that is older than the `~/.npmrc` release-age cooldown; a
default that is still inside the cooldown is flagged and left unchanged. These
defaults are shared by every fnm Node version, while corepack itself and its
`pnpm`/`yarn` shims are installed per Node version; the audit checks each
corepack version. New majors are reported by both the update and
`dotfiles-audit`, and become warnings when more than one major behind. To
switch manually, run `corepack install -g pnpm@<version>`.

Bun is not managed by corepack; every installed Bun, including one per fnm
Node version, gets the same new-major check.
