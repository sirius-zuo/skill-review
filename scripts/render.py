"""Render the HTML report and the terminal summary (Python 3.7 stdlib only): spec section 11.2.

Every value that comes from results.json (and so, indirectly, from the reviewed target) passes
through esc(). Only markup generated in this file is inserted raw. The report has no script
element and its template carries a restrictive Content-Security-Policy meta.
"""
import argparse
import html
import os
import re
import sys
import tempfile

from common import (CATEGORY_ORDER, EXIT_OK, ROOT_DIR, UsageError, read_json, run_main,
                    scoring)

TEMPLATE_PATH = os.path.join(ROOT_DIR, "support", "report-template.html")
PLACEHOLDERS = ("title", "header", "banners", "verdict_strip", "top_issues", "dashboard",
                "skill_sections", "patterns", "method_notes")
_PLACEHOLDER_RE = re.compile(r"\{\{(" + "|".join(PLACEHOLDERS) + r")\}\}")
_TIERS = ("critical", "high", "medium", "low", "unknown")
_BANDS = ("strong", "adequate", "weak")
_CTRL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_BANNER_TEXT = {
    "discover": "Skill discovery", "bundle": "Bundling", "lint": "Lint", "scan": "Security scan",
    "ingest": "Result ingest", "score": "Scoring", "build_kit": "Kit build",
    "assemble": "Assembly",
}


def esc(value):
    return html.escape(str(value), quote=True)


def _val(value, none="n/a"):
    return none if value is None else esc(value)


def _cls(value, allowed):
    """A CSS class suffix: only values from a fixed allowlist, never raw data."""
    return value if value in allowed else "unknown" if "unknown" in allowed else ""


def _pct(x):
    return "n/a" if x is None else esc("%d%%" % int(round(float(x) * 100)))


# ---------------------------------------------------------------- small pieces

def _badge(kind, value):
    allowed = _TIERS if kind == "tier" else _BANDS
    if value is None:
        return '<span class="badge">n/a</span>'
    return '<span class="badge %s-%s">%s</span>' % (kind, _cls(value, allowed), esc(value))


def _simulated():
    return '<span class="badge simulated">simulated</span>'


def _loc(ev):
    f = ev.get("file")
    if f is None:
        return ""
    line = ev.get("line")
    return esc(f) + (":" + esc(line) if line is not None else "")


def _evidence_lines(evidence):
    out = []
    for ev in evidence or []:
        if not isinstance(ev, dict):
            continue
        bits = [b for b in (_loc(ev), ('"%s"' % esc(ev["quote"])) if ev.get("quote") else "") if b]
        if bits:
            out.append('<div class="evidence">%s</div>' % " ".join(bits))
    return "".join(out)


def _rec(r):
    simulated = " " + _simulated() if r.get("source") == "routing" else ""
    rank = r.get("priority_rank")
    rank_cls = "rec-p%d" % rank if isinstance(rank, int) and 1 <= rank <= 6 else "rec"
    why = '<div class="why">%s</div>' % esc(r["why"]) if r.get("why") else ""
    return ('<div class="rec %s"><div class="rec-head">[%s] %s (%s)%s</div>'
            '<div>%s</div>%s%s</div>') % (
        rank_cls, esc(r.get("category", "")), esc(r.get("text", "")), esc(r.get("id", "")),
        simulated, esc(r.get("source", "")), why, _evidence_lines(r.get("evidence")))


def _table(headers, rows):
    head = "".join("<th>%s</th>" % h for h in headers)
    body = "".join("<tr>%s</tr>" % "".join("<td>%s</td>" % c for c in row) for row in rows)
    return "<table><thead><tr>%s</tr></thead><tbody>%s</tbody></table>" % (head, body)


def _details(summary_html, body_html, open_=False):
    return '<details%s><summary>%s</summary><div class="details-body">%s</div></details>' % (
        " open" if open_ else "", summary_html, body_html)


# ---------------------------------------------------------------- sections

def _header(r):
    return ('<h1>Skill Review Report</h1><div class="meta"><span>Target: <span class="mono">%s'
            '</span></span><span>Date: %s</span><span>Engine: %s</span></div>') % (
        esc(r["target"]), esc(r["date"]), esc(r["engine"]))


def _banners(r):
    out = []
    if r.get("self_review"):
        out.append('<div class="banner">Self-review: this run reviewed skill-review itself. '
                   'Treat the verdicts as a smoke test, not an independent audit.</div>')
    for step in r.get("banners") or []:
        label = _BANNER_TEXT.get(step, step)
        out.append('<div class="banner">%s step ran in LLM-fallback mode (no Python 3.7+ '
                   'scripts); its results are less reproducible.</div>' % esc(label))
    return "\n".join(out)


