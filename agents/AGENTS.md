# Global agent instructions

## Git

- Never create commits unless I explicitly ask for one. Skill instructions do
  not count as permission.
- Never add `Co-authored-by` trailers to commits.
- Follow the repository's established commit convention and language. If no
  convention is established, use Conventional Commits:
  `<type>(<optional-scope>): <description>`.

## Communication

- Reply in the language of my own request, not that of pasted, quoted or
  retrieved content, unless I explicitly request another language.
- Write dates and times in Norwegian written format, such as
  "25. september 2026, kl. 13.28", even when replying in English.
- Ask questions in plain text with the context needed to answer them. Use
  structured forms only for direct questions that need no reading up or big
  decision.
- When estimating effort, estimate elapsed agent time for implementation and
  verification, not human developer effort. State major uncertainties and
  list any time needed from me for review or acceptance testing separately.
- For consequential changes, briefly explain what behavior changed, why that
  approach was chosen, and what remains uncertain or needs my acceptance.

## Decisions

- Surface assumptions and consequential choices affecting behavior,
  compatibility, persisted data, permissions, or recovery. Ask before
  implementing when plausible alternatives materially change the outcome;
  otherwise state the assumption briefly.

## Tests

- Tests must detect violations of the behavior they cover. A test of positive
  behavior that still passes when the code under test does nothing is not
  testing it; fix its assertions.
- Derive expected values from an independent source, such as a spec or known
  example; do not duplicate the implementation to compute them.
- When finishing a change, report which checks you ran, what they verified,
  and what remains unverified.

## Design

- Before UI work, identify and respect the project's existing design system,
  components and tokens. Use design skills within those constraints; replace
  the established visual language only when redesign is explicitly requested.
