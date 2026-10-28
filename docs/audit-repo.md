# Repository triage

[README](../README.md#repository-triage)

## Workspace disk usage

When additional worktrees exist, the default audit lists registered paths and
branches without scanning their contents, with a hint to run `--workspaces`
for more detail. Otherwise, this section is hidden.

`audit-repo --workspaces` adds a size-ranked report of all registered worktrees
of the audited repository, including its main checkout and linked worktrees
outside that directory. It does not discover unrelated clones or scan home
directories. The opt-in scan includes generated artifacts but excludes `.git`
metadata, using the same native allocated-size scanner as `files sizes`.

Assessments reuse the stale-branch plan and shared worktree checks from
`prune_branches`, including local/ignored files, hidden index flags and
worktree-only history. The audited checkout and locked worktrees are kept;
detached HEADs are flagged for review. Remote assessments use the last fetched
refs, and unclassified worktrees are not described as safe to remove. With
`--no-package-manager`, remote classification stays disabled while local
worktree checks and sizing still run. No fetching or deletion is added.

Permission-denied entries give incomplete sizes and warnings; missing
registered paths are shown explicitly. Age is not used as evidence of safety.
Large worktrees can take time; delayed terminal progress is shown. Existing
`prune_branches` remains responsible for any approved cleanup.

## Package-manager execution

Use `--no-package-manager` for local checks without running a package manager or
Node. It skips vulnerability and outdated queries, the current Node version and
pnpm's global cooldown lookup; skipped checks are reported, not counted as clean.
It also skips stale-branch classification, which can run configured Git hooks
and merge drivers. Git lazy fetching and optional index writes are disabled;
missing local objects fail the check instead of being downloaded.
File-based package metadata, project/home cooldown configuration, Git signals,
roadmap validation, test discovery and build configuration checks remain.

Without that flag, the command runs the package manager selected by the target
repository. Corepack can download and execute the selected version or URL.
Package queries can contact registries and write caches, even though no project
tests run and project files are not changed. `--skip-security` skips only the
vulnerability query; it does not disable outdated or configuration commands.

## Documentation checks

Documentation checks cover tracked and untracked files that Git doesn't ignore.
They flag conventional files with the wrong case or in the wrong place (`README.md`,
`AGENTS.md`, `CLAUDE.md`, `LICENSE`, `.github/copilot-instructions.md` and
similar), a `DESIGN.md` without design tokens, paths that differ only in case,
and Markdown links whose case differs from the file. macOS ignores case, so
these work locally but break on Linux and on Git hosts.

## Roadmap checks

The audit uses the roadmap skill's read-only `check` validation for managed
roadmaps. Candidate roadmap files require Python 3 to inspect; repositories
without any candidate do not need it. It shares discovery with `roadmaps`: a
`roadmap.md` or `roadmaps.md` (any casing) at the root or in `docs/`, `tasks/`
or `planning/`, containing `<!-- roadmap:start -->`. Briefs live in `roadmap/`
beside the index, or at the repository root. Directory names are also matched
case-insensitively, retaining their actual spelling.

It checks markers, brief front matter, statuses, dependencies, archive placement,
links and whether the generated lists match the briefs. Errors fail the audit;
other roadmap formats are left alone. It never syncs or migrates a roadmap.
An unreadable roadmap or unavailable validator fails the check. `.env` briefs
are not read; their presence makes roadmap validation incomplete and fails it.

## Source files and packages

Source-file statistics use tracked files, excluding known generated artifacts,
symlinks, and missing files. `.env` files are never read by any check, since
they may hold secrets. Files the audit has no permission to read are left out
of the statistics with a note, but an unreadable documentation file fails the
documentation check.

A project is labelled a workspace only when `pnpm-workspace.yaml` or
`package.json` `workspaces` lists packages. With a root `package.json`, it also
checks package metadata, cooldowns, and JS/TS tooling; these checks require jq.
Without `--no-package-manager`, npm and pnpm provide structured vulnerability
and outdated-package results.
Bun and other managers' audit/outdated results are not parsed by this tool.

It also reports a Node runtime vendored into `node_modules` by
`devEngines.runtime.onFail: "download"`. A build linked out of the global
package-manager store is flagged, because sandboxes commonly deny executing that
tree and every child process resolving a bare `node` through
`node_modules/.bin` then fails. Setting `runtimeOnFail: ignore` in
`pnpm-workspace.yaml` keeps the version manager's Node instead.

## Reading the results

Findings are triage indicators, not a security verdict or measured test
coverage. Git-history signals (churn, ownership concentration, fix and
firefighting commits) and missing conventionally named test files are warnings,
since they suggest a closer look rather than prove a defect; ownership
concentration is expected in a solo project.

`--since` controls churn and fix/revert history; ownership uses all history,
and velocity uses the last 12 complete calendar months, including inactive
months. JSONC and inherited TypeScript settings are reported as unevaluated.
