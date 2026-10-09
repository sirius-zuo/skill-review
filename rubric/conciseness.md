# Conciseness & structure
Applies: always
> A gate passes on substance, not on a heading that names it. A section titled X that does not do X fails.

## Critical gates

### CON-CG1 — Is it free of material explaining what a capable model already knows?
PASS: Goes straight to the project-specific rules  /  FAIL: Explains what JSON or a git commit is

### CON-CG2 — Does every section change what the agent does or knows (no boilerplate sections)?
PASS: Each section carries a rule or a fact  /  FAIL: "About", "Contributing" and "License" sections in SKILL.md

## Quality gates

### CON-QG1 — Does detail needed only by some tasks live in linked files rather than the body?
Applies: large_body
PASS: Body links to reference.md for the rare case  /  FAIL: Whole API reference inline in a long body

### CON-QG2 — Is content free of duplication between SKILL.md and linked files?
PASS: Each rule appears once  /  FAIL: Same table pasted in body and reference file

### CON-QG3 — Do linked files have descriptive names and a clear pointer saying when to read them?
Applies: has_references
PASS: "For OAuth errors, read oauth-errors.md"  /  FAIL: "See notes2.md" with no context

## Score anchors

| Score | Meaning |
|---|---|
| 9–10 | Everything earns its place; short where the task is simple; detail layered. |
| 6–8 | Minor padding or one oversized section. |
| 4–5 | Substantial padding or duplication. |
| ≤3 | Mostly filler. |
