---
title: Agent ecosystem review
status: ready
summary: Check where the agents we use read instructions, skills, hooks and MCP config, and keep sync and audit in step.
kind: routine
every: 1m
last_done: 2026-10-20
---

# Agent ecosystem review

[Roadmap](../ROADMAP.md)

## Outcome

Each run confirms that the repo's agent setup matches what the agents we use
expect today, and lists changes worth adopting. It covers:

- **Locations:** global instruction files, skill directories, and deprecated
  paths that should be cleaned up, as with Codex moving from `~/.codex/skills`
  to `~/.agents/skills`.
- **Skills:** changes to the skill format and frontmatter, plus updates to the
  repo-authored skills and to the third-party skills listed in
  `agents/skills/skills.txt`.
- **Hooks, MCP servers, plugins and custom agents:** new or changed features in
  the agents we use that should be managed here. The repo manages none of them
  yet, so a finding becomes a proposal, not a change.
- **Agents:** which agents are in use. Add providers that we have adopted and
  remove providers that we no longer use.

## Steps

1. Refresh this brief's scope. Run `git log --since=<previous run> --stat` and
   check which agents are in use (`scripts/lib/agent-instructions.sh`,
   `scripts/lib/tool-updates.sh`, the agent home directories on this machine).
   Update the lists in this brief to match.
2. Read the changelogs and documentation for Claude Code, Codex, Copilot CLI and
   any other agent in use, covering the period since the previous run.
3. Compare with the repo:
   - instruction targets: `scripts/lib/agent-instructions.sh`
   - skill providers, legacy providers and stale agent directories: the
     `PREFERRED_PROVIDERS`, `LEGACY_PROVIDERS` and `AUTO_DELETE_CANDIDATES`
     lists in `scripts/audit-skills.sh`
   - skill sync: `scripts/sync-agent-skills.sh`, `agents/skills/`
   - instructions: `agents/AGENTS.md`
4. Check what other installers write and where, and that nothing shows twice:
   - `npx skills`: which agent folders it links into for `-g` installs, and
     whether it still treats `~/.agents/skills` as the canonical store.
   - nav-pilot (`nav-pilot list --installed`): it installs real agents, skills
     and instructions into `~/.copilot`, so `audit-skills.sh` must keep
     treating them as foreign content. Watch for new targets (it also supports
     OpenCode and pi) and name collisions with our skills.
   - cplt: its sandbox profile (`cplt --print-profile`) must still allow
     reading `~/.agents` and the instruction targets, or sandboxed agents lose
     our skills and instructions.
   - pnpm (12.11+): `pnpm install` links skills that dependencies ship under
     `skills/<name>/SKILL.md` into project agent folders such as
     `.claude/skills`, after `pnpm approve`. The `skills.dirs` setting picks
     the folders. Check which folders it targets by default, whether it now
     links without approval or into global folders, and that the links do not
     end up in Git.
5. Update third-party skills with `npx -y skills check -g`. Despite its name,
   it installs updates as well as listing them, as `dotfiles-update` does.
   Record which skills changed.
6. Run `bash scripts/audit.sh` on this machine to confirm the installed state.
7. Report findings with proposed changes. Larger changes become separate roadmap
   items.
8. Add a dated line to the Log with the outcome.

## Log

- 2026-10-20: First run, checked against the official docs.
  - Copilot reads `~/.agents/skills` natively (since 1.0.11), so the links in
    `~/.copilot/skills` duplicate it.
  - No agent we use reads `~/.config/agents/skills`.
  - Antigravity, the only user of `~/.gemini`, reads skills from
    `~/.gemini/config/skills` (not `~/.gemini/skills`) and instructions from
    `~/.gemini/AGENTS.md`.
  - Claude still reads only `~/.claude/skills`.
  - Codex moved to `~/.agents/skills`; its legacy links are already cleaned
    up.
  - `skills check -g` updated `nuxt`.
  - `audit.sh` shows only the pnpm warnings.
  - Hooks, MCP and plugins: new features exist, but the repo doesn't manage
    them, so nothing changes.
  - Fixed: Claude Code is now the only linked provider. `~/.copilot/skills`,
    `~/.gemini/skills` and `~/.config/agents/skills` became legacy
    directories whose managed links `--fix-symlinks` removes. Gemini and
    Antigravity are not used, so `--clean-stale` now also removes `~/.gemini`
    when it holds only managed links in its skill folders.
  - `npx skills` (1.7.2) installs global skills for Copilot, Codex, Gemini and
    Antigravity into `~/.agents/skills` only; only Claude Code gets links.
    nav-pilot installs real files into `~/.copilot` with no name clashes.
- 2026-10-20: VS Code reads `~/.copilot/skills`, `~/.claude/skills` and
  `~/.agents/skills` but lists each skill name once (61 unique names from 96
  folders), so the Claude links cause no duplicates. Dropped that check.
