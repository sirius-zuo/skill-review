"""Calibration suite tools: metrics, mutations, and the corpus fetch extraction guard.

No network access: fetch_corpus is tested only through its tar extraction function.
"""
import io
import json
import os
import sys
import tarfile
import unittest

import _helpers
from _helpers import FIXTURES_DIR, ROOT_DIR, run_script, tempdir

CAL_DIR = os.path.join(ROOT_DIR, "tests", "calibration")
if CAL_DIR not in sys.path:
    sys.path.insert(0, CAL_DIR)

from mutate import apply_mutation  # noqa: E402
from score_calibration import (cohen_kappa, corpus_matches, mutation_detection,  # noqa: E402
                               percent_agreement)
import fetch_corpus  # noqa: E402

GOOD = os.path.join(FIXTURES_DIR, "good-skill")


def load_json(name):
    with open(os.path.join(CAL_DIR, name), encoding="utf-8") as f:
        return json.load(f)


def mutations():
    return {m["id"]: m for m in load_json("mutations.json")["mutations"]}


def pipeline(skill_dir):
    """Run discover, bundle, lint, scan on skill_dir; return (work_dir, manifest)."""
    out = os.path.join(os.path.dirname(skill_dir), "run")
    p = run_script("discover.py", "run", skill_dir, "--self-dir", ROOT_DIR, "--out", out,
                   "--no-kit", "--no-routing")
    if p.returncode != 0:
        raise AssertionError("discover failed: %s %s" % (p.stdout, p.stderr))
    work = json.loads(p.stdout)["work_dir"]
    for script in ("bundle.py", "lint.py", "scan.py"):
        p = run_script(script, "--work-dir", work)
        if p.returncode != 0:
            raise AssertionError("%s failed: %s %s" % (script, p.stdout, p.stderr))
    with open(os.path.join(work, "manifest.json"), encoding="utf-8") as f:
        manifest = json.load(f)
    return work, manifest


def results(skill_dir):
    work, manifest = pipeline(skill_dir)
    lint_ids, hits, text = set(), [], ""
    for sk in manifest["skills"]:
        with open(os.path.join(work, sk["key"], "lint.json"), encoding="utf-8") as f:
            lint_ids.update(x["rule_id"] for x in json.load(f)["findings"])
        with open(os.path.join(work, sk["key"], "scan.json"), encoding="utf-8") as f:
            hits.extend(json.load(f)["hits"])
    for dirpath, _d, files in os.walk(work):
        for fn in files:
            with open(os.path.join(dirpath, fn), "rb") as f:
                text += f.read().decode("utf-8", "replace")
    return lint_ids, hits, text


REF = ("brand-guidelines", "internal-comms", "pdf")


def _corpus():
    with open(os.path.join(ROOT_DIR, "tests", "calibration", "corpus.json")) as f:
        return json.load(f)


def _mutations():
    with open(os.path.join(ROOT_DIR, "tests", "calibration", "mutations.json")) as f:
        return json.load(f)["mutations"]


def _as_expected(m):
    """A skill result that meets every expectation of mutation m."""
    exp = m["expect"]
    gates = {gid: {"answer": ans} for gid, ans in exp["gates"].items()}
    return {"risk_tier": exp["tier"] or "Low", "quality_band": None,
            "lint_findings": [{"rule_id": r} for r in exp["lint"]],
            "scan_hits": [{"pattern_id": p} for p in exp["hits"]],
            "categories": {"c": {"gates": gates}}}


