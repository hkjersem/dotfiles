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
preserved. Codex and Copilot CLI read `~/.agents/skills` directly, so only
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
