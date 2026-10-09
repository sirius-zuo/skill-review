# Skill Review

A portable agent skill that reviews other agent skills. Point it at a local directory or a GitHub repository: it discovers every skill, checks and scans each one, and writes an HTML report plus a ready-to-run **eval kit** per skill.

Works with any AI coding agent that can read markdown instruction files and run shell commands: Claude Code, Codex, Cursor, Windsurf, GitHub Copilot, and others.

---

## What It Does

- **Discovers** skills automatically: a single skill, a skill with supporting files, a skillset, or a plugin repository. Every file in a skill folder is checked, including hidden folders, `tests/` and `examples/`.
- **Checks** each skill with deterministic scripts: frontmatter and structure lint, a security pattern scan, and a capability inventory (what the skill can actually do: run scripts, write files, use the network, handle credentials, take irreversible actions).
- **Judges** what scripts cannot decide with a sub-agent that must quote verifiable evidence for every "yes". Quotes that do not match the skill's files are dropped.
- **Reports** three separate verdicts per skill (below), in a self-contained HTML report with no JavaScript and a strict Content-Security-Policy.
- **Generates an eval kit** per skill: trigger prompts, task cases and safety cases you run on your own platform to get real evidence.
- **Never executes** the reviewed skill, never modifies it, and treats everything in it as untrusted data.

---

## The Three Verdicts

Each skill gets three independent verdicts. They are never averaged into one number.

| Verdict | Values | What it means |
|---|---|---|
| **Risk** | Critical / High / Medium / Low (or Unknown if the review failed) | What the skill can do (its **exposure**: E0 knowledge only, E1 runs scripts or shell, E2 writes files, reads the network or invokes agents, E3 irreversible actions, network writes or credentials) combined with missing safeguards and confirmed scan hits. The first matching rule in `rules/risk-table.json` wins, and the report names the rule. |
| **Quality** | 0–10 per category; Strong / Adequate / Weak overall | Gate-based scores in nine categories: trigger, scope, clarity, conciseness, workflow, scripts and tools, safety, output, evaluation. Categories that do not apply (for example, workflow for a single-step skill) are shown as N/A. Points and bands are in `rules/scoring.json`. |
| **Evidence** | Unverified / Reported | Whether real eval results exist for the skill (see below). |

A small, focused, knowledge-only skill can be Low risk and Strong quality. A long, thoroughly documented skill that pipes a download into a shell is still Critical.

### Unverified vs. Reported

A static review cannot show how a skill behaves at runtime. So:

- **Unverified (static only)** — no eval results were found. This is the default for every new skill.
- **Reported** — the repository contains eval results the review could read: a `kit-results.json` from this tool's kit, a Claude plugin-eval result, or a skill-creator `benchmark.json`. Reported results are shown with their pass rates, with-skill vs. without-skill delta, date and freshness, and are labeled "Reported — not verified by this review".

Nothing in a review claims runtime evidence that a real run did not produce. The optional **routing check** (a sub-agent that sees only skill names and descriptions and picks one per prompt) is labeled *simulated* everywhere and never changes a score, tier or evidence level.

### The kit loop

1. **Generate.** A review writes `kit/<skill>/` under the run folder: `kit.json`, fixtures and a `README.md`.
2. **Run on your platform.** Run each case three times in a fresh session with the skill installed and three times without it. With `--kit-format claude-plugin-eval`, the kit is also exported in Claude Code plugin-eval format.
3. **Commit `kit-results.json`** next to the skill (the kit README shows the format).
4. **Re-review.** The next review ingests the results, and the skill's evidence level becomes **Reported**.

---

## Requirements

- **Python 3.7 or newer** (standard library only; nothing to install). The scripts in `scripts/` do discovery, lint, scan, scoring and rendering.
- **`git`**, only to review a GitHub URL.
- **Sub-agents** (optional). Without them, use `--mode single`; the routing check is then skipped.

