---
title: Tool updates review
status: ready
summary: Read upstream changelogs for the tools this repo installs and wraps, and act on changes that affect it.
kind: routine
every: 1m
last_done: 2026-10-20
---

# Tool updates review

[Roadmap](../ROADMAP.md)

## Outcome

Each run lists new releases since the previous run for the tools the repo
installs, wraps or audits. For each release it notes changes that affect the
repo: new features that could replace custom code, deprecations, changed paths
or flags, and fixes that make workarounds unnecessary. Small fixes are done
during the run if the owner asks; larger work becomes separate roadmap items.

`dotfiles-update` already installs new versions. This review checks whether the
repo's scripts still match them.

## Steps

1. Refresh this brief's scope. Run `git log --since=<previous run> --stat` and
   compare the list below with `Brewfile`, `scripts/lib/tool-updates.sh`,
   `scripts/install-zsh-plugins.sh` and the `scripts/` wrappers. Add tools the
   repo now installs, wraps or audits, and remove those it no longer uses.
2. Find the previous run's date in the Log below. Read releases published since
   then (`gh release list -R <owner/repo>`, `npm view <pkg> time`), focusing on
   these tools and the files that depend on them:
   - `skills` CLI (`vercel-labs/skills`): `scripts/sync-agent-skills.sh`,
     `scripts/audit-skills.sh`, `macos/update.sh`
   - fnm, corepack, pnpm, Yarn, Bun, npm: `scripts/install-node.sh`,
     `scripts/corepack-defaults.sh`, `scripts/ensure-pm-config.sh`,
     `scripts/package-manager/`
   - Homebrew and `brew bundle`: `Brewfile`, `macos/applications.sh`,
     `macos/update.sh`
   - oh-my-zsh, zsh plugins and fzf: `scripts/install-zsh-plugins.sh`,
     `zsh/conf.d/`
   - dotbot: `install`, `install.conf.yaml`
   - Codex desktop app and its bundled CLI: `scripts/codex-resume.py`,
     `scripts/lib/codex-resume.swift`, `docs/codex-resume.md`. Look for
     changes to the queue API (`thread/queue/*`), session logs, the chat
     database and the input field. Then run `codex-resume --limits`,
     `codex-resume --check --chat <id>` (input-field path, read-only) and
     `codex-resume --now --chat <id> --message "Reply with OK only."`, which
     should report sending via the Codex queue.
3. Check the version baselines against the projects in `~/Developer` (skip
   `fagtorsdag` and `worktrees`). Read `packageManager`, `devEngines`,
   `engines.node`, `.nvmrc` and `.node-version`, and compare them with the
   previous run's baseline in the Log:
   - pnpm: the oldest pinned version against the floor in `AGENTS.md`. Propose
     raising the floor when old pins are gone; only the owner decides. A pin
     below the floor needs a fix or an exception.
   - Node: pinned majors against the majors fnm has installed. Flag majors
     that are end-of-life or within six months of it, and majors whose newest
     npm no longer supports them (`update-node-versions.sh` warns about this).
   - Yarn, Bun and npm pins: flag majors the wrappers or corepack defaults do
     not cover.
4. Ignore releases that change nothing the repo uses.
5. Report findings briefly. For each one, either propose a change or say it
   needs nothing.
6. Add a dated line to the Log listing the releases reviewed, the baselines
   and the outcome.

## Log

- 2026-10-20: `skills` CLI only, 1.6.0 to 1.7.4. Codex moved global skills to
  `~/.agents/skills`, so `audit-skills.sh` stopped linking into
  `~/.codex/skills` and now removes the legacy links. Nothing else needed
  changes. The other tools are not yet reviewed.
- 2026-10-20: Full run covering releases since 2026-07-20. Reviewed: fnm
  1.39.0 (no release); npm 11.19 to 12.2.1; corepack 0.36.0; pnpm 11.23 to
  12.14.0; Yarn 4.18.x; Bun 1.4.0 to 1.4.3; Homebrew 6.0.13 to 7.0.10; about
  190 oh-my-zsh commits; zsh plugins; fzf 0.74.2 to 0.74.5; dotbot (no
  change); Codex 0.145.0 to 0.163.0.
  - Fixed: `update.sh` now runs the oh-my-zsh updater with `-v minimal`,
    because a new banner line slipped past the `grep` filter.
  - Fixed: Node 26 becomes LTS on 2026-10-28, and `fnm --corepack-enabled`
    fails there (fnm#1469). `install-node.sh` now sets
    `FNM_COREPACK_ENABLED=false` and installs corepack itself.
  - Fixed: since pnpm 11.28, `pnpm -r outdated --json` keys can look like
    `name@version (dev)`; `pnpm-update.sh` now maps them to package names.
  - Open: `codex-resume` only finds uncompressed `.jsonl` session logs, but
    most Codex logs are now `.jsonl.zst`. Waits for the owner's codex-resume
    work.
  - Corepack stays on Node 26: it is still maintained, and the shell aliases
    and default management depend on it.
  - pnpm 12 breaks no repo script (verified against its source, not by
    running it).
  - Smaller findings went under Ideas in ROADMAP.md.
  - Checks: `codex-resume --limits` and `--list`, the Swift self-test (80/80)
    and the codex-resume unit tests (59) all pass. The live `--check` and
    `--now` runs are not done yet.
- 2026-10-20: Baseline. pnpm pins range from 10.29.3 to 12.9.1, three
  projects are on pnpm 10, and the oldest pnpm 11 pin is 11.1.1. The `pm*`
  commands pass smoke tests on 10.29.3, 11.1.1, 11.8.0 and 11.28.6; the floor
  in `AGENTS.md` is 10.29. Node: projects pin 24, except `unleash` (22.17);
  fnm has 22.23.3 and 24.22.0. Node 22 reaches end-of-life on 2027-04-30.
  Yarn: `unleash` pins 4.9.2. No project pins Bun or npm.
