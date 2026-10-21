# audit-repo

Run as `bash ~/.dotfiles/scripts/audit-repo.sh [--dir PATH] [--since PERIOD] [--skip-security]`.
Details: `~/.dotfiles/docs/audit-repo.md`.

It reports Git activity, ownership, churn, source sizes, test-file
conventions, build configuration, documentation naming, and which branches
prune branches would delete, from the last fetch. With a root `package.json`
it also checks package metadata, cooldowns, vulnerabilities and outdated
packages; those checks need `jq`.

It does not edit project files, install dependencies, run tests, or fetch.
It is not offline: npm and pnpm audit and outdated queries can contact
registries and write package-manager caches. `--skip-security` skips only the
vulnerability query, not the outdated query.

## Report

Findings are triage indicators, not a security verdict. Exit status 1 means
concrete issues or checks that could not run, so read the output to tell
which; warnings alone exit 0. Summarize issues before warnings, and do not
start fixing findings unless the user asks.
