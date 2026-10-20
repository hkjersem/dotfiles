---
title: Verify the Node 26 LTS switch
status: planned
summary: Check that the first dotfiles-update after Node 26 becomes LTS installs it cleanly with corepack, globals and package managers working.
kind: investigation
when: 'Node 26 becomes LTS (planned 2026-10-28) and dotfiles-update has run'
---

# Verify the Node 26 LTS switch

[Roadmap](../ROADMAP.md)

## Outcome

The automatic move to Node 26 works on every machine, or what fails is fixed.

## Context

`macos/update.sh` runs `scripts/install-node.sh lts`, so the first
`dotfiles-update` after 2026-10-28 moves the default to Node 26 without
asking. Node 25+ doesn't bundle corepack, and `fnm --corepack-enabled` fails
on Node 26 (Schniz/fnm#1469). `install-node.sh` therefore installs with
`FNM_COREPACK_ENABLED=false` and installs and enables corepack itself. That
path is covered by mocked tests in `scripts/tests/test_install_node.py`, but
it has never run against a real Node 26.

`install-node.sh` doesn't migrate npm, so npm falls back to the version
bundled with Node 26 until `update.sh` runs `npm install npm -g` later in the
same run.

## Decisions

- 2026-10-20: Keep corepack on Node 26. It is still maintained, and the
  shell aliases (`zsh/conf.d/aliases.zsh`) and `scripts/corepack-defaults.sh`
  depend on it. Revisit only if corepack is archived. (agreed)

## Acceptance

- [ ] `node -v` is 26.x and Node 24 is still installed (one version per
      major).
- [ ] `corepack --version` works, and `pnpm -v` and `yarn -v` resolve through
      corepack.
- [ ] Global npm packages from Node 24 are present, and `npm -v` is the
      updated npm, not the bundled one.
- [ ] `bash scripts/audit.sh` shows no new Node or corepack warnings.
- [ ] Checked on both a personal and a work machine.
