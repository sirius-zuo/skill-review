import json
import os
import subprocess
import unittest

import _helpers
from _helpers import FIXTURES_DIR, make_tree, run_script, tempdir
from common import read_json, validate_against
from discover import build_manifest
from ingest import build_ingest, detect_format, freshness, normalize

FX = os.path.join(FIXTURES_DIR, "ingest")


def load(name):
    return read_json(os.path.join(FX, name))


def raw(name):
    with open(os.path.join(FX, name), encoding="utf-8") as f:
        return f.read()


def lookup_fixture_kit(_source):
    return load("kit.json")


def no_kit(_source):
    return None


SK = "---\nname: %s\ndescription: d\n---\nbody\n"


def git(root, *args):
    subprocess.run(["git", "-C", root] + list(args), check=True, stdout=subprocess.PIPE,
                   stderr=subprocess.PIPE)


class IngestTests(unittest.TestCase):
    def test_detect(self):
        self.assertEqual(detect_format(load("kit-results.json"), "kit-results.json"), "kit-results")
        self.assertEqual(detect_format(load("plugin-eval.json"), "x.json"), "claude-plugin-eval")
        self.assertEqual(detect_format(load("benchmark.json"), "benchmark.json"), "skill-creator-benchmark")
        self.assertIsNone(detect_format({"cases": [{"arms": {}}]}, "x.json"))
        self.assertIsNone(detect_format([1], "x.json"))

    def test_kit_results_rates_and_trigger(self):
        r = normalize("kit-results", load("kit-results.json"),
                      "s/evals/skill-review-kit/kit-results.json", lookup_fixture_kit)
        self.assertEqual((r["with_pass_rate"], r["without_pass_rate"], r["delta"]), (0.75, 0.25, 0.5))
        self.assertEqual((r["trigger_precision"], r["trigger_recall"], r["stale_kit"]), (1.0, 0.5, False))
        self.assertEqual((r["n_cases"], r["runs_per_case"], r["skill_name"], r["valid"]), (2, 2, "demo", True))

    def test_kit_results_stale(self):
        r = normalize("kit-results", load("kit-results.json"), "k.json", no_kit)
        self.assertTrue(r["stale_kit"])
        self.assertIsNone(r["trigger_recall"])
        other = dict(load("kit.json"), kit_id="other")
        r = normalize("kit-results", load("kit-results.json"), "k.json", lambda s: other)
        self.assertTrue(r["stale_kit"])

    def test_plugin_eval(self):
        r = normalize("claude-plugin-eval", load("plugin-eval.json"), "x.json", no_kit)
        self.assertAlmostEqual(r["with_pass_rate"], 0.8)
        self.assertAlmostEqual(r["without_pass_rate"], 0.5)
        self.assertEqual((r["n_cases"], r["runs_per_case"], r["date"], r["valid"]), (2, 2, None, True))

    def test_benchmark(self):
        r = normalize("skill-creator-benchmark", load("benchmark.json"), "benchmark.json", no_kit)
        self.assertEqual((r["with_pass_rate"], r["without_pass_rate"]), (0.85, 0.35))
        self.assertEqual((r["skill_name"], r["date"], r["runs_per_case"], r["n_cases"], r["valid"]),
                         ("pdf", "2026-01-15T10:30:00Z", 3, 3, True))

    def test_partial_and_bad_rates_invalid(self):
        r = normalize("claude-plugin-eval", load("partial-plugin-eval.json"), "x.json", no_kit)
        self.assertFalse(r["valid"])
        self.assertIn("partial suite", r["warnings"])
        r = normalize("claude-plugin-eval", load("bad-rates.json"), "x.json", no_kit)
        self.assertFalse(r["valid"])

    def test_mapping_repo_level(self):
        with tempdir() as root:
            make_tree(root, {"s/SKILL.md": SK % "s", "plugin-eval.json": raw("plugin-eval.json"),
                             "s/benchmark.json": raw("benchmark.json")})
            m = build_manifest(root)
            out = build_ingest(m)
            self.assertEqual(len(out["repo"]["results"]), 1)
            self.assertEqual(out["repo"]["results"][0]["source_file"], "plugin-eval.json")
            skill = out["skills"][m["skills"][0]["key"]]
            self.assertEqual([r["source_file"] for r in skill["results"]], ["s/benchmark.json"])
            self.assertTrue(skill["has_valid_result"])
            self.assertTrue(skill["has_baseline"])
            self.assertEqual(validate_against(skill, "ingest"), [])
            self.assertEqual(validate_against(out["repo"], "kit-results" if False else "ingest"), [])

    def test_explicit_name_wins(self):
        with tempdir() as root:
            make_tree(root, {"a/SKILL.md": SK % "a", "pdf/SKILL.md": SK % "pdf",
                             "a/benchmark.json": raw("benchmark.json")})
            m = build_manifest(root)
            out = build_ingest(m)
            by = {s["name"]: s["key"] for s in m["skills"]}
            self.assertEqual(len(out["skills"][by["pdf"]]["results"]), 1)
            self.assertEqual(out["skills"][by["a"]]["results"], [])

    def test_cli_and_invalid(self):
        with tempdir() as root, tempdir() as work:
            make_tree(root, {"s/SKILL.md": SK % "s", "s/bad-rates.json": raw("bad-rates.json"),
                             "s/kit-results.json": "{not json"})
            m = build_manifest(root)
            from common import write_json
            write_json(os.path.join(work, "manifest.json"), m, "manifest")
            p = run_script("ingest.py", "--work-dir", work)
            self.assertEqual(p.returncode, 0, p.stderr)
            d = read_json(os.path.join(work, m["skills"][0]["key"], "ingest.json"))
            self.assertEqual(d["results"], [])
            self.assertEqual(len(d["invalid"]), 2)
            self.assertFalse(d["has_valid_result"])
            self.assertEqual(validate_against(read_json(os.path.join(work, "repo-ingest.json")), "ingest"), [])

    def test_oversized_numbers_are_invalid_not_fatal(self):
        pe = load("plugin-eval.json")
        pe["cases"][0]["aggregates"]["score"] = 10 ** 400
        bm = load("benchmark.json")
        bm["run_summary"]["with_skill"]["pass_rate"]["mean"] = 10 ** 400
        with tempdir() as root:
            make_tree(root, {"s/SKILL.md": SK % "s", "s/plugin-eval.json": json.dumps(pe),
                             "s/benchmark.json": json.dumps(bm),
                             "s/kit-results.json": raw("kit-results.json")})
            m = build_ingest(build_manifest(root))
            d = list(m["skills"].values())[0]
            bad = {i["source_file"]: i for i in d["invalid"]}
            self.assertEqual(set(bad), {"s/plugin-eval.json", "s/benchmark.json"})
            self.assertTrue(all(i["reason"] for i in bad.values()))
            self.assertEqual([r["source_file"] for r in d["results"]], ["s/kit-results.json"])
        r = normalize("claude-plugin-eval", pe, "x.json", no_kit)
        self.assertFalse(r["valid"])

    def test_freshness_unknown_without_git_and_shallow(self):
        with tempdir() as root:
            make_tree(root, {"SKILL.md": SK % "s"})
            self.assertEqual(freshness("2026-01-01T00:00:00Z", root, "SKILL.md"), "unknown")
        self.assertEqual(freshness(None, "/nonexistent-dir-xyz", "SKILL.md"), "unknown")

    def test_freshness_true_false(self):
        with tempdir() as root:
            make_tree(root, {"SKILL.md": SK % "s"})
            git(root, "init", "-q")
            git(root, "add", "--", "SKILL.md")
            git(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "x")
            self.assertEqual(freshness("2099-01-01T00:00:00Z", root, "SKILL.md"), True)
            self.assertEqual(freshness("2000-01-01T00:00:00+00:00", root, "SKILL.md"), False)
            self.assertEqual(freshness("2099-01-01", root, "SKILL.md"), True)
            self.assertEqual(freshness("garbage", root, "SKILL.md"), "unknown")
            self.assertEqual(freshness("2099-01-01", root, "untracked/SKILL.md"), "unknown")

    def test_git_path_with_leading_dash(self):
        with tempdir() as root:
            make_tree(root, {"-rf/SKILL.md": SK % "dash"})
            git(root, "init", "-q")
            git(root, "add", "--", "-rf/SKILL.md")
            git(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "x")
            self.assertEqual(freshness("2099-01-01T00:00:00Z", root, "-rf/SKILL.md"), True)
            self.assertEqual(freshness("2000-01-01T00:00:00Z", root, "-rf/SKILL.md"), False)


if __name__ == "__main__":
    unittest.main()
