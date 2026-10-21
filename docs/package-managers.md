# Package-manager commands

[README](../README.md#package-manager-commands)

The README lists the commands. This page covers updates, pnpm workspaces,
local libraries, and how `dotfiles-update` keeps package managers and Node
current. The commands support npm, Bun and pnpm 10.29 or newer; options that
need a newer pnpm say so.

## Updates and audits

`pmu` respects configured release-age cooldowns. `--cooldown <days>` overrides
them, including `--cooldown 0` to disable the cooldown. Use `--yes` only when
you intend to skip confirmation. `pma --deep` additionally refreshes pnpm's
lockfile before auditing; it is not a read-only check. `pma` applies fixes
straight away; `pma --dry-run` only reports, and can't be combined with `--deep`.

With pnpm 12.7 or newer, `pmu --actions` updates the GitHub Actions in
`.github/workflows` instead of packages. pnpm finds the updates, applies its own
`minimumReleaseAge` (earlier pnpm 12 releases ignore it for actions), and pins
each updated action to a commit hash with the version in a comment. It stays
within the current major unless `--major` is given, and accepts one action name
such as `actions/checkout`. pnpm reads release dates from temporary Git clones,
so it needs access to github.com.

## pnpm workspaces

In pnpm workspaces, `pmi` and `pmr` use the current member or infer an existing
dependency's target from the root. Ambiguous targets fail rather than guessing.
For pnpm, `--root` explicitly selects the root package. Unknown installs at the
root stay at the root; catalog entries change only with an explicit version.
Use `pmi --help`, `pmr --help`, or `pmu --help` for details.

## Local libraries

`pmi ../lib` adds a local library as a permanent dependency, linked rather
than copied: `link:../lib` in pnpm, `file:../lib` in npm and `../lib` in Bun.
In a pnpm workspace it goes into the current member. Commit it only when the
library always lives next to the project, as in a monorepo; `pmu` and `pmc`
leave such entries alone.

To try a local library in place of the installed version, run `pml ../lib`,
and `pml --undo ../lib` to go back. It works with npm and pnpm 11 or newer;
Bun and Yarn are not supported. pnpm's own undo leaves a `link:` dependency it
added in `package.json` before 11.28 and 12.7, so `pml --undo` removes it there. pnpm records the link in tracked files until
the undo, which leaves nothing to commit.

## Package-manager versions

In zsh, `npm` and `npx` use the npm installed in the active fnm Node version,
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

## Node versions

`dotfiles-update` keeps every installed fnm Node major current. The default
moves to the newest LTS; every other major moves to its newest patch.

Each Node version then gets the newest npm that is older than the cooldown and
whose `engines.node` accepts that Node version. When npm drops a Node major,
that major stays on the last npm supporting it and the update warns. Node
older than 18, which no npm 10 supports, keeps its npm with a warning. Global
packages are only updated where npm is 11.10.0 or newer, because older npm
ignores `min-release-age`.

`dotfiles-audit` notes installed majors that are past end-of-life in Node's
release schedule. The note is not a warning, since a project may still need
one.
