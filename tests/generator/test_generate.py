# agentive-unit: selftest.component
# design: spec.framework_repo.editing_rules (FM.generate: --check writes nothing, names stale/missing/orphaned files)
"""tools/generate.py behaviour on a temporary copy of the repo."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GEN = os.path.join(ROOT, "tools", "generate.py")
# Isolated fixture: the generator inputs plus only A's own units and sources, so these tests do not depend on
# (or collide with) the sources B, C and D add under src/.
COPY = ["AGENTS.md", "framework.yaml", "package.json", "design", "tools",
        "src/units/foundation.yaml", "src/repo-layer/dot-agents/lib", "src/plugin/claude-code/assets/icon.png"]


def gen(root, *args):
    r = subprocess.run([sys.executable, GEN, "--root", root, *args], capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


class GenerateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        for p in COPY:
            s = os.path.join(ROOT, p)
            d = os.path.join(self.tmp, p)
            os.makedirs(os.path.dirname(d), exist_ok=True)
            if os.path.isdir(s):
                shutil.copytree(s, d, ignore=shutil.ignore_patterns("__pycache__"))
            else:
                shutil.copy2(s, d)
        rc, out = gen(self.tmp)
        self.assertEqual(rc, 0, out)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def path(self, rel):
        return os.path.join(self.tmp, rel)

    def snapshot(self):
        out = {}
        for d, _dirs, files in os.walk(self.tmp):
            for f in files:
                p = os.path.join(d, f)
                out[p] = (os.path.getmtime(p), os.path.getsize(p))
        return out

    def test_check_passes_and_writes_nothing(self):
        before = self.snapshot()
        rc, out = gen(self.tmp, "--check")
        self.assertEqual(rc, 0, out)
        self.assertIn("generate --check: ok", out)
        self.assertEqual(before, self.snapshot())

    def test_check_names_stale_missing_orphaned(self):
        with open(self.path("docs/DECISIONS.md"), "a", encoding="utf-8") as f:
            f.write("hand edit\n")
        os.unlink(self.path(".claude-plugin/marketplace.json"))
        os.makedirs(self.path("plugins/claude-code/agentive/extra"))
        with open(self.path("plugins/claude-code/agentive/extra/x.txt"), "w") as f:
            f.write("x")
        before = self.snapshot()
        rc, out = gen(self.tmp, "--check")
        self.assertEqual(rc, 1, out)
        self.assertIn("STALE docs/DECISIONS.md", out)
        self.assertIn("MISSING .claude-plugin/marketplace.json", out)
        self.assertIn("ORPHANED plugins/claude-code/agentive/extra/x.txt", out)
        self.assertIn("NEXT: python3 tools/generate.py", out)
        self.assertEqual(before, self.snapshot())
        rc, out = gen(self.tmp)
        self.assertEqual(rc, 0, out)
        self.assertFalse(os.path.exists(self.path("plugins/claude-code/agentive/extra")))
        rc, out = gen(self.tmp, "--check")
        self.assertEqual(rc, 0, out)

    def test_source_change_makes_outputs_stale(self):
        with open(self.path("src/repo-layer/dot-agents/lib/agentive/common.py"), "a", encoding="utf-8") as f:
            f.write("# change\n")
        rc, out = gen(self.tmp, "--check")
        self.assertEqual(rc, 1, out)
        self.assertIn("STALE plugins/claude-code/agentive/payload/repo-layer/dot-agents/lib/agentive/common.py", out)
        self.assertIn("STALE plugins/codex/agentive/skills/agentive-init/payload/repo-layer/dot-agents/lib/agentive/common.py", out)
        self.assertIn("STALE plugins/codex/agentive/skills.lock", out)

    def test_source_errors(self):
        os.makedirs(self.path("src/engine"), exist_ok=True)
        with open(self.path("src/engine/stray.py"), "w") as f:
            f.write("x = 1\n")
        os.makedirs(self.path("src/repo-layer/.claude"), exist_ok=True)
        with open(self.path("src/repo-layer/.claude/settings.json"), "w") as f:
            f.write("{}\n")
        with open(self.path("src/units/zz-test.yaml"), "w") as f:
            f.write("owner: D\nunits:\n  - id: rl.bad\n    kind: repo_layer\n    src: src/repo-layer/.claude/settings.json\n"
                    "    refs: [spec.no_such_key]\n    f: f-nope\n")
        rc, out = gen(self.tmp, "--check")
        self.assertEqual(rc, 1, out)
        self.assertIn("ERROR src/engine/stray.py: source file belongs to no unit", out)
        self.assertIn("ref 'spec.no_such_key' does not resolve", out)
        self.assertIn("f 'f-nope' is not in design spec.repo_layer.files", out)
        self.assertIn("starts with '.'", out)
        self.assertIn("nothing written", out)

    def test_header_and_exec_rules(self):
        with open(self.path("src/units/zz-test.yaml"), "w") as f:
            f.write("owner: B\nunits:\n  - id: rl.tool\n    kind: repo_layer\n    src: src/repo-layer/dot-agents/bin/tool\n"
                    "    refs: [spec.verb_wrapper]\n")
        os.makedirs(self.path("src/repo-layer/dot-agents/bin"), exist_ok=True)
        with open(self.path("src/repo-layer/dot-agents/bin/tool"), "w") as f:
            f.write("#!/bin/sh\necho hi\n")
        rc, out = gen(self.tmp, "--check")
        self.assertIn("has a shebang but is not executable", out)
        self.assertIn("no header line 'agentive-unit: rl.tool'", out)
        with open(self.path("src/repo-layer/dot-agents/bin/tool"), "w") as f:
            f.write("#!/bin/sh\n# agentive-unit: rl.tool\necho hi\n")
        os.chmod(self.path("src/repo-layer/dot-agents/bin/tool"), 0o755)
        rc, out = gen(self.tmp)
        self.assertEqual(rc, 0, out)
        p = self.path("plugins/claude-code/agentive/payload/repo-layer/dot-agents/bin/tool")
        self.assertTrue(os.access(p, os.X_OK))
        with open(self.path("plugins/claude-code/agentive/payload/index.json"), encoding="utf-8") as f:
            self.assertIn('"install": ".agents/bin/tool"', f.read())

    def test_shared_unit_regions(self):
        os.makedirs(self.path("src/repo-layer/AGENTS.md.regions"))
        with open(self.path("src/repo-layer/AGENTS.md.regions/context.md"), "w") as f:
            f.write("Review rules: @code_review.md\n")
        with open(self.path("src/units/zz-test.yaml"), "w") as f:
            f.write("owner: C\nunits:\n  - id: rl.agents-md\n    kind: repo_layer\n    src: src/repo-layer/AGENTS.md.regions/\n"
                    "    class: shared\n    regions: [context, nope]\n    f: f-agents-md\n    refs: [\"spec.repo_layer.files[f-agents-md]\"]\n")
        rc, out = gen(self.tmp, "--check")
        self.assertIn("region AGENTS.md#nope is not in design init_apply.managed_regions.regions", out)
        with open(self.path("src/units/zz-test.yaml"), "w") as f:
            f.write("owner: C\nunits:\n  - id: rl.agents-md\n    kind: repo_layer\n    src: src/repo-layer/AGENTS.md.regions/\n"
                    "    class: shared\n    regions: [context]\n    f: f-agents-md\n    refs: [\"spec.repo_layer.files[f-agents-md]\"]\n")
        rc, out = gen(self.tmp)
        self.assertEqual(rc, 0, out)
        with open(self.path("plugins/claude-code/agentive/payload/index.json"), encoding="utf-8") as f:
            text = f.read()
        self.assertIn('"region": "context"', text)
        self.assertIn('"install": "AGENTS.md"', text)


if __name__ == "__main__":
    unittest.main()
