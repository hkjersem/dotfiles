# Agent skills

[README](../README.md#agent-skills)

## Third-party manifest

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

## What belongs in a skill

Agents decide for themselves whether to load a skill, and sometimes don't. In
[Vercel's evals](https://vercel.com/blog/agents-md-outperforms-skills-in-our-agent-evals),
a small Next.js 16 suite targeting APIs missing from model training, an
unprompted docs skill was never invoked in 56% of cases and was no better than
no docs. A line in `AGENTS.md` telling the agent to use it raised the pass rate
from 53% to 79%, and a compressed docs index in `AGENTS.md` reached 100%. This
is one vendor's study, not a general rule.

- Use skills for multi-step jobs and best-practice guidance, such as the
  repo-authored skills and framework skills like `vue` or `nuxt`.
- Docs for framework versions newer than the model's training belong in the
  project's own `AGENTS.md`, for example via `npx @next/codemod agents-md` in
  Next.js projects, not in a skill or in `agents/AGENTS.md`.
- In projects that rely on a framework skill, name it in the project's
  `AGENTS.md`, e.g. "Explore the project first, then use the vue skill". Vercel
  found that "explore first" wording worked better than "you must invoke".

## Skills for dotfiles commands

The `dotfiles` skill lets agents in other repositories run `codex-resume`,
`prune_branches`, `audit-repo`, `pmu` and `pma`, for example "run dotfiles
prune branches". Its description makes agents load it only when a message
says "dotfiles" and asks for a command or task, and its body runs a
command only on a direct request, so mentioning one in discussion does
nothing. Each command has a reference file in `references/` with the
preview-before-change and approval steps agents follow. Keep command-specific
guidance there: add another command as a reference file and a row in its table,
not as a separate skill.

Other repo-authored skills may reuse these commands and consult their reference
files instead of duplicating logic. They follow their own triggers; the user
does not have to say "dotfiles" for such an integration. Only skills explicitly
intended for standalone distribution must avoid dotfiles dependencies.

The `repo-audit` skill uses `audit-repo --no-package-manager` as a local triage
helper when available, then investigates its leads. Snapshots and missing
tooling still support manual inspection; a missing or blocked check is reported.

Command reuse does not expand a task's permissions. Check whether a command
executes project code, contacts the network or changes files before using it,
and keep the task's approval boundaries. Use the script entry point, since
agent shells may not load interactive aliases. Report missing tools and access
failures rather than installing tools or working around restrictions.

## Skills from other repositories

`agents/skills/sources.txt` lists skill directories owned by other
repositories, one `~/path` per line. Each subdirectory containing a `SKILL.md`
is linked like a repo-authored skill, so the owning repository stays the only
copy. A directory that does not exist on a machine is skipped, and a link whose
external skill has disappeared or lost its `SKILL.md` is removed by the next
sync. Names that collide with repo-authored, manifest or already installed
skills are reported and left unchanged.

## Audit and repair

Without repair flags, the skills audit is read-only. `--fix-symlinks` creates
missing links and repairs links whose targets are inside the canonical store;
real files, directories, and foreign links are reported as conflicts and
preserved. When a repo-authored skill is removed or renamed, the next sync
removes its link from `~/.agents/skills`, and `--fix-symlinks` then removes
Claude Code's link to it. Codex and Copilot CLI read `~/.agents/skills` directly, so only
Claude Code gets links. `--fix-symlinks` also removes managed links left in
`~/.codex/skills`, `~/.copilot/skills`, `~/.gemini/skills` and
`~/.config/agents/skills`, which no agent in use needs.
`--fix-lockfile` removes entries for missing skill directories but
does not replace a symlinked lockfile.

`--clean-stale` only removes listed agent directories whose skill subtrees
contain verified managed links and empty directories. For most agents that is
`skills/`; `~/.gemini` also covers Antigravity's `antigravity/skills` and
`antigravity/global_skills`. Real content, foreign links, and sibling
directories such as `skills-backup` are preserved.
`--quiet` changes output only; it does not disable requested repairs or hide
warnings as successful checks. Python 3 is required for link-target checks,
and jq is required for manifest/lockfile verification.