class GatingTests(unittest.TestCase):
    def test_s1_needs_all_three_reference_skills(self):
        corpus = _corpus()
        full = {n: [{"risk_tier": "Low"}] for n in REF}
        self.assertIs(corpus_matches(corpus, full)[1], True)
        for missing in REF:
            runs = dict((n, v) for n, v in full.items() if n != missing)
            self.assertIsNone(corpus_matches(corpus, runs)[1], missing)
        # A missing reference skill is NO DATA even when another one mismatched.
        runs = {"brand-guidelines": [{"risk_tier": "Critical"}], "pdf": [{"risk_tier": "Low"}]}
        self.assertIsNone(corpus_matches(corpus, runs)[1])
        self.assertIsNone(corpus_matches(corpus, {})[1])

    def test_s2_counts_missing_mutation_runs(self):
        corpus, muts = _corpus(), _mutations()
        base = {e["name"]: [{"risk_tier": e["expected_tiers"][0]}] for e in corpus["skills"]}
        runs = dict(base)
        for m in muts:
            runs[m["id"]] = [_as_expected(m)]
        det, jud, _ = mutation_detection(muts, runs, corpus)
        self.assertEqual(det, 1.0)
        self.assertEqual(jud, 1.0)
        for m in muts:
            partial = dict((k, v) for k, v in runs.items() if k != m["id"])
            det, jud, detail = mutation_detection(muts, partial, corpus)
            self.assertFalse(det == 1.0 and jud == 1.0, m["id"])
            self.assertIn((m["id"], "no run"), detail)
            if m["expect"]["lint"] or m["expect"]["hits"]:
                self.assertLess(det, 1.0, m["id"])

    def test_s2_no_runs_is_no_data(self):
        det, jud, _ = mutation_detection(_mutations(), {}, _corpus())
        self.assertIsNone(det)
        self.assertIsNone(jud)

    def test_cli_partial_data_never_passes(self):
        with tempdir() as d:
            run = os.path.join(d, "run")
            os.makedirs(run)
            m = _mutations()[0]
            skills = [dict(_as_expected(m), skill=m["id"]),
                      {"skill": "brand-guidelines", "risk_tier": "Low"}]
            with open(os.path.join(run, "results.json"), "w") as f:
                json.dump({"target": d, "skills": skills}, f)
            p = _helpers.run_path(os.path.join(ROOT_DIR, "tests", "calibration",
                                               "score_calibration.py"),
                                  "--runs", run, "--corpus",
                                  os.path.join(ROOT_DIR, "tests", "calibration", "corpus.json"),
                                  "--mutations",
                                  os.path.join(ROOT_DIR, "tests", "calibration",
                                               "mutations.json"))
            lines = [l for l in p.stdout.splitlines() if l.strip().startswith(("S1", "S2"))]
            self.assertEqual(len(lines), 3, p.stdout + p.stderr)
            for line in lines:
                self.assertFalse(line.rstrip().endswith("PASS"), line)
            self.assertTrue(lines[0].rstrip().endswith("NO DATA"), lines[0])


class MetricTests(unittest.TestCase):
    def test_kappa_known_values(self):
        self.assertAlmostEqual(cohen_kappa(["y", "y", "n", "n"], ["y", "y", "n", "n"]), 1.0)
        self.assertAlmostEqual(cohen_kappa(["y", "n", "y", "n"], ["y", "y", "n", "n"]), 0.0)

    def test_kappa_degenerate_and_mismatch(self):
        self.assertAlmostEqual(cohen_kappa(["y", "y"], ["y", "y"]), 1.0)
        with self.assertRaises(ValueError):
            cohen_kappa(["y"], ["y", "n"])

    def test_percent_agreement(self):
        self.assertAlmostEqual(percent_agreement(["y", "n", "y", "n"], ["y", "y", "n", "n"]), 0.5)
        self.assertAlmostEqual(percent_agreement(["y"], ["y"]), 1.0)


class DataTests(unittest.TestCase):
    def test_corpus_shape(self):
        c = load_json("corpus.json")
        self.assertEqual(c["anthropic_skills_sha"], "683bc88e56f3e09ba94f7055977f3d3aa499f202")
        names = [s["name"] for s in c["skills"]]
        self.assertEqual(names, ["good-skill", "text-formatter", "bad-skill", "file-deployer",
                                 "brand-guidelines", "internal-comms", "pdf"])
        by = {s["name"]: s for s in c["skills"]}
        self.assertEqual(by["file-deployer"]["expected_tiers"], ["Critical"])
        self.assertEqual(by["pdf"]["expected_tiers"], ["Low", "Medium"])
        self.assertEqual(by["good-skill"]["expected_band"], "Strong")
        for s in c["skills"]:
            if s["source"] == "fixture":
                self.assertTrue(os.path.isdir(os.path.join(ROOT_DIR, s["path"])), s["path"])

    def test_mutation_ids(self):
        self.assertEqual(sorted(mutations()), ["MU-%02d" % i for i in range(1, 14)])


