# Repository triage

[README](../README.md#repository-triage)

## Documentation checks

Documentation checks cover tracked and untracked files that Git doesn't ignore.
They flag conventional files with the wrong case or in the wrong place (`README.md`,
`AGENTS.md`, `CLAUDE.md`, `LICENSE`, `.github/copilot-instructions.md` and
similar), a `DESIGN.md` without design tokens, paths that differ only in case,
and Markdown links whose case differs from the file. macOS ignores case, so
these work locally but break on Linux and on Git hosts.

## Source files and packages

Source-file statistics use tracked files, excluding known generated artifacts,
symlinks, and missing files. `.env` files are never read by any check, since
they may hold secrets. Files the audit has no permission to read are left out
of the statistics with a note, but an unreadable documentation file fails the
documentation check.

A project is labelled a workspace only when `pnpm-workspace.yaml` or
`package.json` `workspaces` lists packages. With a root `package.json`, it also
checks package metadata, cooldowns, and JS/TS tooling; these checks require jq.
npm and pnpm provide structured vulnerability and outdated-package results.
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
