# Calibration suite

Run by hand before each release (spec section 13.2). It measures the reviewer against reference
skills, hand-written labels and known mutations, and checks the result against the success
criteria S1 to S5 (spec section 2). Nothing here runs in the unit-test suite except the pure
tools (metrics, mutations, tar extraction guard); no test uses the network.

## Files

| File | Purpose |
|---|---|
| `corpus.json` | The reference skills with expected tiers and bands, written before any run. Records the pinned `anthropics/skills` commit. |
| `fetch_corpus.py --dest DIR` | Downloads the pinned commit into a new, empty directory outside this repository and extracts only the corpus skills. |
| `mutations.json` | MU-01 to MU-13: base skill, transformation, expected effect. |
| `mutate.py --corpus-dir D --out DIR` | Applies every mutation to a temp copy of its base. Never touches the base. For `rename_folder` (MU-09) the copy's `name` is first set to its original folder name, so only the rename can trip `L-NAME-03` (`prepare_base` builds that aligned base for comparison). |
| `score_calibration.py` | Prints the metrics against S1 to S5 with PASS or FAIL. |
| `labels/` | Maintainer-written expected gate answers (see `labels/README.md`). |

## Procedure

1. Fetch the Anthropic skills: `python3 tests/calibration/fetch_corpus.py --dest /tmp/skr-corpus`
   (any new directory outside the repository). Apply mutations:
   `python3 tests/calibration/mutate.py --corpus-dir /tmp/skr-corpus --out /tmp/skr-mutations`.
2. Run the reviewer with `--trials 3` on each corpus skill and on each mutated skill. Keep every run
   directory (it holds `results.json`).
3. Score: `python3 tests/calibration/score_calibration.py --runs RUN_DIR... --labels tests/calibration/labels --corpus tests/calibration/corpus.json --mutations tests/calibration/mutations.json`.
   Pass several runs of the same skill to measure tier stability across runs. If a result's skill
   name is not the corpus name (for example `good-skill` has the name `json-validator`), put a
   `run-map.json` in the run directory: `{"json-validator": "good-skill"}`.
4. Record the model, the engine and the metrics in `support/proven-runs.md`.
5. Changing any expectation in `corpus.json` or `mutations.json` requires a written reason in
   `support/proven-runs.md`. Do not edit an expectation to make a run pass.

## Judge diversity

Each release should include one calibration run with a judge from a different model family, where
the platform allows it. Always record the model used.

## Fallback parity

Run the fallback engine on the `tests/unit/test_fixtures_e2e.py` fixtures and diff the output against
the saved expected JSON in `tests/fixtures/expected/`. Lint rule ids and scan pattern ids must match
exactly. Record the result in `support/proven-runs.md`.

## Detection kinds

Mutations whose expectation is a lint rule or scan pattern are measured as deterministic detection
(target 100%). Mutations whose expectation is a gate answer or a tier are measured as judge-driven
detection (target 90% or more). A mutation can count in both. Neither number is claimed without run data.
