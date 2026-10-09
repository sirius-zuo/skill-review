# Workflow
Applies: multi_step
> A gate passes on substance, not on a heading that names it. A section titled X that does not do X fails.

## Blockers

### WFL-B1 — Is there a loop or retry with no exit condition? (YES = blocker)
PASS: "Retry up to 3 times, then report" (answer NO)  /  FAIL: "Keep retrying until it works" (answer YES)

## Critical gates

### WFL-CG1 — Are steps ordered, with a checkable completion condition for each?
PASS: "Step 2 done when build exits 0"  /  FAIL: Steps listed with no way to verify completion

### WFL-CG2 — Is a failure path defined for each step that can fail (what to do, when to stop)?
PASS: "If the fetch fails, report and stop"  /  FAIL: Assumes every step succeeds

### WFL-CG3 — Is fan-out (sub-agents, per-item loops) bounded by a stated maximum?
Applies: fans_out
PASS: "At most 5 sub-agents"  /  FAIL: "Spawn an agent per file" with no cap

## Quality gates

### WFL-QG1 — Do quality-critical steps use a validate → fix → repeat loop?
PASS: "Run the linter; fix; rerun until clean (max 3)"  /  FAIL: Output produced once and never checked

### WFL-QG2 — Are fragile deterministic steps delegated to scripts rather than prose?
PASS: Date arithmetic done by a script  /  FAIL: Asks the agent to hand-compute checksums

### WFL-QG3 — Is it stated what context or state is carried forward and what is dropped?
Applies: long_running
PASS: "Keep only the summary file between phases"  /  FAIL: Long run with no guidance on state

### WFL-QG4 — Are the conditions for stopping to ask the user explicit?
PASS: "Ask before overwriting an existing file"  /  FAIL: Never says when to ask

## Score anchors

| Score | Meaning |
|---|---|
| 9–10 | Ordered, verifiable, bounded, failure paths defined. |
| 6–8 | Sound flow with a few unchecked steps. |
| 4–5 | Steps vague or failure paths missing. |
| ≤3 | Unbounded or incoherent. |