class MutationTests(unittest.TestCase):
    def apply(self, mid, base=GOOD, extra=None):
        with tempdir() as d:
            out = os.path.join(d, "good-skill")
            m = mutations()[mid]
            skill = apply_mutation(base, m, out)
            r = results(skill)
            extra and extra(skill, r)
            return r

    def _check(self, mid):
        exp = mutations()[mid]["expect"]
        lint_ids, hits, _ = self.apply(mid)
        for rid in exp["lint"]:
            self.assertIn(rid, lint_ids, mid)
        ids = [h["pattern_id"] for h in hits]
        for pid in exp["hits"]:
            self.assertIn(pid, ids, mid)

    def test_deterministic_mutations(self):
        for mid in ("MU-02", "MU-05", "MU-09", "MU-11", "MU-12", "MU-13"):
            with self.subTest(mutation=mid):
                self._check(mid)

    def test_other_mutations_apply(self):
        for mid in ("MU-01", "MU-03", "MU-04", "MU-07", "MU-08"):
            with self.subTest(mutation=mid):
                self._check(mid)

    def test_mu13_is_minor_pip(self):
        _, hits, _ = self.apply("MU-13")
        pip = [h for h in hits if h["pattern_id"] == "SEC-SC-UNPINNED-PIP"]
        self.assertTrue(pip)
        self.assertEqual(pip[0]["severity"], "minor")

    def test_mu11_script_bundled_and_scanned(self):
        _, hits, text = self.apply("MU-11")
        self.assertTrue(any(h["file"].startswith("examples/") for h in hits))

    def test_mu12_target_never_read(self):
        with tempdir() as d:
            secret = os.path.join(d, "secret.txt")
            with open(secret, "w") as f:
                f.write("SECRET-CONTENT\n")
            m = dict(mutations()["MU-12"])
            m["args"] = dict(m["args"], target=secret)
            out = os.path.join(d, "work", "good-skill")
            skill = apply_mutation(GOOD, m, out)
            self.assertTrue(os.path.islink(os.path.join(skill, m["args"]["path"])))
            _, hits, text = results(skill)
            self.assertIn("SEC-SYMLINK-ESCAPE", [h["pattern_id"] for h in hits])
            self.assertNotIn("SECRET-CONTENT", text)

    def test_base_untouched(self):
        with open(os.path.join(GOOD, "SKILL.md"), "rb") as f:
            before = f.read()
        self.apply("MU-08")
        with open(os.path.join(GOOD, "SKILL.md"), "rb") as f:
            self.assertEqual(f.read(), before)


def _tar_bytes(members):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, kind, data in members:
            ti = tarfile.TarInfo(name)
            if kind == "file":
                ti.size = len(data)
                tf.addfile(ti, io.BytesIO(data))
            elif kind == "symlink":
                ti.type = tarfile.SYMTYPE
                ti.linkname = data
                tf.addfile(ti)
            elif kind == "dev":
                ti.type = tarfile.CHRTYPE
                tf.addfile(ti)
    return buf.getvalue()


class FetchTests(unittest.TestCase):
    def extract(self, members, names=("pdf",)):
        with tempdir() as d:
            dest = os.path.join(d, "dest")
            os.makedirs(dest)
            data = _tar_bytes(members)
            try:
                fetch_corpus.extract_skills(io.BytesIO(data), dest, names)
            finally:
                self.result_listing = sorted(
                    os.path.relpath(os.path.join(p, f), d)
                    for p, _ds, fs in os.walk(d) for f in fs)
            return self.result_listing

    def test_fetch_rejects_traversal(self):
        with self.assertRaises(ValueError):
            self.extract([("repo/skills/pdf/SKILL.md", "file", b"x"),
                          ("../evil", "file", b"bad")])
        self.assertFalse([p for p in self.result_listing if "evil" in p])

    def test_fetch_rejects_traversal_inside_skill_path(self):
        with self.assertRaises(ValueError):
            self.extract([("repo/skills/pdf/../../../evil", "file", b"bad")])

    def test_fetch_rejects_absolute(self):
        with self.assertRaises(ValueError):
            self.extract([("/etc/evil", "file", b"bad")])

    def test_fetch_skips_symlink_and_device(self):
        listing = self.extract([
            ("repo/skills/pdf/SKILL.md", "file", b"hello"),
            ("repo/skills/pdf/link", "symlink", "/etc/passwd"),
            ("repo/skills/pdf/dev", "dev", b""),
            ("repo/skills/other/SKILL.md", "file", b"no")])
        self.assertEqual(listing, [os.path.join("dest", "pdf", "SKILL.md")])

    def test_fetch_refuses_dest_inside_repo(self):
        with self.assertRaises(ValueError):
            fetch_corpus.check_dest(os.path.join(ROOT_DIR, "tests", "calibration", "x"))


if __name__ == "__main__":
    unittest.main()
