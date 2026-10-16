---
name: roadmap
description: Plan and track work in a repository roadmap made of self-contained item briefs and a script-generated index (ROADMAP.md with roadmap markers), for one owner working with coding agents. Use to set up, clean up or migrate a roadmap; to add, start, ship, finish, drop or archive items; to track due dates and recurring routines; or to pick what to work on next. In a repository whose roadmap uses another format, it only says to follow that format's own rules.
---

# Roadmap

Classify the repository's roadmap first, then read only what applies.

1. Find the roadmap index: usually `ROADMAP.md`, sometimes `docs/roadmap.md`,
   a file in a planning folder such as `tasks/roadmap.md`, or a planning section
   linked from README or AGENTS.md.
2. Look for the line `<!-- roadmap:start -->` in it.

| Finding | Do this |
|---|---|
| Marker present | **Conforming** (or drifting). Read [references/manual.md](references/manual.md) and follow it. |
| No marker, but item files start with `---` front matter holding `status:` | **Drifting** this layout. Read the manual; `check` explains what is missing. |
| No roadmap, and the owner asks for one | Read the manual, run `init`, and write the purpose paragraph with the owner. |
| No roadmap, owner did not ask | Do the task without one; offer a roadmap only if planning keeps getting lost. |
| Another format, owner asks to migrate | Read [references/repair.md](references/repair.md) and the manual, then migrate. |
| Another format, owner asks to fix or clean it up | Read [references/repair.md](references/repair.md) only. It is a cleanup, not a migration. |
| Another format, anything else | **Stop reading this skill.** Follow the roadmap's own rules, structure and status names, and the repository's AGENTS.md. Do not run the script; it exits 2 on other formats. |

In every case: listing an item on a roadmap does not authorise implementing it,
and decisions about commitment, acceptance and dropping belong to the owner.
Do not add roadmap items for work you are simply asked to do; add one only
when the owner asks or deferred work would otherwise be lost.
Asked what to do next, present the open options briefly and let the owner
choose; do not invent a ranking the roadmap does not have.
