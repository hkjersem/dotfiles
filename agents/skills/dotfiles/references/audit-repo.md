# audit-repo

Run as `bash ~/.dotfiles/scripts/audit-repo.sh [--dir PATH] [--since PERIOD] [--no-package-manager] [--skip-security]`.
Details: `~/.dotfiles/docs/audit-repo.md`.

It reports Git activity, ownership, churn, source sizes, test-file
conventions, build configuration, documentation naming, skill-managed roadmap
validity (requires Python 3), and which branches prune branches would delete,
from the last fetch. With a root `package.json` it also checks package metadata,
cooldowns, vulnerabilities and outdated packages; those checks need `jq`.

## Execution boundaries

For local inspection without package-manager execution, use
`--no-package-manager`. It keeps file and Git checks, but skips vulnerability
and outdated queries, the current Node version and pnpm's global configuration
lookup. Stale-branch classification is also skipped because configured Git hooks
and merge drivers can execute code. Git lazy fetching and optional index writes
are disabled; missing local objects fail the check rather than being downloaded.
Project cooldown configuration is still inspected without running tools.

Without that flag, it runs the package manager selected by the repository.
Corepack may download and execute that selected version or URL; audit and
outdated queries can contact registries and write caches. These operations need
authorization covering execution and network access. `--skip-security` skips
only the vulnerability query, not outdated or configuration commands.

It does not edit project files, install project dependencies, run project tests,
or fetch Git refs. Do not edit or install tools in the dotfiles checkout while
working in another repository. Report missing tools, unsupported flags and
access failures rather than bypassing restrictions or retrying without
`--no-package-manager`.

## Report

Findings are triage indicators, not a security verdict. Exit status 1 means
concrete issues or checks that could not run, so read the output to tell
which; warnings alone exit 0. Summarize issues before warnings, and do not
start fixing findings unless the user asks. For a deeper review, give the
output to the `repo-audit` skill as leads, not established findings; run it
only when the user asks for that review.
