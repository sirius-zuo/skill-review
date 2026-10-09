# Evaluation & evidence
Applies: always
> A gate passes on substance, not on a heading that names it. A section titled X that does not do X fails.

## Blockers

### EVL-B1 — Are there no test cases, expected behaviors or eval results anywhere? (YES = blocker)
PASS: Has evals or documented expected behaviors (answer NO)  /  FAIL: Nothing anywhere (answer YES)

## Critical gates

### EVL-CG1 — Are there at least 3 test cases or evals with specific expected outcomes?
PASS: Three evals each with expected output  /  FAIL: One vague example

### EVL-CG2 — Are there negative or should-not-trigger cases?
PASS: An eval where the skill must not trigger  /  FAIL: Only happy-path cases

## Quality gates

### EVL-QG1 — (script: `ingest.has_valid_result`) Is there at least one valid ingested eval result for this skill?
PASS: ingest.json holds a valid result  /  FAIL: No ingested results

### EVL-QG2 — (script: `ingest.has_baseline`) Does an ingested result include a without-skill baseline?
PASS: Result includes baseline run  /  FAIL: Only with-skill runs

### EVL-QG3 — Are known failure modes or limitations documented?
PASS: "Fails on scanned PDFs"  /  FAIL: Claims to work for everything

### EVL-QG4 — Does it state which models or agents it has been tested with?
PASS: "Tested on Sonnet and Opus"  /  FAIL: No mention of what it was tested on

## Score anchors

| Score | Meaning |
|---|---|
| 9–10 | Tests with negatives, results ingested with baseline, limits documented. |
| 6–8 | Some tests; evidence partial. |
| 4–5 | Few or vague tests. |
| ≤3 | No evidence. |
