"""Capability-based risk tier: facts, first-match table, rationale text."""
import applies_if

_TIER_ORDER = {"E0": 0, "E1": 1, "E2": 2, "E3": 3}


def _live(ctx, severity):
    return [h for h in ctx.get("live_hits", []) if h.get("severity") == severity]


def _below(score, limit, inclusive):
    if score is None:
        return False
    return score <= limit if inclusive else score < limit


def compute_facts(ctx, thresholds):
    exp = _TIER_ORDER[ctx["exposure"]]
    sc = ctx.get("scores", {})
    return {
        "live_critical_hit": bool(_live(ctx, "critical")),
        "live_major_hit": bool(_live(ctx, "major")),
        "undisclosed": bool(ctx.get("undisclosed")),
        "safety_blocker": bool(ctx.get("safety_blockers")),
        "safety_cg_fails": bool(ctx.get("safety_cg_failed")),
        "exposure_e3": exp == 3,
        "exposure_e1_or_e2": exp in (1, 2),
        "exposure_ge_e1": exp >= 1,
        "exposure_ge_e2": exp >= 2,
        "trigger_low": _below(sc.get("trigger"), thresholds["trigger_low_max"], True),
        "safety_below_target": _below(sc.get("safety"), thresholds["safety_target"], False),
        "scripts_tools_low": _below(sc.get("scripts_tools"), thresholds["scripts_tools_low_max"], True),
    }


def evaluate(facts, table):
    for rule in table:
        if applies_if.evaluate(rule["when"], facts):
            return rule
    raise applies_if.ExprError("risk table has no matching rule")


def _failed_text(ctx, gate_short):
    parts = []
    for g in ctx.get("safety_cg_failed", []):
        s = gate_short.get(g)
        parts.append("%s (%s)" % (g, s) if s else g)
    return ", ".join(parts)


def rationale(rule, ctx, gate_short):
    sev = {"C1": "critical", "H2": "major"}.get(rule["id"])
    hits = _live(ctx, sev) if sev else ctx.get("live_hits", [])
    sc = ctx.get("scores", {})
    scores = ", ".join("%s %s" % (k, "n/a" if sc.get(k) is None else sc[k])
                       for k in ("trigger", "safety", "scripts_tools"))
    text = rule["explain"].format(
        exposure=ctx["exposure"],
        hits=", ".join("%s (%s)" % (h["hit_id"], h["severity"]) for h in hits),
        undisclosed=", ".join(ctx.get("undisclosed", [])),
        blockers=", ".join(ctx.get("safety_blockers", [])),
        failed=_failed_text(ctx, gate_short),
        scores=scores)
    return "%s — rule %s: %s" % (rule["tier"].capitalize(), rule["id"], text)


def failed_review_tier(hits, table):
    ctx = {"exposure": "E0", "live_hits": list(hits), "scores": {}}
    facts = compute_facts(ctx, {"trigger_low_max": 0, "safety_target": 0, "scripts_tools_low_max": 0})
    for rule in table:
        if rule["id"] in ("C1", "H2") and applies_if.evaluate(rule["when"], facts):
            return rule["tier"], rule["id"]
    return "unknown", None


def rule_items(rule_id, ctx):
    if rule_id in ("C1", "H2"):
        sev = "critical" if rule_id == "C1" else "major"
        return set(("hit", h["hit_id"]) for h in _live(ctx, sev))
    if rule_id in ("C2", "H3"):
        return set(("capability", u) for u in ctx.get("undisclosed", []))
    if rule_id == "C3":
        return set(("gate", g) for g in ctx.get("safety_blockers", []))
    if rule_id in ("H1", "M1", "M2"):
        return set(("gate", g) for g in ctx.get("safety_cg_failed", []))
    if rule_id == "M3":
        gates = list(ctx.get("failed_gates", [])) + list(ctx.get("safety_cg_failed", []))
        return set(("gate", g) for g in gates if g.startswith("SCT-"))
    return set()
