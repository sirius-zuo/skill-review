import json
import os
import re
import subprocess
import sys
import unittest

import _helpers  # noqa: F401  (sets sys.path)

from common import read_json, scoring, write_json
import routing

SCRIPT = os.path.join(_helpers.SCRIPTS_DIR, "routing.py")


def _kit(name, pos, neg):
    cases = []
    for i, p in enumerate(pos):
        cases.append({"id": "T%d" % i, "prompt": p, "should_trigger": True,
                      "rationale": "r", "near_miss_of": None})
    for i, p in enumerate(neg):
        cases.append({"id": "N%d" % i, "prompt": p, "should_trigger": False,
                      "rationale": "r", "near_miss_of": None})
    return {"schema_version": 1, "skill": name, "kit_id": "k", "trigger_cases": cases,
            "task_cases": [], "safety_cases": [], "fixtures": []}


def _setup(tmp):
    root = os.path.join(tmp, "root")
    work = os.path.join(tmp, "work")
    kits = os.path.join(tmp, "kit")
    skills = []
    for i, name in enumerate(["alpha", "beta"]):
        d = os.path.join(root, name)
        os.makedirs(d)
        with open(os.path.join(d, "SKILL.md"), "w") as f:
            f.write("---\nname: %s\ndescription: Does %s things\n---\nbody\n" % (name, name))
        key = "%02d-%s" % (i + 1, name)
        skills.append({"key": key, "name": name, "dir": name, "kind": "skill",
                       "skill_file": name + "/SKILL.md"})
        write_json(os.path.join(kits, key, "kit.json"),
                   _kit(name, ["use %s a%d" % (name, j) for j in range(2)],
                        ["other %s %d" % (name, j) for j in range(2)]))
    write_json(os.path.join(work, "manifest.json"), {"root": root, "skills": skills})
    write_json(os.path.join(work, "run.json"), {"nonce": "abc123", "kit_dir": kits})
    return work


def sev(recall=1.0, ftr=0.0, confusions=None):
    m = {"s": {"recall": recall, "false_trigger_rate": ftr, "confusions": confusions or {}},
         "fmt": {"recall": 1.0, "false_trigger_rate": 0.0, "confusions": {}}}
    out = routing.findings(m, scoring()["routing"])["s"]
    return out


