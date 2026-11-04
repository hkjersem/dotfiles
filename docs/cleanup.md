# Cleanup and clean copies

[Back to README](../README.md)

## Clean and copy projects

`wipe_clean` previews generated directories and cache files with allocated
sizes and an estimated total, then asks before deleting them. Hard links and
shared APFS blocks may overstate reclaimed space. If sizing fails, cleanup
stops without deleting anything. Git metadata and submodule directories are preserved.
Use `--dry-run` to preview without prompting or deleting.
Use `wipe_clean --sort size` to preview largest targets first; the default is
alphabetical path order. After deletion, fast filesystem queries show available
space before and after, without rescanning. This measures only the current
filesystem; concurrent activity and APFS snapshots mean it is not an exact
measurement of space reclaimed by cleanup.
Add `--older-than 7d` to select only targets whose own modification time and
every descendant's modification time are older than that threshold
(`m`, `h`, `d`, `w`). Symlink destinations are not inspected. Unreadable
metadata aborts cleanup, and selected ages are rechecked after confirmation.
This needs additional metadata scans. Age does not prove a target is unused;
stop builds and other writers first, since the scan and deletion are not atomic.
The shared artifact list includes Python's `.pytest_cache`, also excluded by
`copy_clean` and `files --clean`.
`copy_clean` copies a project without the same generated artifacts, using
macOS clone-on-write copies where supported. Existing destinations are never
overwritten.

```sh
wipe_clean
wipe_clean --sort size
wipe_clean --older-than 7d --sort size
copy_clean ../project-copy
copy_clean --ignore-git --keep-name ../copies
copy_clean --zip
copy_clean --zip ../project ../project-clean.zip
```

A single copy argument is the destination; the source defaults to the current
directory. `--keep-name` requires an existing destination directory.
ZIP archives always include the source folder name; without a destination,
the archive is created inside the source. `--ignore-git` excludes `.git`
directories; otherwise Git metadata is copied too.

## Clean Docker storage

`clean_docker --inspect` shows Docker storage and inspection guidance.
`--dry-run` shows only the cleanup plan, for both supported cleaners by default
or those selected with `--only`. Neither mode prompts or deletes.
A normal run requires explicit `--only` selection, asks with the single-key
`[y/N]` prompt, then reports what happened without displaying a plan.
Containers, volumes and tagged images are never deleted; it never runs
`system prune`, `--all`, or VM disk compaction.

```sh
clean_docker --inspect
clean_docker --dry-run
clean_docker --only build-cache --older-than 30d
clean_docker --only build-cache,dangling-images --older-than 7d --dry-run
clean_docker --context colima --builder colima --only build-cache
```

`--older-than` uses `m`, `h`, `d` or `w`: build-cache age means last use, while
image age means creation, not last use. Containerd-backed image stores use local
image-record creation, so a recently pulled old image may be retained. Without
the flag, all eligible data in the selected categories is considered.
Build-cache previews exclude shared, internal and frontend records. With an age
filter, cache counts are upper bounds **before age filtering**: Buildx exposes
only rounded last-use ages. Image counts are upper bounds based on image build
creation, not containerd's local timestamp. Docker applies the native age and
eligibility checks during pruning. Cache cleanup can slow later builds.

The default builder is the context's own builder, **not** whichever builder
Buildx has selected. `--builder` selects another explicitly; the context and
builder targets are pinned and rechecked after confirmation. A stopped builder
is reported, not started. If `DOCKER_HOST` is set, choose a named `--context`
explicitly. Storage totals describe the context's daemon; a different builder
can have separate storage. Docker's reclaimable totals are not the filtered
cleanup estimate or guaranteed space returned to macOS from Colima/Docker's VM.
The command is also listed under `dotfiles-help cleanup`.

In `--inspect`, the storage report marks each category's cleanup scope; Docker's
reclaimable image storage can include tagged images that this command retains. An
“Inspect further” section prints read-only commands for the same context and
builder, with volume-reference checks and manual removal guidance when containers
or volumes exist. Manual removals have no confirmation prompt and can destroy
needed data; `--help` stays focused on flags and examples. `--inspect` cannot be
combined with `--dry-run`, `--only` or `--older-than`; `--context` and `--builder`
work in all modes.

