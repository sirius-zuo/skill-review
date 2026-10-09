# Scripts & tools
Applies: scripts or shell or network_read or network_write
> A gate passes on substance, not on a heading that names it. A section titled X that does not do X fails.

## Blockers

### SCT-B1 — Is there no handling at all when a script or tool fails? (YES = blocker)
PASS: "If the script exits non-zero, show stderr and stop" (answer NO)  /  FAIL: Never mentions failure of any tool call (answer YES)

## Critical gates

### SCT-CG1 — Do scripts handle their own errors with actionable messages rather than leaving it to the agent?
Applies: scripts
PASS: Prints "file not found: X; pass --input"  /  FAIL: Bare traceback on bad input

### SCT-CG2 — Are inputs validated before being passed to commands or tools?
PASS: Checks the path exists and is inside the project  /  FAIL: Passes user text straight into a command

### SCT-CG3 — Are dependencies (packages, binaries, versions) declared?
PASS: "Requires jq 1.6+"  /  FAIL: Uses jq without saying so

## Quality gates

### SCT-QG1 — Are constants (timeouts, retries, thresholds) justified rather than unexplained?
Applies: scripts
PASS: "30s timeout: API p99 is 10s"  /  FAIL: Unexplained sleep(47)

### SCT-QG2 — Is it clear whether each script is to be run or read?
Applies: scripts
PASS: "Run scripts/validate.py; do not read it"  /  FAIL: Scripts listed with no usage note

### SCT-QG3 — Is partial failure handled when one of several calls fails?
Applies: multi_step
PASS: "Report which items failed and continue with the rest"  /  FAIL: Aborts silently after the third of ten calls fails

## Score anchors

| Score | Meaning |
|---|---|
| 9–10 | Robust scripts, validated inputs, declared dependencies, explained constants. |
| 6–8 | Mostly robust; a few gaps. |
| 4–5 | Weak error handling or undeclared dependencies. |
| ≤3 | No failure handling. |
