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

## Eval and feedback loop

Each repo-authored skill has `evals/evals.json`: realistic task prompts,
expected outcomes, and observable assertions. The repository test
`test_repo_authored_skills_have_valid_eval_cases` checks that every authored
skill has non-empty, valid evals and that referenced fixture files exist. CI
checks the catalog's structure only; it does not run an agent or claim that a
skill passes its evals.

Run evals from the repository root with
`python3 scripts/skill-evals.py <skill>[:id]`; without arguments it lists the
cases, and `--dry-run` shows the planned runs. Each case runs in a fresh Copilot
CLI session (`copilot -p`, memory off) started in a workspace outside this
repository, so its instructions and docs do not help either run. Some models
never end a `copilot -p` reply with a question, even when told to, so
assertions check that nothing is applied without approval rather than how the
agent asks. A case's
`setup` field names a script, relative to the skill, that builds a disposable
repository there; `workdir` names the subfolder to start in. Without `setup`,
the case's `files` are copied in. One run leaves the skill available rather
than forcing it, so it also shows whether the skill triggers; the baseline adds
`--excluded-tools skill`. Use `--runs 3` to see variance and `--model` to check
another model. The command flags failed runs, skill use in the wrong arm, and
changes to this repository during the runs.

Add `--grade` to grade the runs afterwards, or grade an earlier results folder
with `--regrade <folder>`. Each run is copied to a temporary folder
whose name does not reveal the arm, and a fresh Copilot CLI session with skills
excluded grades it against the case's assertions: the burden of proof is on the
assertion, claimed checks must show in the transcript, and an "unchanged"
assertion fails on any write, even one later undone. The transcript still shows
whether the skill was loaded, so grading is blind to the folder, not to the
behaviour. The grade is checked to cover every assertion and saved as
`grading.json` beside the run, with any feedback on the eval itself. The summary
compares the two arms per case, which separates a skill improvement from a task
the agent already handles, and lists assertions every run passed: they do not
separate the arms and should be tightened or removed. Mark a case
`"guard": true` when the agent is expected to pass it without the skill, such
as a trivial change that must not be over-planned; it still guards against
the skill making things worse, but is left out of that list. Graders use AI
credits too. `summary.json` records both arms' scores, the gap between them and
the runner and grader models.

Pin both models when comparing over time (`--model` and `--grader-model`, the
grader from a different model family than the runner); otherwise a new CLI
default looks like a skill change. Compare the gap rather than absolute pass
rates, since it changes less between models. When a pinned model is retired,
run one baseline with its replacement and compare later runs to that. Record
assertion
outcomes and concrete evidence; do not commit transcripts, private
conversations, or unrelated task data.

Add or revise an eval when a skill-related miss or correction reveals a
generalizable failure mode. Keep assertions behavioral and discriminating:
they should expose the failure, not require matching exact prose. For code
behavior, prefer a regression test; for a repository-wide invariant, prefer an
automated check or concise instruction. Use a skill for reusable multi-step
judgment that neither test nor rule can capture. Re-run the focused eval after
the change, with a no-skill comparison where practical. Defer broader work to
the roadmap rather than expanding an unrelated task.

## Skills for dotfiles commands

The `dotfiles` skill lets agents in other repositories run `codex-resume`,
`prune_branches`, `audit-repo`, `roadmaps`, `pmu` and `pma`, for example "run dotfiles
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