class RoutingTests(unittest.TestCase):
    def test_distractors(self):
        d = read_json(os.path.join(_helpers.ROOT_DIR, "kit-templates", "distractors.json"))
        self.assertEqual(len(d["distractors"]), 15)

    def test_prepare_shuffles_and_wraps(self):
        with _helpers.tempdir() as tmp:
            w = _setup(tmp)
            p1 = routing.prepare(w, 1)
            p2 = routing.prepare(w, 2)
            t1, t2 = open(p1).read(), open(p2).read()
            self.assertEqual(os.path.basename(p1), "routing-input-1.txt")
            self.assertNotEqual(t1, t2)
            self.assertIn('<untrusted nonce="abc123">', t1)
            self.assertIn("Does alpha things", t1)
            self.assertEqual(t1, open(routing.prepare(w, 1)).read())
            m = read_json(os.path.join(w, "routing-map.json"))
            self.assertEqual(len(m["prompts"]), 8)
            self.assertEqual(len([n for n in m["skills"]]), 17)
            for dd in read_json(os.path.join(_helpers.ROOT_DIR, "kit-templates",
                                             "distractors.json"))["distractors"]:
                self.assertIn(dd["name"], t1)
            # every description wrapped: no unwrapped description line
            self.assertEqual(t1.count("<untrusted nonce="), t1.count("</untrusted nonce="))

    def test_order_differs(self):
        with _helpers.tempdir() as tmp:
            w = _setup(tmp)
            ids = []
            for n in (1, 2, 3):
                t = open(routing.prepare(w, n)).read()
                ids.append(re.findall(r"^(P\d+):", t, re.M))
            self.assertEqual(sorted(ids[0]), sorted(ids[1]))
            self.assertTrue(ids[0] != ids[1] or ids[1] != ids[2])

    def test_metrics_majority_and_ties(self):
        kits = {"a": {}, "b": {}}
        pm = {"P1": {"skill": "a", "should_trigger": True},
              "P2": {"skill": "a", "should_trigger": True},
              "P3": {"skill": "a", "should_trigger": False}}
        calls = [{"P1": "a", "P2": "a", "P3": "a"},
                 {"P1": "a", "P2": "b", "P3": "none"},
                 {"P1": "b", "P2": "none", "P3": "a"}]
        m = routing.metrics(kits, calls, pm)
        self.assertEqual(m["a"]["recall"], 0.5)  # P1 -> a, P2 tie -> none
        self.assertEqual(m["a"]["false_trigger_rate"], 1.0)
        self.assertEqual(m["a"]["confusions"], {})
        calls[1]["P2"] = "b"
        calls[2]["P2"] = "b"
        m = routing.metrics(kits, calls, pm)
        self.assertEqual(m["a"]["confusions"], {"b": ["P2"]})

    def test_findings_thresholds(self):
        def ids(**kw):
            return [(f["id"], f["severity"]) for f in sev(**kw)]
        self.assertEqual(ids(recall=0.49), [("RT-UNDER", "major")])
        self.assertEqual(ids(recall=0.5), [("RT-UNDER", "minor")])
        self.assertEqual(ids(recall=0.8), [])
        self.assertEqual(ids(ftr=0.21), [("RT-OVER", "minor")])
        self.assertEqual(ids(ftr=0.2), [])
        f = sev(confusions={"fmt": ["P1", "P2"]})
        self.assertEqual([(x["id"], x["severity"]) for x in f], [("RT-COLLIDE", "major")])
        self.assertIn("fmt", f[0]["text"])
        self.assertEqual(sev(confusions={"fmt": ["P1"]}), [])
        self.assertTrue(f[0]["simulated"])
        self.assertEqual(f[0]["source"], "routing")
        self.assertEqual(f[0]["category"], "trigger")

    def test_distractor_win_not_collision(self):
        m = {"s": {"recall": 0.0, "false_trigger_rate": 0.0,
                   "confusions": {"pdf-handler": ["P1", "P2"]}},
             "t": {"recall": 1.0, "false_trigger_rate": 0.0, "confusions": {}}}
        f = routing.findings(m, scoring()["routing"])["s"]
        self.assertEqual([x["id"] for x in f], ["RT-UNDER"])
        m["s"]["confusions"] = {"t": ["P1", "P2"]}
        f = routing.findings(m, scoring()["routing"])["s"]
        self.assertIn("RT-COLLIDE", [x["id"] for x in f])

    def test_findings_none_recall(self):
        self.assertEqual(sev(recall=None), [])

    def test_check_rejects_unknown_names(self):
        with _helpers.tempdir() as tmp:
            w = _setup(tmp)
            routing.prepare(w, 1)
            pm = read_json(os.path.join(w, "routing-map.json"))
            good = dict((p, "none") for p in pm["prompts"])
            doc = {"schema_version": 1, "engine": "script", "call": 1, "choices": good}
            write_json(os.path.join(w, "routing-1.json"), doc)
            self.assertEqual(routing.check(w, 1), [])
            bad = dict(good)
            bad["P1"] = "nonexistent"
            doc["choices"] = bad
            write_json(os.path.join(w, "routing-1.json"), doc)
            self.assertTrue(routing.check(w, 1))
            missing = dict(good)
            del missing["P2"]
            doc["choices"] = missing
            write_json(os.path.join(w, "routing-1.json"), doc)
            self.assertTrue(any("P2" in e for e in routing.check(w, 1)))
            r = subprocess.run([sys.executable, SCRIPT, "check", "--work-dir", w, "--call", "1"],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertEqual(r.returncode, 1)

    def test_cli_prepare(self):
        with _helpers.tempdir() as tmp:
            w = _setup(tmp)
            r = subprocess.run([sys.executable, SCRIPT, "prepare", "--work-dir", w, "--call", "2"],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue(os.path.exists(os.path.join(w, "routing-input-2.txt")))


if __name__ == "__main__":
    unittest.main()
