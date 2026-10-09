# Output contract
Applies: always
> A gate passes on substance, not on a heading that names it. A section titled X that does not do X fails.

## Blockers

### OUT-B1 — Is the output undefined or unpredictable? (YES = blocker)
PASS: Output is defined (answer NO)  /  FAIL: Says only "report back" (answer YES)

## Critical gates

### OUT-CG1 — Can a reader predict the output's structure before running it?
PASS: Shows a template or schema  /  FAIL: "Produce a nice summary"

### OUT-CG2 — Does the output tell the user what happened and, where relevant, what to do next?
PASS: "Created 3 files. Next: run tests."  /  FAIL: Silent on completion

## Quality gates

### OUT-QG1 — Is the machine-readable structure documented for consumers?
Applies: composable_output
PASS: JSON fields listed with types  /  FAIL: JSON output with undocumented fields

### OUT-QG2 — Are errors distinguishable from success?
PASS: Non-zero exit and an ERROR prefix  /  FAIL: Same output shape for success and failure

### OUT-QG3 — Is output length bounded for large inputs?
PASS: "Show the first 20 matches and a count"  /  FAIL: Dumps every match

## Score anchors

| Score | Meaning |
|---|---|
| 9–10 | Defined, predictable, informative, bounded. |
| 6–8 | Mostly defined; minor gaps. |
| 4–5 | Vague structure or no next steps. |
| ≤3 | Undefined. |
