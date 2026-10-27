---
title: Maintenance cost review
status: ready
summary: Measure what each command costs to keep, against how much it is used, and propose what to delete, merge or shrink.
kind: routine
every: 1m
last_done: 2026-10-26
---

# Maintenance cost review

[Roadmap](../ROADMAP.md)

## Outcome

Every line in this repository has to be kept working across tool updates,
macOS releases and new agents. Agents make adding code cheap, so the cost of
keeping it grows faster than before (James Shore,
[You Need AI That Reduces Your Maintenance Costs](https://www.jamesshore.com/v2/blog/2026/you-need-ai-that-reduces-your-maintenance-costs)).
This review weighs each command's cost against its use and proposes
deletions, merges and simplifications. The owner decides; nothing is removed
during a run without the owner's agreement. Larger work becomes separate
roadmap items.

It complements the [Tool updates review](tool-updates-review.md), which asks
whether the code still matches upstream tools. This one asks whether the code
should exist.

## Steps

1. Group the files by command or feature: the script, its `scripts/lib/`
   helpers, its tests in `scripts/tests/` and its docs in `docs/` and
   `agents/skills/dotfiles/references/`. Include untracked files. Start from
   the groups in the previous run's Log and add new ones.
2. For each group, record lines of code, tests and docs, and commits and
   changed lines over the last 60 days (`git log --numstat`). Count physical
   lines of tracked and untracked text files, give each shared helper to one
   group, and list every group in the Log so totals add up and runs compare.
3. Count retained `~/.zsh_history` entries per command and per flag, matching
   only the command word and flag names. Aggregate locally and output only
   those counts, never history lines or argument values. The shell drops
   duplicate lines (`HIST_IGNORE_ALL_DUPS` in `zsh/conf.d/history.zsh`), so a
   count is distinct invocations, not uses: a command run daily the same way
   counts once. This repository is the owner's toolset for every Mac and
   repository, and each Mac has its own history and its own use: one Mac
   runs `codex-resume --recurring` constantly and uses `nosync` for iCloud.
   History also misses runs by agents, LaunchAgents and scripts, and
   commands added in the last two months, such as `roadmaps`, have not had
   time to build up use. Low counts are a question for the owner, never a
   reason to delete by themselves.
4. Apply the `repo-audit` skill's maintenance cost focus to the groups:
   deletion candidates, duplicate mechanisms, workarounds whose cause is
   gone, unstable boundaries and tests coupled to internals. Patching real
   boundaries (paths, `launchctl`, network, other processes) is fine.
   Patching a script's own functions is a lead, not a finding: some wrap
   side effects on purpose. Report it only where a specific change was made
   harder, and prefer extending existing fakes over new test setups.
5. Report a short table (group, cost, use, churn) and, for each finding,
   propose delete, merge, shrink, decouple tests or keep, with the lines it
   would save and what could break. Changes happen only on request.
6. Add a dated line to the Log with totals, notable groups and the outcome,
   so the next run can compare.

## Log

- 2026-10-26: First run, read-only; many files had uncommitted work, so
  nothing was changed. History covers August 2025 to now (2,603 retained
  entries; duplicates are dropped, so counts below are distinct
  invocations on this Mac only).
  - Totals: 13,050 lines of code, 9,217 of tests and 655 of docs across all
    groups below. October alone added about 18,000 lines and removed 4,600.
  - Groups (code/tests/docs lines; commits and changed lines in 60 days;
    history count):
    - pm\* wrappers: 2,419/1,086/144; 17, 1,980; `pm` 34, `pmi` 20,
      `pmu` 11, `pmr` 5, `pma` 2, `pmc` 1, `pmx` 1, `pml` 0.
    - codex-resume: 1,396/1,757/153; 35, 6,262; 37 (`--time` 11, `--now` 5,
      `--reset` 3, `--check` 3, `--recurring` 0). A Swift helper of 1,924
      lines was added and removed in October.
    - setup, update and audit: 1,353/1,141/0; 22, 2,046.
    - roadmap skill: 1,368/470/0; 5, 1,946.
    - skills and instructions sync: 1,305/706/82; 15, 2,215.
    - audit-repo: 1,142/555/67; 12, 1,940; 4.
    - Node, corepack and npm helpers: 946/896/0; 11, 1,599;
      `npm_release_age` 45, `npm_globals_diff` 6, `install_node` 2.
    - prune_branches: 895/582/99; 7, 1,724; 5.
    - agent-awake: 881/283/110; not yet committed; 0.
    - wipe, wipe_clean, copy_clean: 423/214/0; 3, 631; `copy_clean` 14,
      `wipe` 3, `wipe_clean` 1.
    - agent-sessions.zsh: 337/592/0; 5, 949; runs on every agent start.
    - output libraries: 186/168/0; 1, 208.
    - nosync: 162/366/0; 5, 972; 2.
    - roadmaps: 158/103/0; 4, 316; 1.
    - shell help and flag highlighting: 79/298/0; 4, 360.
  - Findings, all proposals for the owner:
    - codex-resume has the most churn: 6,262 of 23,148 changed lines (27%),
      with 11% of the code. The pm\* wrappers are the largest group. Keep
      codex-resume: the owner confirmed `--recurring` runs constantly on
      another Mac. Why it churns (Codex API changes or its own design) was
      not investigated.
    - Lead: `test_codex_resume.py` has 165 patches; about 45 replace the
      script's own functions (`resume_through_queue` 14,
      `wait_until_scheduled_time` 9, `latest_chat` 7 and others). No
      specific change was shown to be harder because of them. If one is,
      extend the existing `FAKE_APP_SERVER` in that file rather than adding
      new fakes.
    - Few history entries on this Mac: `pml` (0; 109 lines plus tests),
      `pmc` (1), `wipe_clean` (1). The owner keeps all three: `pml` is
      brand new, `pmc` should be used more, and `wipe_clean` is useful.
      Keep `nosync` (used on the iCloud Mac) and `roadmaps` (new, expected
      to be used).
    - Tests outweigh code in agent-sessions, nosync and the shell help and
      highlight checks. That is acceptable where they guard behaviour on
      every shell start, but worth a look for duplicate cases.
    - No duplicate mechanisms found; the clean commands already share
      `scripts/lib/clean-targets.sh`.
    - Outcome: nothing to remove among the commands. The zsh-autosuggestions
      and zsh-syntax-highlighting clones, which oh-my-zsh now bundles, were
      dropped from `install-zsh-plugins.sh` and deleted by hand on both
      Macs. This run only
      measured size, churn and use; it did not look for dead paths or
      outdated workarounds inside the scripts. The owner wants that deeper
      pass, limited to the pm\* wrappers, audit-repo, codex-resume and
      setup/update/audit, once their uncommitted work is finished.
