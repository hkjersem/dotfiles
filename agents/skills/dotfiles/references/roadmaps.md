# roadmaps

Run as `python3 ~/.dotfiles/scripts/roadmaps.py [PATH ...] [--check] [--today YYYY-MM-DD]`.

Each path is a repository or a folder whose direct non-hidden subfolders are
repositories. Without paths, it reads `~/.config/dotfiles/roadmap-paths`, one
path per line, allowing `~`, blank lines and lines beginning with `#`. Use the
requested paths; do not broaden the search or change this machine-local
configuration to make a run succeed.

It finds skill-managed `roadmap.md` or `roadmaps.md` files, in any casing, at
the root or in `docs/`, `tasks/` or `planning/`. Briefs live in `roadmap/`
beside the index, or at the repository root.

## Report

The command is read-only: it does not fetch, execute project code, sync generated
lists, migrate layouts or change item statuses. It shows overdue items and items
due within seven days, work awaiting acceptance, active work and validation
errors. Quiet roadmaps appear under "Nothing needs attention", with a count of
ready items when present.

For detailed validation, use `roadmaps --check .` or `roadmaps . --check`.
Without paths, `roadmaps --check` checks the configured locations. It uses the
same discovery, so no index or brief-directory flags are needed. It reports
each error and warning, and identifies valid roadmaps by their index path.
Overview errors include a shell-quoted check command for the affected repository.

Progress identifies the location being searched or checked, only on terminal
stderr; it is cleared when that operation ends. Ctrl+C clears progress and exits
130 without a traceback.

Exit status 1 means an invalid date, missing configuration, read failures or
roadmap errors; argument-parsing errors exit 2. In check mode, missing directories
and paths without managed roadmaps also exit 1. In overview mode, missing
directories are notes and "No roadmaps found" can exit 0, so success does not
prove the requested roadmap was checked. Report the actual cause. Do not repair
or implement roadmap items merely because they appear in this report.
