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
  `agents/skills/skills.txt`. Check the authored-skill eval catalog and follow
  repeated regressions into a durable safeguard.
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
7. Review regressions found since the previous run, using task briefs, test/CI
   outcomes and eval changes. Corrections within a session are left to each
   agent's own memory features; do not mine conversations or session history
   here. For a repeated or costly agent miss, choose
   the narrowest durable fix: a behavior test, repository check/instruction or
   skill eval. Run `python3 scripts/skill-evals.py <skills> --runs 2 --grade --model <runner>
   --grader-model <grader>` for every authored skill (it uses AI credits; ask
   first), with the model pair from the previous eval Log entry. Record each
   case's gap between the arms and the pair in the Log, and compare the gaps
   with the previous entry. If a model was retired, pick its replacement and
   record the new results as the new baseline. Tighten or remove assertions
   listed under "Passed in every run". Defer larger work to a separate roadmap
   item.
8. Report findings and proposed changes.
9. Add a dated line to the Log with the outcome, including whether a known
   regression recurred and whether its safeguard prevented it.

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
- 2026-11-10: First graded eval baseline, 2 runs per arm, runner
  `gpt-6.1-sol`, grader `claude-sonnet-5.5`. Gaps are with-skill minus
  without-skill pass rates in percentage points; `g` marks guard cases.
  - blast-radius 1–3: +8, +36, +17. dotfiles 1–3: +75, 0g, +80. test-plan
    1–4: 0g, +62, 0g, 0g. blast-radius 3 and dotfiles 1 predate the last
    skill edits.
  - agents-md-repair 1–5: 0, +19, 0, 0, 0. handoff 1–5: +20, +36, +40, +21,
    +50. repo-audit 1–5: +8, 0, +43, +40, +12.
  - roadmap 1–6: +40, +30, 0, +12, 0, +40. tidy-diff 1–11: 0, +42, 0, 0, +17,
    0, +8, +29, +17, 0, 0.
  - `gpt-6.1-sol` never ends a `copilot -p` reply with a question, so
    assertions check that a decision is left to the user, not how it is asked.
  - Fixed: roadmap 6 applied `when:` itself (+40 to +50) and roadmap 3 never
    offered migration or a home for the architecture decisions (0 to +25);
    the manual and repair guide now cover both.
  - The other 12 cases with no gap test restraint or general practice the
    runner already has, so they are now guards rather than tightened.