**Without Python:** the skill still runs. The agent follows `support/fallback.md`, which applies the same rule files in `rules/` step by step. Every output it writes is tagged `"engine": "llm-fallback"`, and the report shows a banner naming the affected steps. Fallback results are less reproducible than script results.

---

## Installation

The skill is a directory of markdown instructions, Python scripts and rule files. Installation means making that directory available to your agent.

### Claude Code

Claude Code registers the skill from the `name: skill-review` frontmatter in `SKILL.md`.

```bash
git clone https://github.com/sirius-zuo/skill-review.git
cp -r skill-review ~/.claude/skills/skill-review
```

**Invoke with the slash command:**

```
/skill-review ./my-skill
/skill-review https://github.com/org/agent-skills --trials 3
```

**Or via natural language** — Claude recognises the description in `SKILL.md`:

```
Review the skill at ./my-skill
Is the skill at ./agent-skills/deploy safe to install?
```

### Codex

```bash
git clone https://github.com/sirius-zuo/skill-review.git ~/skills/skill-review
```

In your project's `AGENTS.md`:

```markdown
## Available Skills

**skill-review** — reviews agent skills for risk and quality; writes an HTML report and an eval kit.
Instructions: ~/skills/skill-review/SKILL.md
To use: ask Codex to review a skill directory or GitHub repo.
```

**Invoke:** `Review the skill at ./my-skill`

### Cursor

```bash
git clone https://github.com/sirius-zuo/skill-review.git ~/skills/skill-review
mkdir -p .cursor/rules
```

Create `.cursor/rules/skill-review.md`:

```markdown
---
description: Use when the user asks to review, audit or vet an agent skill or skillset
---

To review a skill, read and follow the instructions in:
~/skills/skill-review/SKILL.md
```

**Invoke in Cursor's chat:** `Review the skill at ./my-skill`

### Windsurf

```bash
git clone https://github.com/sirius-zuo/skill-review.git ~/skills/skill-review
```

In `.windsurfrules`:

```
When asked to review, audit or vet an agent skill or skillset, read and follow the
instructions in ~/skills/skill-review/SKILL.md.
```

**Invoke in Cascade:** `Review the skill at ./my-skill`

### GitHub Copilot

```bash
git clone https://github.com/sirius-zuo/skill-review.git ~/skills/skill-review
```

In `.github/copilot-instructions.md`:

```markdown
## Skill Review

When asked to review, audit or vet an agent skill or skillset, read and follow the
instructions in ~/skills/skill-review/SKILL.md.
```

**Invoke in Copilot Chat:** `Review the skill at ./my-skill`

For a one-off review without any config:

```
Read ~/skills/skill-review/SKILL.md and review the skill at ./my-skill
```

### Any Other Agent

Any agent that can read files, run `python3` and follow instructions works:

```
Read ~/skills/skill-review/SKILL.md and follow its instructions
to review the skill at ./path/to/skill
```

---

## Usage

| Platform | How to invoke |
|----------|--------------|
| Claude Code | `/skill-review ./my-skill` |
| Codex | `Review the skill at ./my-skill` |
| Cursor | `Review the skill at ./my-skill` |
| Windsurf | `Review the skill at ./my-skill` |
| GitHub Copilot | `Review the skill at ./my-skill` |

### Arguments

| Argument | Default | Meaning |
|---|---|---|
| `path` | (required) | A local directory, or `https://github.com/<org>/<repo>` (optional `.git`) |
| `--out DIR` | `./skill-reviews/<target>-<YYYYMMDDTHHMM>/` | Run folder for the report, results and kits |
| `--mode parallel\|single` | `parallel` | `parallel` uses sub-agents in batches; `single` does everything in-session and skips the routing check |
| `--trials N` | `1` | Independent judges per skill; answers use the majority, and agreement is reported |
| `--no-kit` | off | Do not generate eval kits (also skips the routing check) |
| `--no-routing` | off | Skip the simulated routing check |
| `--kit-format claude-plugin-eval` | off | Also export each kit in Claude Code plugin-eval format |
| `--keep-work` | off | Keep the intermediate `work/` folder for debugging |

