---
title: Shell history sync between Macs
status: idea
summary: Merge zsh history between my Macs on demand over the local network, with no cloud service.
kind: investigation
---

# Shell history sync between Macs

[Roadmap](../ROADMAP.md)

## Outcome

A command run on demand merges `~/.zsh_history` between two of my Macs, so
commands typed on one can be found with history search on the other. History
never leaves machines I control.

## Context

History is configured in `zsh/conf.d/history.zsh`. It keeps a single
`~/.zsh_history` with timestamps (oh-my-zsh enables `EXTENDED_HISTORY`),
deduplicates entries and shares history between open shells. Each machine keeps
its own file today.

At least one machine is a work Mac, so history may contain internal hostnames,
paths, tokens passed on the command line and other work data.

## Scope

Constraints a solution must respect:

- Triggered manually or by an explicit schedule; no always-on sync daemon.
- Machine to machine over the local network or SSH. No third-party cloud,
  including hosted atuin sync.
- Merge rather than overwrite: union both files, order by timestamp, drop
  duplicates, keep the zsh multi-line and metafied encoding intact.
- Safe with open shells, which append to the file at any time; back up the file
  before writing.
- Respect the `HIST_IGNORE_SPACE` convention, and leave out lines that look
  like secrets.
- Optional per machine, through the machine profile or a local setting, so
  work history can stay on the work Mac.

## Open questions

- Should history merge between work and personal Macs at all, or only between
  machines with the same profile? Does company policy allow it?
- How should the Macs find and authenticate each other? Options include
  Remote Login/SSH with a dedicated key, or a direct copy with AirDrop.
- Is a self-hosted atuin server on the LAN better than merging plain files? It
  would change history search, which history-substring-search and fzf handle
  today.
- Which secret patterns should be filtered, and should filtering also apply to
  local history?