def _verdict_strip(r):
    items = []
    for s in r["skills"]:
        level = (s.get("evidence") or {}).get("level")
        items.append(
            '<div class="strip-item"><div class="name">%s</div>'
            '<div>Risk %s Quality %s</div><div>Evidence: %s</div><div>Exposure: %s</div></div>' % (
                esc(s["skill"]), _badge("tier", s["risk_tier"]),
                _badge("band", s.get("quality_band")), _val(level), _val(s.get("exposure"))))
    return '<div class="strip">%s</div>' % "".join(items) if items else \
        '<p class="muted">No skills were reviewed.</p>'


def _top_issues(r):
    n = scoring()["report"]["top_issues_run"]
    items = []
    for t in (r.get("top_issues") or [])[:n]:
        sim = " " + _simulated() if t.get("source") == "routing" else ""
        items.append("<li>%s <strong>%s</strong> &mdash; %s (%s)%s</li>" % (
            _badge("tier", t["tier"]), esc(t["skill"]), esc(t["text"]), esc(t["id"]), sim))
    return "<ol>%s</ol>" % "".join(items) if items else '<p class="muted">No issues found.</p>'


def _score_cell(cat):
    if not isinstance(cat, dict) or not cat.get("applicable", True) or cat.get("score") is None:
        return '<td class="score score-grey">N/A</td>'
    score = cat["score"]
    b = scoring()["bands"]
    cls = "green" if score >= b["strong_min"] else "amber" if score >= b["adequate_min"] else "red"
    return '<td class="score score-%s">%s</td>' % (cls, esc(score))


def _dashboard(r):
    head = ["Skill"] + [esc(c) for c in CATEGORY_ORDER] + ["Risk", "Quality", "Evidence"]
    rows = []
    for s in r["skills"]:
        cats = s.get("categories") or {}
        cells = ["<td>%s</td>" % esc(s["skill"])]
        cells += [_score_cell(cats.get(c)) for c in CATEGORY_ORDER]
        level = (s.get("evidence") or {}).get("level")
        cells.append("<td>%s</td>" % _badge("tier", s["risk_tier"]))
        cells.append("<td>%s %s</td>" % (_val(s.get("quality_overall")),
                                         _badge("band", s.get("quality_band"))))
        cells.append("<td>%s</td>" % _val(level))
        rows.append("".join(cells))
    if not rows:
        return '<p class="muted">No skills were reviewed.</p>'
    ths = "".join("<th>%s</th>" % h for h in head)
    trs = "".join("<tr>%s</tr>" % row for row in rows)
    return "<table><thead><tr>%s</tr></thead><tbody>%s</tbody></table>" % (ths, trs)


def _gate_rows(s):
    rows = []
    cats = s.get("categories") or {}
    for cat_name in [c for c in CATEGORY_ORDER if c in cats] + sorted(
            c for c in cats if c not in CATEGORY_ORDER):
        for gid in sorted((cats[cat_name].get("gates") or {})):
            g = cats[cat_name]["gates"][gid]
            ev = g.get("evidence") or []
            if ev:
                marks = "".join('<div class="evidence"><span class="ok">&#10003;</span> %s %s</div>' % (
                    _loc(e), '"%s"' % esc(e["quote"]) if e.get("quote") else "") for e in ev)
            else:
                marks = '<span class="bad">&#10007;</span> <span class="muted">no verified evidence</span>'
            reason = esc(g["reason"]) if g.get("reason") else ""
            rows.append([esc(cat_name), esc(gid), esc(g.get("kind", "")),
                         esc(g.get("answer", "")), marks, reason])
    return rows


