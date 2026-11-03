# File reports

[README](../README.md#inspect-files-and-folders)

`files <command> [options] [path]` reports on the current folder by default.
Except for the global `caches` report, commands support `-r`/`--recursive`
and repeatable `--exclude GLOB`, matching an entry name or relative path.
Hidden and Git-ignored entries are included unless explicitly excluded.
Symlinks are never followed, including the target folder itself.
No inspected files are changed; `sizes` saves and expires its own metadata snapshots.

Zsh completes subcommand names and flags from the command's help. Folder
names use BSD `ls` colours (`LSCOLORS`), without trailing slashes. Empty-entry
results keep explicit `File` and `Folder` labels. Redirected output and
`NO_COLOR` remain plain.

## Commands

| Command | What it does |
|---|---|
| `files count -r` | Separate totals for files, folders, symlinks and other entries; excludes the root itself |
| `files large -r --min 100M` | Largest files first; size suffixes use powers of 1024 |
| `files changed -r --since 7d` | Files by modification time, newest first, with Norwegian timestamps; ages accept `m`, `h`, `d`, `w` |
| `files sizes` | Allocated file bytes per child folder, including its subfolders |
| `files empty -r` | Zero-byte files and genuinely empty folders, not folders made empty by exclusions |
| `files types -r` | Counts and bytes by final extension, case-insensitive; dotfiles without a suffix have no extension |
| `files duplicates -r` | SHA-256 content matches, grouped by size first; skips empty files and counts hard links once |
| `files caches` | Rank existing developer cache folders by allocated bytes, including nested contents |
| `files leftovers -r` | Possible agent/temporary leftovers with sizes, File/Folder, latest descendant modification and match reason |

Listings show up to 20 rows or duplicate groups; `--limit 0` shows all.
`count` always shows all totals.

## Exclusions

Add `--clean` to a path-based report to exclude generated directories and file
patterns shared with `wipe_clean` and `copy_clean`, such as `node_modules`,
`dist`, `.venv` and `*.tsbuildinfo`. The report marks when this filter is active.
It does not exclude Git metadata or apply `.gitignore`.

Use `--clean-git` for the same exclusions plus `.git` directories and worktree
pointer files at every depth. It does not hide `.gitignore`, `.github`, or
other Git-ignored project files. `leftovers` always excludes `.git`.

## Warnings and cloud-only entries

All reports summarize unreadable entries by reason. Add `--verbose` (`-v`) to
show each affected path; the report remains marked incomplete either way.
Cloud-only counts appear below the results with a `--local-only` hint;
`--verbose` adds the explanation of cloud traversal and duplicate hashing.
Affected child folder sizes show `Unknown`, not zero. An unreadable target
folder, other I/O errors, or detected changes during duplicate hashing fail
explicitly. Reports are snapshots, not a transactional view of changing folders.

Cloud-only entries are marked `[cloud-only]` wherever their paths are listed.
Use `--local-only` with any path-based report to exclude them, for example
`files large -r --local-only`. Detection uses macOS's dataless flag, not zero
allocated bytes or the folder name. Downloaded iCloud files remain included.
Cloud-only folders are never traversed, their contents are unknown, and
`duplicates` skips cloud-only files. The process disables automatic cloud
downloads for every report.

`~/Library/Mobile Documents` contains iCloud Drive (`com~apple~CloudDocs`)
and app-specific iCloud containers. It can contain both downloaded files and
cloud-only placeholders; it is not itself a cache or cleanup target.

## Folder sizes and cached results

`sizes` scans nested contents for totals unless `--direct` is set. Its `-r`
flag expands the listing rather than changing how totals are calculated.
Parent and nested folder totals overlap, so do not sum rows from `sizes -r`.
Size scans use macOS's bulk metadata API and retain folder totals rather than
the whole file list. No compiler or additional dependency is needed.

Allocated file-data bytes are reported by default, so sparse files count
only their allocated data. Use `--logical` to report full file lengths.
Shared APFS blocks can be counted more than once; outside duplicate
detection, hard-linked paths count separately. Duplicate savings are
estimates excluding hard-linked copies; APFS sharing may reduce actual savings.

Fresh size runs atomically save the full snapshot, including warnings, under
`${XDG_CACHE_HOME:-~/.cache}/dotfiles/files-sizes/`. Use `files sizes --cached`
to reuse it with a different `--limit` without inspecting the target folder.
Cached output shows its age; snapshots expire after 24 hours and are replaced
by fresh runs with the same folder and scan options, including recursion,
direct mode, logical size mode, local-only filtering and exclusions.
Missing, invalid or expired caches produce an error, never an automatic rescan.

Each `files sizes` invocation, including `--cached`, deletes snapshot files
last written at least 24 hours ago from this cache directory. No background
service runs; files remain while the command is unused. Cached results do not
reflect added, removed or changed files.

On a terminal, size scans lasting over two seconds show an updating progress
line with the current folder and entry count. It clears before the report or
on interruption; redirected output stays plain.

## Possible leftovers

`leftovers` matches agent configuration/session names (`.claude`, `.cursor`,
`.gemini`, `.copilot`, `.cursorrules`, `.aider*`), agent logs (`*.agent.log`)
and broad temporary/workspace names (`tmp.*`, `wt-*`, `agent-workspace*`,
`agent-work-*`, `nav-pilot-e2e-bin*`). These are candidates for inspection,
not proof that anything is disposable.

Only the requested directory is searched; `-r` discovers nested candidates.
Matching folders are shown once with their full included subtree size.
Symlink candidates are omitted. File contents are never read.

Add `--older-than 7d` to require no modification in the included subtree.
Last modification includes descendants, so this report performs an additional
metadata traversal beyond native sizing. Unreadable ages do not qualify for
an age filter and produce warnings.

Matches inside `node_modules`, `.cache`, `.local/share/fnm`,
`.vscode/extensions` and `Library/Caches` are hidden and counted separately;
`--verbose` includes them. Agent folders outside these tool-managed trees
remain visible, without implying they should be deleted.

## Developer caches

`caches` checks known default locations for npm, pnpm, Bun, Homebrew, pip, uv
(honouring `XDG_CACHE_HOME`), Go, Gradle, Cargo, Playwright and Xcode DerivedData.
It always scans recursively and does not use the size snapshot cache.
Missing folders are omitted; inaccessible folders show `Unknown` with warnings,
and symlink roots are reported but not followed. Custom tool cache locations
are not discovered, and no package managers or cleanup tools run.

This subcommand accepts `--limit`, `--verbose` and `--help`, not a target path.
When caches are found, it ends with a hint to preview cleanup with
`clean_caches --dry-run`.

## Examples

```sh
files large -r --local-only --min 100M ~/Downloads
files large -r --exclude node_modules --exclude .git ~/Developer
files count -r --clean
files sizes -r --clean-git
files sizes -r --clean-git --cached --limit 0
files duplicates -r --limit 0 ~/Downloads
files leftovers -r --older-than 7d --verbose
files changed --help
```