## Clean global caches

`clean_caches` previews supported global developer caches. It runs from HOME
regardless of your current folder; use `wipe_clean` for project-local output.
Go cleanup prefers Go on PATH, otherwise discovers already-installed mise Go
versions from HOME (active first, otherwise newest stable). The executable is
pinned through the cache-path recheck and cleanup. Discovery is offline, and Go
toolchain auto-downloads are disabled; nothing is installed or updated.
When Go is unavailable on PATH and in mise, existing conventional Go cache paths are reported as
unverified defaults with cleanup blocked (exit status 1). No configuration is
inferred, Go is not installed, and these paths are never deleted as a fallback.
An absent default path is reported as no cache directory, not a failed check;
this does not rule out caches at custom locations.
Cleaning requires explicit `--only` selection and single-key `[y/N]`
confirmation. `--dry-run` previews without prompting or cleaning.
`--deep` permits expensive cleaners but does not select them.
Temporary scan progress clears before the cache/size/folder table is printed;
paths beneath HOME use `~/`. Sizes show cache usage, not guaranteed savings.
Add `--sort size` to list largest caches first, with missing caches last.

```sh
clean_caches --dry-run
clean_caches --only uv,pip
clean_caches --only go-modules,playwright --deep
```

Native cleaners: npm (`cache clean --force`), pnpm (`store prune`),
Homebrew (`cleanup`), Bun (`pm cache rm`), Yarn (`cache clean`),
pip3 (`cache purge`), uv (`cache clean`), Go (`clean -cache` or
`clean -modcache`). Locations are queried from the installed tool before
preview and again after confirmation; Yarn uses its version-specific query.
These queries run tools and may invoke a tool-manager shim. Missing tools,
invalid locations and incomplete size scans are errors; failed preflight
checks prevent all cleaning. Failed cleaners are reported and make the
command exit nonzero, while remaining selected cleaners are still attempted.

npm and Yarn discovery checks PATH, every fnm Node installation, and existing
Corepack packages without invoking Corepack download shims. fnm's npm is run
with its matching Node; duplicate cache locations are cleaned only once.
Yarn Classic queries `cache dir`. Modern Yarn is forced into global-cache
mode for both `config get cacheFolder` and `cache clean`; project-local
`.yarn/cache` folders and installed global packages are not selected.
Invalid queries or cache paths enclosing installed tools block cleanup.

Bun discovery checks PATH, `BUN_INSTALL/bin/bun` (default `~/.bun/bin/bun`),
every fnm Node installation, and already-installed mise Bun (active first,
otherwise newest stable). Executables are resolved and duplicate queried
cache paths are cleaned once. No Bun installation or update is performed.
All-cache previews omit unavailable cleaners without known cache folders and
measure their existing known cache locations under `Report-only storage`,
with no duplicate `Skipped cleaners` entry.
That section also shows Corepack tools, Cargo storage and mise downloads when
present, explaining why they are not cleanup targets. These rows are never
included in cleanup or confirmation. Explicitly selecting an unavailable
cleaner remains an error. General explanations appear under `Notes`.
When orphan cleanup is supported, the report-only row gives its explicit
`--orphaned --dry-run` command. This is a separate content-verification step,
not a claim that the whole reported folder is eligible for deletion.

For missing Bun, npm, pip or Go, explicit orphan cleanup is available:

```sh
clean_caches --only bun --orphaned --dry-run
clean_caches --only bun --orphaned
```

`--orphaned` requires an explicit allowlisted `--only` selection and refuses
when the tool is discovered; use native cleanup without the flag instead.
Only conventional HOME locations are considered, not inferred custom paths.
It removes recognised Bun package/manifest entries, npm's `_cacache`, pip's
`http`, `http-v2` and `wheels`, or Go's hash-sharded build artifacts and
`log.txt`. Cache roots, unknown entries, npm logs/npx environments, pip
self-check data and Go fuzz inputs are preserved. Bun package manifests and
version aliases must match its cache layout; unsupported layouts stay.

