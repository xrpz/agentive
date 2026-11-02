# agentive-unit: selftest.component
# design: decisions.accepted[DESIGNINDEX], spec.framework_repo.editing_rules (FM.generate: --check names stale generated files)
"""design/INDEX.md (generated) and tools/design.py (get, find): the index maps ids to lines without a hand-kept line number."""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import design as design_tool  # noqa: E402

GEN = os.path.join(ROOT, "tools", "generate.py")
DESIGN_PY = os.path.join(ROOT, "tools", "design.py")
COPY = ["AGENTS.md", "framework.yaml", "package.json", "design", "tools",
        "src/units/foundation.yaml", "src/repo-layer/dot-agents/lib", "src/plugin/claude-code/assets/icon.png"]


def run(*args):
    r = subprocess.run([sys.executable, DESIGN_PY, *args], capture_output=True, text=True, cwd=ROOT)
    return r.returncode, r.stdout + r.stderr


def design_lines():
    with open(os.path.join(ROOT, "design", "framework.yaml"), encoding="utf-8") as f:
        return f.read().split("\n")


class IndexContent(unittest.TestCase):
    def test_every_indexed_entry_starts_on_its_line(self):
        """The line in the index is the line of the entry in the file now (the claim a hand-written line table would lose)."""
        lines = design_lines()
        ents, _ = design_tool.entries(ROOT)
        items = [e for e in ents if "[" in e["path"]]
        self.assertGreater(len(items), 150, "decisions, contracts, unverified claims, files and stages are indexed")
        bad = []
        for e in items:
            ident = e["path"][e["path"].index("[") + 1:-1]
            first = lines[e["start"] - 1]
            if not re.search(r"\bid: [\"']?" + re.escape(ident) + r"[\"']?(?=[,}\s]|$)", first):
                bad.append((e["path"], e["start"], first[:80]))
        self.assertEqual(bad, [])

    def test_keys_and_spans_are_ordered_and_inside_the_file(self):
        lines = design_lines()
        ents, _ = design_tool.entries(ROOT)
        top = [e for e in ents if e["group"] == "top"]
        self.assertEqual([e["path"] for e in top], ["schema_version", "meta", "sources", "spec", "contracts", "unverified",
                                                    "decisions", "view", "layout"])
        for a, b in zip(top, top[1:]):
            self.assertLess(a["end"], b["start"])
        for e in ents:
            self.assertTrue(1 <= e["start"] <= e["end"] <= len(lines), e)
            self.assertTrue(lines[e["start"] - 1].strip(), e)

    def test_the_index_names_the_new_decisions(self):
        index = design_tool.render_index(ROOT)
        for ident in ("REVIEWRULES", "IMPLSPLIT", "DESIGNINDEX", "SIZEBUDGET", "SELFTESTFAST", "PRADOPT", "UV-13", "RV5", "f-agents-md"):
            self.assertIn(f"| `{ident}` |", index, ident)
        self.assertIn("python3 tools/design.py get", index)


class Lookup(unittest.TestCase):
    def test_get_prints_one_node(self):
        rc, out = run("get", "decisions.accepted[PRADOPT]", "--max-chars", "0")
        self.assertEqual(rc, 0, out)
        self.assertTrue(out.startswith("id: PRADOPT\n"), out[:80])
        self.assertIn("spec_ref:", out)

    def test_get_caps_a_long_node_and_says_how_to_read_on(self):
        rc, out = run("get", "decisions.accepted[PRADOPT]", "--max-chars", "300")
        self.assertEqual(rc, 0, out)
        self.assertIn("[cut at 300 of ", out)
        self.assertIn("NEXT: a deeper path", out)
        self.assertLess(len(out), 700)

    def test_get_a_scalar_and_a_dotted_path(self):
        rc, out = run("get", "spec.review_pipeline.stages[RV5].name")
        self.assertEqual((rc, out.strip().splitlines()[0]), (0, "specialist_reviewers"), out)
        rc, out = run("get", "spec.framework_repo.maintenance_model.id")
        self.assertEqual((rc, out.strip()), (0, "FM"))

    def test_unknown_path_fails_with_a_next(self):
        rc, out = run("get", "decisions.accepted[NOSUCHID]")
        self.assertEqual(rc, 1)
        self.assertIn("NEXT:", out)
        self.assertIn("design/INDEX.md", out)

    def test_find_lists_the_entries_that_hold_a_text(self):
        rc, out = run("find", "PRADOPT")
        self.assertEqual(rc, 0, out)
        self.assertIn("decisions.accepted[PRADOPT]\tline ", out)
        rc, out = run("find", "no such text anywhere zzz-qq")
        self.assertEqual(rc, 1)
        self.assertIn("NEXT:", out)

    def test_agents_md_tells_the_lookup_commands_and_keeps_no_line_numbers(self):
        with open(os.path.join(ROOT, "AGENTS.md"), encoding="utf-8") as f:
            text = f.read()
        self.assertIn("design/INDEX.md", text)
        self.assertIn("python3 tools/design.py get", text)
        self.assertIn("python3 tools/design.py find", text)
        self.assertIsNone(re.search(r"framework\.yaml:\d+", text), "a hand-written line number goes stale at the next edit")


class StaleIndex(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        for p in COPY:
            s, d = os.path.join(ROOT, p), os.path.join(self.tmp, p)
            os.makedirs(os.path.dirname(d), exist_ok=True)
            if os.path.isdir(s):
                shutil.copytree(s, d, ignore=shutil.ignore_patterns("__pycache__"))
            else:
                shutil.copy2(s, d)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def gen(self, *args):
        r = subprocess.run([sys.executable, os.path.join(self.tmp, "tools", "generate.py"), "--root", self.tmp, *args],
                           capture_output=True, text=True)
        return r.returncode, r.stdout + r.stderr

    def test_check_names_a_stale_index_after_a_design_edit_and_generate_fixes_it(self):
        self.assertEqual(self.gen()[0], 0)
        self.assertEqual(self.gen("--check")[0], 0)
        path = os.path.join(self.tmp, "design", "framework.yaml")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        # one extra line before the decisions block moves every later line by one
        text = text.replace("\nunverified:\n", "\n# one more comment line\nunverified:\n", 1)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        rc, out = self.gen("--check")
        self.assertEqual(rc, 1, out)
        self.assertIn("STALE design/INDEX.md", out)
        self.assertEqual(self.gen()[0], 0)
        self.assertEqual(self.gen("--check")[0], 0)

    def test_a_deleted_index_is_reported_missing(self):
        self.assertEqual(self.gen()[0], 0)
        os.unlink(os.path.join(self.tmp, "design", "INDEX.md"))
        rc, out = self.gen("--check")
        self.assertEqual(rc, 1)
        self.assertIn("MISSING design/INDEX.md", out)


if __name__ == "__main__":
    unittest.main()
