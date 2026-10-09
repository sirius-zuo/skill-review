# Proven Runs

Log of verified end-to-end executions of skill-review v2. Append one entry under "Runs" after each real run, using the entry format below. Calibration runs are recorded here too.

## Entry format

Each entry is a `### Run <n> — <YYYY-MM-DD>` heading followed by these fields:

- **Date:** the date of the run (`YYYY-MM-DD`).
- **Target:** the reviewed path or GitHub URL, with the commit SHA when known.
- **Model:** the model (and platform) that ran the orchestrator and the judge, kit and routing sub-agents. Record every model family used.
- **Engine:** `script` or `llm-fallback`, and the steps that used the fallback, if any. Fallback runs are never used as a calibration baseline.
- **Trials:** the `--trials` value.
- **Tiers / Bands:** the risk tier (with the matched risk rule id) and quality band per skill.
- **Calibration metrics:** for calibration runs, the output of `score_calibration.py`: tier and band matches, deterministic and judge mutation detection rates, Cohen's κ and agreement, trial agreement, quote-failure rate, tier stability. Write `n/a` for ordinary runs.
- **Expectation changes:** any change to a calibration expectation, with the written reason. Write `none` when nothing changed.

## Runs

No v2 runs recorded yet.

## Legacy rubric — not comparable

The runs below used the v1 pipeline: thirteen documentation-centred categories, static score ceilings and simulated dynamic testing. Their scores and risk levels are not comparable with v2 verdicts and must not be used as a baseline.

### Run 1 — 2026-05-15

- **Input:** skill-review project root (self-review)
- **Skills reviewed:** 1 (skill-review)
- **Mode:** parallel
- **Static analysis:** completed — 13 categories scored, all applicable
- **Dynamic testing:** completed — 23 scenarios across safety_security, proven_reliability, trigger_invocation (9 pass, 2 partial, 12 fail)
- **Report:** docs/review/skill-review-2026-05-15T1545.html
- **Overall score:** 5.77/10, risk: Critical
- **Notes:** Self-review. Failures are expected and documented as improvements in this spec: docs/superpowers/specs/2026-05-15-skill-reviewer-improvements-design.md
- **Failure patterns:** All 12 Phase 5 FAILs and 2 PARTIALs were attributable to missing content isolation (`<skill_content>` tags not yet implemented), missing input validation in discover.md, and blocker escalation miscalibration. All root causes addressed in the May 2026 improvements branch (merged 2026-05-16).
- **Phase pass rates:** Phase 5: 9/23 PASS, 2/23 PARTIAL, 12/23 FAIL

---

### Run 2 — 2026-05-16

- **Input:** skill-review project root (self-review)
- **Skills reviewed:** 1 (skill-review)
- **Mode:** parallel
- **Static analysis:** completed — 13 categories scored, all applicable
- **Dynamic testing:** completed — 12 scenarios across safety_security, trigger_invocation, test_coverage, tool_integration (9 pass, 0 partial, 3 fail)
- **Report:** docs/review/skill-review-2026-05-15T2234.html *(generated 2026-05-15 late evening; review completed 2026-05-16)*
- **Overall score:** 7.23/10, risk: High
- **Failure patterns:** 3 Phase 5 FAILs in tool_integration: GEN-1 (nested sub-agent tool pattern — scenario inconclusive), GEN-2 (malformed JSON from sub-agent — recovery path not exercised), GEN-3 (write permission denied in Phase 6 — no error recovery). These are addressed by the operational maturity improvements spec (2026-05-16).
- **Phase pass rates:** Phase 5: 9/12 PASS, 0/12 PARTIAL, 3/12 FAIL
- **Notes:** Re-review after May 2026 improvements merged. Score improved from 5.77/10 Critical (Run 1) to 7.23/10 High.

---

### Run 3 — 2026-05-16

- **Input:** skill-review project root (self-review)
- **Skills reviewed:** 1 (skill-review)
- **Mode:** parallel
- **Static analysis:** completed — 13 categories scored, all applicable
- **Dynamic testing:** completed — 24 scenarios across all applicable categories (20 pass, 3 partial, 1 fail)
- **Report:** docs/review/skill-review-2026-05-16T1525.html
- **Overall score:** 8.77/10, risk: Medium
- **Failure patterns:** 1 Phase 5 FAIL: AT-1 (no JSON schema/type validation on sub-agent returns — wrong types propagate silently to Phase 6). 3 PARTIALs: EC-3 (no per-file size guard), TF-1 (no skill-level timeout, relies on platform), TF-4 (Phase 5 failure handling undocumented). Static BLOCKER-1 in safety_security and tool_integration was a false positive — discover.md input validation (URL pattern matching + metacharacter rejection) clears it dynamically.
- **Phase pass rates:** Phase 5: 20/24 PASS, 3/24 PARTIAL, 1/24 FAIL
- **Notes:** Re-review after operational maturity improvements (context compaction, proven-runs annotations, Resource Characteristics section). Score improved from 7.23/10 High (Run 2) to 8.77/10 Medium. Remaining gap: tool_integration = 6 due to missing JSON schema validation on sub-agent returns.
