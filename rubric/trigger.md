# Trigger & description
Applies: always
> A gate passes on substance, not on a heading that names it. A section titled X that does not do X fails.

## Blockers

### TRG-B1 — Is the description so generic it could apply to most skills ("helps with files", "processes data")? (YES = blocker)
PASS: "Extracts tables from PDF invoices into CSV" (answer NO)  /  FAIL: "Helps with files and processes data" (answer YES)

## Critical gates

### TRG-CG1 — Does the description say both what the skill does and when to use it?
PASS: "Fills PDF forms. Use when the user mentions PDFs or forms."  /  FAIL: "Fills PDF forms." with no use-when clause

### TRG-CG2 — Does it contain concrete trigger terms a user would actually say (file types, domain nouns, task verbs)?
PASS: Mentions ".xlsx", "pivot table", "merge sheets"  /  FAIL: Only abstract words such as "data operations"

### TRG-CG3 — Does it avoid summarizing the skill's workflow or steps?
PASS: Description names purpose and trigger only  /  FAIL: Description lists "first parse, then validate, then write"

## Quality gates

### TRG-QG1 — Is it clearly distinguishable from the descriptions of the other skills in this run?
Applies: has_siblings
PASS: Names a unique domain or an explicit boundary  /  FAIL: Near-identical wording to a sibling skill

### TRG-QG2 — Are the inputs the skill needs discoverable from the description or the first screen of the body?
PASS: Top of body: "Needs a CSV path and a target column"  /  FAIL: Inputs only revealed at step 7

### TRG-QG3 — Does the description match what the body actually does?
PASS: Description and body cover the same task  /  FAIL: Description promises translation; body only proofreads

## Score anchors

| Score | Meaning |
|---|---|
| 9–10 | Specific, trigger-rich, what and when both present, matches the body, distinct from siblings. |
| 6–8 | Mostly specific; one of what/when or trigger terms is thin. |
| 4–5 | Vague or partly misleading; missing when-to-use or trigger terms. |
| ≤3 | Generic, misleading, or absent. |
