# roadmaps

Run as `python3 ~/.dotfiles/scripts/roadmaps.py [PATH ...] [--check | --sync] [--strict] [--today YYYY-MM-DD]`.

Each path is a repository or a folder whose direct non-hidden subfolders are
repositories. Without paths, it reads `~/.config/dotfiles/roadmap-paths`, one
path per line, allowing `~`, blank lines and lines beginning with `#`. Use the
requested paths; do not broaden the search or change this machine-local
configuration to make a run succeed.

It finds skill-managed `roadmap.md` or `roadmaps.md` files, in any casing, at
the root or in `docs/`, `tasks/` or `planning/`. Briefs live in `roadmap/`
beside the index, or at the repository root.

## Report

The overview and check modes are read-only. No mode fetches, executes project
code, migrates layouts or changes item statuses. The overview shows overdue
items and items due within seven days, work awaiting acceptance, active work
and validation errors. Quiet roadmaps appear under "Nothing needs attention",
with a count of ready items when present.

For detailed validation, use `roadmaps --check .` or `roadmaps . --check`.
Without paths, `roadmaps --check` checks the configured locations. It uses the
same discovery, so no index or brief-directory flags are needed. It reports
each error and warning, and identifies valid roadmaps by their index path.
Overview errors include a shell-quoted check command for the affected repository.

Add `--strict` only with `--check`, for example `roadmaps --check --strict .`,
to include heuristic warnings about summaries, acceptance criteria, progress,
routine logs, backlinks and code pointers. Use it when preparing a handoff or
reviewing brief quality, not as a requirement for the normal overview. Warnings
alone do not change the exit status, and strict checks never write files.

Use `roadmaps --sync .` (or `roadmaps . --sync`) to update generated index blocks,
then validate. Sync requires explicit paths and never reads the configured list.
A collection path updates all discovered roadmaps in that collection. Briefs,
statuses and handwritten index sections stay unchanged. Missing brief directories,
invalid front matter or damaged markers block the affected update; other validation
errors are reported after syncing, so exit 1 can accompany index changes.
Before an agent runs sync, show the target scope and obtain approval covering
these writes. A direct request to sync those paths supplies that approval.

Progress identifies the location being searched or checked, only on terminal
stderr; it is cleared when that operation ends. Ctrl+C clears progress and exits
130 without a traceback.

Exit status 1 means an invalid date, missing configuration, read failures or
roadmap errors; argument-parsing errors exit 2. In check mode, missing directories
and paths without managed roadmaps also exit 1; the same applies in sync mode.
In overview mode, missing directories are notes and "No roadmaps found" can
exit 0, so success does not prove the requested roadmap was checked. Report the
actual cause. Do not repair or implement roadmap items merely because they
appear in this report.