There is no up-front questionnaire. The skill asks only when:

1. the target is the skill-review installation itself;
2. the output folder is inside the reviewed repository;
3. the estimated number of model passes is high (the threshold is in `rules/scoring.json`).

### What happens

1. **Discover and bundle** — find skills, clone a URL into a marked temporary folder, and bundle each skill's files with line numbers inside nonce-tagged blocks.
2. **Deterministic checks** — lint, security scan and capability inventory; ingest any eval results found.
3. **Estimate** — ask before a large run.
4. **Judge** — one sub-agent per skill (per trial) answers the rubric gates with quoted evidence.
5. **Score** — verify quotes, compute category scores, quality band, risk tier and recommendations.
6. **Kit and routing check** — write and validate each eval kit; run the simulated routing check.
7. **Assemble and render** — `results.json`, `report.html` and a terminal summary.
8. **Clean up** — the temporary clone and `work/` are deleted.

### Terminal summary

```
Skill review complete — 2 skills
Report:   /abs/skill-reviews/agent-skills-20261008T1430/report.html     Kits: /abs/skill-reviews/agent-skills-20261008T1430/kit/
Risk:     0 Critical · 1 High · 0 Medium · 1 Low   (worst: deploy-skill)
Quality:  1 Strong · 1 Adequate · 0 Weak
Evidence: 0 Reported · 2 Unverified (static only)
Top issues:
 1. [high] deploy-skill — ... (SAF-CG2)
```

### The report

- A verdict strip per skill: risk tier, quality band, evidence level, exposure level.
- The top issues across the run, and a dashboard of skills × categories.
- One collapsible section per skill: risk rationale (with the matched rule), top recommendations, capability inventory, scan hits with triage, lint findings, the gate table with quoted evidence and a ✓/✗ for each verified quote, reported results, simulated routing results, judge reliability, and kit status.
- Cross-skill patterns and method notes (what a static review cannot show, and how to run the kit).

---

## File Structure

```
skill-review/
  SKILL.md                orchestrator (start here)
  README.md
  scripts/                Python 3.7+ standard-library scripts
    discover.py  bundle.py  lint.py  scan.py  ingest.py
    score.py  build_kit.py  export_kit.py  routing.py  assemble.py  render.py
    common.py  frontmatter.py  applies_if.py  jsonschema_lite.py  judgment.py  risk.py
  rules/                  the single source of truth for every rule and threshold
    lint-rules.json  security-patterns.json  capabilities.json
    gates.json  scoring.json  risk-table.json
    schemas/              JSON schemas for every intermediate and final output
  rubric/                 one file per quality category, defining each gate
    trigger.md  scope.md  clarity.md  conciseness.md  workflow.md
    scripts-tools.md  safety.md  output.md  evaluation.md
  support/
    judge.md              judge sub-agent instructions
    kit.md                kit sub-agent instructions
    routing.md            routing-check sub-agent instructions
    fallback.md           step-by-step procedures when Python is unavailable
    report-template.html  report template (with CSP)
    proven-runs.md        log of real runs
  kit-templates/          injection, canary and distractor fixtures for eval kits
  tests/
    unit/                 python3 -m unittest discover -s tests/unit -p 'test_*.py'
    fixtures/             skill trees used by the tests
```

A run writes:

```
skill-reviews/<target>-<stamp>/
  report.html
  results.json
  kit/<skill>/            kit.json, fixtures/, README.md, evals/ (with --kit-format)
  work/                   intermediate JSON; deleted unless --keep-work
```

---

## Running the Tests

```bash
python3 -m unittest discover -s tests/unit -p 'test_*.py' -v
```