The preview shows only selected artifact bytes and lists preserved entries.
After confirmation, every plan and tool check is repeated before deletion.
Descriptor-anchored removal never follows cached symlinks; symlink roots or
ancestors are rejected. Close all cache users first: missing executables do
not prove inactivity, and changes can still cause partial cleanup errors.
Downloads may be needed again and offline-only artifacts may be lost.
uv, pnpm stores, Corepack tools, Cargo sources, Go modules and mise storage
are not eligible for orphan cleanup. Fuzz-cache deletion is not included.

For pnpm, the preview includes the configured store and sibling store
generations (for example `v10` and `v11`), plus the separate metadata/execution
cache folder, counted once. A store format is not a CLI major: pnpm 11 and 12
can share `v11`. The current pnpm is preferred; other generations use the
newest already-installed compatible Corepack pnpm per major, discovered under
`COREPACK_HOME` (default `$XDG_CACHE_HOME/node/corepack`, or
`~/.cache/node/corepack` when unset). Executables and versions
are pinned and paths rechecked before native pruning. Missing compatible
versions, invalid packages, symlinks and overlapping store/cache paths block
cleanup; no versions are downloaded or installed.

Native pnpm pruning removes eligible store contents, cached registry metadata
and expired dlx/pnx entries, not the entire cache folder. Unexpired execution
caches and installed package-manager/Node versions are not blanket-deleted.
Corepack downloads and package-manager version switching are disabled for
cleaner subprocesses. Custom project stores outside the configured store root
are not discovered.

`files caches` stays tool-free by default. `files caches --include-configured` also
queries discovered tools from HOME using the same discovery as the cleaners,
merges identical locations with source labels, and reports unavailable tools
and failed queries. Known defaults are retained so orphaned caches remain
visible. Nested rows can overlap; there is no reclaimable-space total.
Failed queries make the command exit nonzero. Tool queries may refresh their
own metadata; they never invoke a cleanup command or install a tool.

`clean_caches --only mise` uses native `mise cache prune`, respecting
`cache_prune_age` and environment-cache TTL. It previews the metadata cache,
environment cache and any external task-cache root. Installed tools, config
and general state are protected from overlapping cache roots. Discovery
disables opportunistic mise age pruning; the actual cleanup honours your
normal retention settings. Installer downloads under `MISE_DATA_DIR/downloads`
are reported separately; they and installed runtimes are not removed wholesale.

Corepack's `v1` directory is reported as **installed package-manager versions**:
`corepack cache clean` removes those packages, so it is deliberately not a
cleaner. Cargo registry/Git locations honour `CARGO_HOME` and remain report-only.
They may contain edited source and dependencies needed offline. Stable Cargo
has automatic retention (since Rust 1.88); manual global garbage collection
currently requires nightly `cargo clean gc -Zgc`, so no nightly toolchain or
third-party cleaner is installed or invoked here.

Xcode's default `~/Library/Developer/Xcode/DerivedData` is removed directly;
custom DerivedData locations are not discovered. Deep cleaners also allow
direct deletion of Gradle's `caches` under `GRADLE_USER_HOME` (default
`~/.gradle`) and Playwright's global browser directory
(`PLAYWRIGHT_BROWSERS_PATH`, or `~/Library/Caches/ms-playwright`).
Project-local Playwright mode (`0`) and relative paths are refused, as is a
Playwright folder holding entries besides `<browser>-<revision>` names and
Playwright bookkeeping (`.links`, `__dirlock`, `.DS_Store`).
Symlink cache paths and ancestors, home/root and ancestors of home,
and non-directory targets are refused.

Cleared downloads need downloading again; build caches need rebuilding.
Close builds and cache users first. Preview sizes are allocated bytes, not a
promise of reclaimed space: native cleaners may preserve cache contents or,
as with Homebrew, clean old installations outside the displayed cache.
Observed free-space changes are measured on each cache's filesystem and
can include concurrent activity and APFS effects.

`dotfiles-update` keeps its existing npm verification, `_npx` removal, pnpm
pruning and Homebrew cleanup; no aggressive cleaners were added there.
Docker, agent sessions, temporary directories and Cargo storage are excluded.
