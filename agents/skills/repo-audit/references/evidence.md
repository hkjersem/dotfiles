# Evidence and prioritization

Use this rubric to evaluate a possible finding, not as a list of mandatory
checks. Select questions that matter to the project's purpose and audit focus.

## What supports a finding

A useful finding connects evidence to an outcome:

- The relevant behavior, invariant, or operational expectation is identifiable.
- A specific code path, configuration, or observed result violates it or creates
  a credible risk under stated conditions.
- The likely consequence matters to this project, rather than merely differing
  from a preferred style.
- The suggested action addresses that mechanism and can be evaluated.

For a defect, identify the affected path and triggering condition. For an
engineering risk, distinguish a plausible failure mode from a reproduced
failure. Cite target-relative paths and lines when available; never invent line
numbers, execution results, or missing project requirements.

## Signals that need interpretation

| Signal | What it does not establish | Useful follow-through |
|---|---|---|
| High churn or large file | Fragile design or a defect | Inspect a relevant change path, coupling, and regression history; account for generated code |
| Few test files or no coverage report | Untested behavior | Read relevant assertions and integration boundaries; distinguish coverage configuration from actual coverage |
| Old dependency | Urgent upgrade or reachable vulnerability | Establish compatibility needs, support status, and affected usage using available evidence |
| Advisory report | An exploitable application path | Check the resolved version, affected feature, and exposure; label reachability unknown if not established |
| Scanner error or absent tool | A successful check | Report what could not be assessed and why |
| Missing familiar tool or convention | A project problem | Check for equivalent mechanisms and the project's actual needs |
| Concentrated authorship or commit frequency | Individual productivity or code quality | Treat history as context for maintenance risk, not a judgment of people |

## Calibrate the report

Prioritize correctness, data integrity, operational reliability, and costly
maintenance risks according to the user's goal. Explain impact rather than
deriving severity from arbitrary thresholds.

High confidence requires a clear mechanism supported by inspected evidence.
When behavior depends on unavailable configuration or runtime state, qualify
the finding or leave it as an investigation lead. A small inspected sample
does not support repository-wide conclusions.

Recommend a targeted next step: a regression case, a specific configuration
correction, an implementation change, or an investigation that resolves the
named uncertainty. Avoid generic prescriptions such as adding more tests,
rewriting large files, or upgrading everything.
