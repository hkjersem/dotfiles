# Global agent instructions

These apply in every repository. A repository's own instructions take
precedence for its conventions.

## Git

- Never create commits unless I explicitly ask for one.
- Never add `Co-authored-by` trailers to commits.

## Communication

- Reply in English when I write in English.
- Write dates and times in Norwegian written format, such as
  "25. september 2026, kl. 13.28", even when replying in English.
- Ask questions in plain text with the context needed to answer them. Use
  structured forms only for direct questions that need no reading up or big
  decision.

## Tests

- A test must fail when the behavior it covers breaks. If it would still pass
  with every function it calls returning nothing, rewrite its assertions or
  delete it.
- When finishing a change, report which checks you ran, what they verified,
  and what remains unverified.

## Sandbox

- Some machines run agents in a required sandbox that can block commands,
  network access, or configuration writes. Report a block; never work around
  it.
