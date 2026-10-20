# Roadmap

Dotfiles that set up Apple Silicon Macs for development and keep them current
across several machines. The roadmap tracks deferred improvements and recurring
reviews that keep the repo in step with the tools and agents it configures,
favouring portable, auditable changes over one-off fixes on a single machine.

## How this roadmap works

- Each item has a brief in [roadmap/](roadmap/). Its front matter (`title`, `status`,
  `summary`, optional `kind`, `depends_on`, `blocked`, `when`, `due`) is the only
  record of status. To work on an item, read its brief; this file is only the map.
- Statuses: idea, planned (committed, decisions open), ready (fully specified),
  active (being implemented), verifying (built; checks or acceptance outstanding),
  done, dropped. Being listed, even as ready, does not authorise implementation.
- The block between the roadmap markers is generated; change briefs, not its
  lines. Now: active and verifying. Next: planned and ready items without a
  `when` condition, unordered. Later: ideas, and committed items the owner
  parked with `when`. Real sequencing is `depends_on`; there is no ranking.
  Routines (`kind: routine`) recur `every` interval and never finish: doing one
  means a dated line in its `## Log`, a new `last_done` and removing any `due`
  (which overrides the computed date). `due` is a target date; nothing runs by
  itself, `ready` lists what is due.
- The owner decides commitment, readiness, parking, acceptance and dropping, and
  assigns work; agents propose. `active` reports who is working on an item; it
  is not a lock, so ask before taking over an active item.
- Done and dropped briefs move to [roadmap/archive/](roadmap/archive/) after the owner
  accepts them. Shipped behaviour belongs in the maintained docs, not in briefs.

<!-- roadmap:start -->
<!-- Generated from brief front matter by the roadmap skill;
     edits here are overwritten on sync. -->

## Now

- [Move the global pnpm default to 12](roadmap/pnpm-12-default.md) · verifying · investigation · Decide when to switch corepack's global pnpm default from 11 to 12, then check the package-manager scripts against it.

## Next

_Nothing committed._

## Routines

- [Agent ecosystem review](roadmap/agent-ecosystem-review.md) · ready · routine · Check where the agents we use read instructions, skills, hooks and MCP config, and keep sync and audit in step. · every 1m · due: 2026-11-20
- [Tool updates review](roadmap/tool-updates-review.md) · ready · routine · Read upstream changelogs for the tools this repo installs and wraps, and act on changes that affect it. · every 1m · due: 2026-11-20

## Later

- [Shell history sync between Macs](roadmap/shell-history-sync.md) · idea · investigation · Merge zsh history between my Macs on demand over the local network, with no cloud service.
- [Verify the Node 26 LTS switch](roadmap/node-26-lts-switch.md) · planned · investigation · Check that the first dotfiles-update after Node 26 becomes LTS installs it cleanly with corepack, globals and package managers working. · when: Node 26 becomes LTS (planned 2026-10-28) and dotfiles-update has run
<!-- roadmap:end -->

## Ideas

<!-- One-liners not yet worth a brief: "- **Name**: why. When: entry condition." -->

- **Drop redundant zsh plugin clones**: oh-my-zsh now ships zsh-autosuggestions and zsh-syntax-highlighting, so the clones in `install-zsh-plugins.sh` duplicate them. When: next zsh setup change.
- **oh-my-zsh update cooldown**: `upgrade.sh -c <days>` would match the release-age cooldowns used for package managers. When: anytime.
- **Corepack `devEngines.packageManager`**: corepack 0.36 reads it when `packageManager` is missing, but `audit-repo.sh` and `_detect-pm.sh` don't. When: a project uses it.
- **Bun 1.4 support**: use `bun audit fix`, check the `bun outdated` parsing after the Rust rewrite, and fix the outdated "bun does not support catalogs" message. When: Bun is installed on a machine.
- **codex-resume queue start**: the experimental `thread/queue/start` or `codex queue` could send right away instead of waiting for the app. `--reset` also ignores `rateLimitsByLimitId`. When: next codex-resume change.
- **Claude skill folders**: make sure the skills audit never touches `~/.claude/skills/synced` or `.trash`. When: before relying on claude.ai skill sync.
- **COPILOT_HOME warning**: setting it stops Copilot from reading `~/.agents/skills`, so the audit could warn when it is set. When: next time `audit-skills.sh` changes.
- **Manage agent hooks, MCP and plugins**: Claude, Codex and Copilot all support user-level hooks and MCP. Keep files that hold secrets out of Git. When: a hook or MCP server is wanted on every machine.
- **Frecency directory jumping**: [zoxide](https://github.com/ajeetdsouza/zoxide) (or similar) jumps to frequently used directories by partial name and works with fzf. It needs a Brewfile entry and an init line in `zsh/conf.d/`. When: anytime.
- **Per-directory environment**: [direnv](https://direnv.net/) loads environment variables and tokens for each project, and could extend the work/personal split beyond Git identity. It needs a Brewfile entry and `eval "$(direnv hook zsh)"` in `env.zsh`. When: a project needs its own environment variables or tokens.

## Not doing

<!-- Rejected directions and why, so they are not proposed again. -->

- **dotbot-brew / dotbot-git plugins**: these would make `./install` slow, need network access and rely on small, barely maintained plugins. Setup and update already run `brew bundle` and `install-zsh-plugins.sh`, and keeping links separate from installs is deliberate.
- **Shell start-up rewrite (lazy loading, zimfw)**: an interactive shell starts in about 0.1 s with the compinit and zcompile shortcuts in place, so there is little to gain.