def _skill_section(r, s):
    key = s["key"]
    parts = []
    parts.append("<h3>Risk rationale</h3><p>%s</p>" % esc(s.get("risk_rationale", "")))
    # recommendations: top N visible, the rest in a nested details
    n = scoring()["report"]["top_recs_per_skill"]
    recs = s.get("recommendations") or []
    parts.append("<h3>Recommendations</h3>")
    if recs:
        parts.append("".join(_rec(x) for x in recs[:n]))
        if recs[n:]:
            parts.append(_details("%d more recommendation%s" % (
                len(recs) - n, "" if len(recs) - n == 1 else "s"),
                "".join(_rec(x) for x in recs[n:])))
    else:
        parts.append('<p class="muted">No recommendations.</p>')
    # capability inventory
    inv = s.get("inventory") or {}
    flags = [esc(k) for k in sorted(inv) if inv[k]]
    parts.append("<h3>Capability inventory</h3><p>Exposure %s. %s</p>" % (
        _val(s.get("exposure")), ("Present: " + ", ".join(flags)) if flags else "None detected."))
    und = s.get("undisclosed_capabilities") or []
    if und:
        parts.append("<p>Undisclosed capabilities: %s</p>" % ", ".join(
            esc(u.get("capability", "")) for u in und))
    # scan hits
    hits = s.get("scan_hits") or []
    parts.append("<h3>Scan hits</h3>")
    if hits:
        rows = []
        for h in hits:
            t = h.get("triage")
            triage = "%s: %s" % (esc(t["label"]), esc(t.get("reason", ""))) if t else "untriaged"
            rows.append([esc(h["hit_id"]), esc(h["severity"]), esc(h["family"]),
                         _loc(h), '<span class="mono">%s</span>' % esc(h["excerpt"]), triage,
                         "yes" if h.get("live") else "no"])
        parts.append(_table(["Hit", "Severity", "Family", "Location", "Excerpt", "Triage",
                             "Live"], rows))
    else:
        parts.append('<p class="muted">No scan hits.</p>')
    # lint findings
    lint = s.get("lint_findings") or []
    parts.append("<h3>Lint findings</h3>")
    if lint:
        rows = []
        for f in lint:
            d = f.get("dismissed")
            rows.append([esc(f["rule_id"]), esc(f["severity"]), _loc(f), esc(f["message"]),
                         ("dismissed: " + esc(d["reason"])) if d else ""])
        parts.append(_table(["Rule", "Severity", "Location", "Message", "Dismissal"], rows))
    else:
        parts.append('<p class="muted">No lint findings.</p>')
    # gates
    parts.append("<h3>Gates</h3>")
    gate_rows = _gate_rows(s)
    parts.append(_table(["Category", "Gate", "Kind", "Answer", "Evidence", "Reason"], gate_rows)
                 if gate_rows else '<p class="muted">No gates answered.</p>')
    # reported results
    ev = s.get("evidence") or {}
    results = ev.get("results") or []
    parts.append("<h3>Reported results</h3>")
    if results:
        rows = [[esc(x.get("source_file", "")), _val(x.get("model")), _val(x.get("date")),
                 _pct(x.get("with_pass_rate")), _pct(x.get("without_pass_rate")),
                 _val(x.get("fresh"))] for x in results]
        parts.append(_table(["Source", "Model", "Date", "With skill", "Without", "Freshness"],
                            rows))
    else:
        parts.append('<p class="muted">Unverified: no eval results were ingested (static '
                     'review only).</p>')
    # routing
    routing_recs = [x for x in recs if x.get("source") == "routing"]
    parts.append("<h3>Routing results %s</h3>" % _simulated())
    rstat = r.get("routing") or {}
    if rstat.get("status") not in (None, "ran"):
        parts.append('<p class="muted">Routing check %s: %s (%s of %s calls valid).</p>' % (
            esc(rstat.get("status")), esc(rstat.get("reason") or "no reason recorded"),
            esc(rstat.get("calls_valid")), esc(rstat.get("calls_expected"))))
    if routing_recs:
        parts.append("<ul>%s</ul>" % "".join("<li>%s (%s) %s</li>" % (
            esc(x["text"]), esc(x["id"]), _simulated()) for x in routing_recs))
    elif rstat.get("status") != "skipped":
        parts.append('<p class="muted">No simulated routing findings.</p>')
    # judge reliability
    jr = s.get("judge_reliability") or {}
    parts.append("<h3>Judge reliability</h3><p>Quote failure rate %s; trial agreement %s; "
                 "trials %s.</p>" % (_pct(jr.get("quote_failure_rate")),
                                     _pct(jr.get("trial_agreement")), _val(jr.get("trials"))))
    # kit
    kit = (r.get("kits") or {}).get(key)
    if kit:
        where = ' at <span class="mono">%s</span>' % esc(kit["path"]) if kit.get("path") else ""
        parts.append("<h3>Kit</h3><p>Status: %s%s</p>" % (esc(kit["status"]), where))
    else:
        parts.append("<h3>Kit</h3><p>Status: none</p>")
    summary = "%s %s %s" % (esc(s["skill"]), _badge("tier", s["risk_tier"]),
                            _badge("band", s.get("quality_band")))
    return _details(summary, "".join(parts))


def _skill_sections(r):
    return "\n".join(_skill_section(r, s) for s in r["skills"]) or \
        '<p class="muted">No skills were reviewed.</p>'


