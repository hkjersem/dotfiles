---
title: Move the global pnpm default to 12
status: idea
summary: Decide when to switch corepack's global pnpm default from 11 to 12, then check the package-manager scripts against it.
kind: investigation
---

# Move the global pnpm default to 12

[Roadmap](../ROADMAP.md)

## Outcome

The global pnpm default is on pnpm 12, and the `pm*` commands still work.

## Context

`scripts/corepack-defaults.sh` updates pnpm only within its current major
(11.x) and reports 12.x as a new major. `audit.sh` warns about it. Switching
is a manual choice: `corepack install -g pnpm@<version>`. Projects that pin
`packageManager` keep their own version either way.

The 2026-10-20 tool updates review read pnpm 12's source and found nothing
that breaks the repo's scripts, but nothing has run against it. The parts
most likely to be affected are `scripts/package-manager/pnpm-update.sh`
(the `outdated --json` format and catalogs), `install.sh`, `remove.sh`,
`clean.sh` and `audit.sh` (the `--fix` flag handling).

pnpm 12 overlaps with tools this repo already uses for the same jobs:

- **Global shims (on by default):** the `globalShims` setting defaults to
  `{ node: true, deno: true, bun: true }`. Inside a project, a pnpm-installed
  `node` runs the version pinned in `devEngines.runtime` or
  `engines.runtime`, and from 12.7 also `.nvmrc` or `.node-version`. fnm
  (`zsh/conf.d/fnm.zsh`) does the same job. Per the release notes, shims are
  created only when asked, or for runtimes installed globally with pnpm, so
  they probably stay inactive here. That is unverified.
- **Package-manager provisioning:** pnpm 12 can install and pin npm, Yarn and
  Bun (`pnpm shim add`, `pnx yarn@4`, `pnpm add -g yarn`). That overlaps with
  corepack and the shell aliases in `zsh/conf.d/aliases.zsh`.
- **Stricter behaviour:** unknown `pnpm-workspace.yaml` settings warn, and
  fail when the project pins pnpm 12. `--frozen-lockfile false` is gone.
  Git dependencies always resolve to HTTPS, so private repos over SSH need a
  global `url.<ssh>.insteadOf` rewrite.

Release notes: the pnpm 12.0.0 to 12.14.0 GitHub releases.

## Decisions

- 2026-10-20: Keep fnm for Node and corepack for package-manager switching.
  pnpm 12's shims and provisioning are not adopted. (proposed)

## Open questions

- Switch now, or wait for a few 12.x patch releases?

## Acceptance

- [ ] `pnpm -v` outside a pinned project shows 12.x.
- [ ] `pmu`, `pmi`, `pmr`, `pmc` and `pma` work in a pnpm workspace with a
      catalog.
- [ ] `bash scripts/audit.sh` shows no pnpm major warning.
- [ ] In a project with `.nvmrc`, `command -v node` and `node -v` still
      resolve through fnm, not a pnpm shim, and `pnpm shim ls` is empty.
- [ ] `yarn -v` in a project pinning Yarn still resolves through corepack.
