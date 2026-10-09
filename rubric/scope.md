# Scope
Applies: always
> A gate passes on substance, not on a heading that names it. A section titled X that does not do X fails.

## Blockers

### SCP-B1 — Is there no discernible purpose? (YES = blocker)
PASS: A clear task is stated near the top (answer NO)  /  FAIL: Collection of notes with no stated goal (answer YES)

## Critical gates

### SCP-CG1 — Can the purpose be stated in one specific sentence?
PASS: "Generates release notes from merged PRs"  /  FAIL: "Helps with development workflows"

### SCP-CG2 — Can a reader tell when not to use it, where confusion with nearby tasks is plausible?
PASS: "Not for editing existing notes; use X instead"  /  FAIL: No boundary though a neighbouring task is plausible

## Quality gates

### SCP-QG1 — Are non-obvious preconditions or required inputs stated?
PASS: "Requires git 2.30+ and a clean tree"  /  FAIL: Fails silently without a token that is never mentioned

### SCP-QG2 — Is it clear what a successful result looks like?
PASS: "Done when tests pass and a PR URL is printed"  /  FAIL: No end state described

## Score anchors

| Score | Meaning |
|---|---|
| 9–10 | One clear purpose, boundaries and preconditions stated, success defined. |
| 6–8 | Clear purpose; boundaries or success criteria partly missing. |
| 4–5 | Purpose broad or blurred; no boundaries. |
| ≤3 | No discernible purpose. |