def _patterns(r):
    items = []
    for p in r.get("patterns") or []:
        names = ", ".join(esc(x) for x in p.get("skills", []))
        if p.get("kind") == "gate":
            items.append("<li>Gate <code>%s</code> fails in: %s</li>" % (esc(p.get("id", "")),
                                                                         names))
        else:
            items.append("<li>%s Routing collision between: %s</li>" % (_simulated(), names))
    return "<ul>%s</ul>" % "".join(items) if items else \
        '<p class="muted">No cross-skill patterns.</p>'


def _method_notes(r):
    return ("<p>A static review reads files; it does not run the skill. It cannot show that a "
            "skill triggers reliably, that its scripts behave as described, or that it improves "
            "task outcomes. Routing findings are simulated by a model answering from skill "
            "descriptions, not measured on a real agent. Evidence marked Unverified rests on "
            "static analysis only.</p>"
            "<p>To get measured evidence, run the generated eval kit on your platform, commit "
            "its <code>kit-results.json</code>, then re-run skill-review: skills with ingested "
            "results move from Unverified to Reported.</p>")


def render_report(results, template):
    values = {
        "title": esc("Skill Review - %s" % results["target"]),
        "header": _header(results),
        "banners": _banners(results),
        "verdict_strip": _verdict_strip(results),
        "top_issues": _top_issues(results),
        "dashboard": _dashboard(results),
        "skill_sections": _skill_sections(results),
        "patterns": _patterns(results),
        "method_notes": _method_notes(results),
    }
    # one pass, so placeholder-looking text inside a value is never substituted
    return _PLACEHOLDER_RE.sub(lambda m: values[m.group(1)], template)


# ---------------------------------------------------------------- terminal summary

def _clean(value):
    return _CTRL_RE.sub(" ", str(value))


def summary(results, run_dir):
    base = os.path.abspath(run_dir)
    roll = results["rollup"]
    tc, bc, ec = roll["tier_counts"], roll["band_counts"], roll["evidence_counts"]
    worst = None
    if roll.get("worst_tier") is not None:
        for s in results["skills"]:
            if s["risk_tier"] == roll["worst_tier"]:
                worst = s["skill"]
                break
    risk = "%d Critical · %d High · %d Medium · %d Low" % (
        tc["critical"], tc["high"], tc["medium"], tc["low"])
    if tc.get("unknown"):
        risk += " · %d Unknown" % tc["unknown"]
    lines = [
        "Skill review complete — %d skills" % len(results["skills"]),
        "Report:   %s     Kits: %s" % (os.path.join(base, "report.html"),
                                       os.path.join(base, "kit") + os.sep),
        "Risk:     %s   (worst: %s)" % (risk, _clean(worst) if worst is not None else "none"),
        "Quality:  %d Strong · %d Adequate · %d Weak" % (
            bc["strong"], bc["adequate"], bc["weak"]),
        "Evidence: %d Reported · %d Unverified (static only)" % (
            ec["reported"], ec["unverified"]),
        "Top issues:",
    ]
    for i, t in enumerate((results.get("top_issues") or [])[:3], 1):
        lines.append(" %d. [%s] %s — %s (%s)" % (
            i, _clean(t["tier"]), _clean(t["skill"]), _clean(t["text"]), _clean(t["id"])))
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- CLI (guarded I/O)

def _guarded_file(path):
    if os.path.islink(path) or not os.path.isfile(path):
        raise UsageError("not a regular file (or a symlink): %s" % path)


def _write_report(run_dir, text):
    out = os.path.join(run_dir, "report.html")
    if os.path.islink(out):
        raise UsageError("refusing to write through a symlink: %s" % out)
    fd, tmp = tempfile.mkstemp(dir=run_dir, prefix=".tmp-", suffix=".html")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, out)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return out


def main(argv):
    ap = argparse.ArgumentParser(prog="render.py")
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--summary", action="store_true")
    args = ap.parse_args(argv)
    run_dir = args.run_dir
    if os.path.islink(run_dir) or not os.path.isdir(run_dir):
        raise UsageError("--run-dir is not a directory (or is a symlink)")
    results_path = os.path.join(run_dir, "results.json")
    _guarded_file(results_path)
    results = read_json(results_path)
    if args.summary:
        sys.stdout.write(summary(results, run_dir))
        return EXIT_OK
    _guarded_file(TEMPLATE_PATH)
    with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
        template = f.read()
    out = _write_report(run_dir, render_report(results, template))
    sys.stdout.write(out + "\n")
    return EXIT_OK


if __name__ == "__main__":
    run_main(main)
