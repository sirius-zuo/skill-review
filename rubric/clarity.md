# Instruction clarity
Applies: always
> A gate passes on substance, not on a heading that names it. A section titled X that does not do X fails.

## Blockers

### CLR-B1 — Do instructions contradict each other so that two careful readers would act differently? (YES = blocker)
PASS: Consistent rules throughout (answer NO)  /  FAIL: "Always ask first" and later "never ask, just proceed" (answer YES)

## Critical gates

### CLR-CG1 — Is terminology consistent (one term per concept)?
PASS: Always "report"  /  FAIL: Alternates "report", "summary", "digest" for one artifact

### CLR-CG2 — Are behavior-driving terms ("appropriate", "best", "properly") defined or replaced by concrete criteria?
PASS: "Split files over 300 lines"  /  FAIL: "Split files when appropriate"

## Quality gates

### CLR-QG1 — Are concrete examples given where the expected output or decision is not obvious?
PASS: Shows a sample commit message in the required format  /  FAIL: Format described only in prose

### CLR-QG2 — Does the degree of freedom match fragility: exact steps for fragile operations, open guidance for judgment calls?
PASS: Exact command for the migration; open guidance for naming  /  FAIL: Free-form guidance for a destructive migration

### CLR-QG3 — Does it give one default rather than a menu of equivalent options?
PASS: "Use pdfplumber"  /  FAIL: "Use pdfplumber, PyPDF2, pdfminer, or any library"

## Score anchors

| Score | Meaning |
|---|---|
| 9–10 | Unambiguous, consistent, concrete criteria, one default. |
| 6–8 | Mostly clear with a few vague terms or inconsistent names. |
| 4–5 | Frequent ambiguity or menus; the agent must guess. |
| ≤3 | Contradictory or unusable. |
