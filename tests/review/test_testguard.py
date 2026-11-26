# agentive-unit: selftest.component
# design: decisions.accepted[TESTGUARD], spec.review_pipeline.stages[RV1], spec.review_pipeline.stages[RV2], spec.review_pipeline.stages[RV8]
"""TESTGUARD (user 2026-11-26): the deterministic detector of weakened tests (testguard.py), per language pattern, the clean diff,
and its use by CI: RV1 signals, RV2 always item, RV4 tier 2, RV8 human_required with the code test_weakened, the comment text."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers as h  # noqa: E402
from test_stages import GateTest  # noqa: E402
from test_secpaths import FakeDiff, block, load  # noqa: E402

from agentive import testguard  # noqa: E402
from agentive.review import gitio, post, rv1, rv2, rv4  # noqa: E402


def run(base, head, statuses=None):
    """detect() over two in-memory trees {path: text}; every path of either tree is `M`/`A`/`D` unless statuses is given."""
    if statuses is None:
        statuses = [("D" if p not in head else "A" if p not in base else "M", p, None) for p in sorted(set(base) | set(head))]
    return testguard.detect(statuses, base.get, head.get, lambda p: rv1.is_test(p, rv1.TEST_GLOBS))


def kinds(found):
    return sorted((f["kind"], f["path"]) for f in found)


PY_BASE = """import pytest


def test_total():
    assert total([1, 2]) == 3
    assert total([]) == 0


class TestDiscount:
    def test_ten_percent(self):
        assert discount(100) == 90
"""


class DetectorTest(unittest.TestCase):
    def test_a_clean_diff_reports_nothing(self):
        head = PY_BASE.replace("== 90", "== 90.0") + "\n\ndef test_new():\n    assert True\n"
        self.assertEqual(run({"tests/test_a.py": PY_BASE, "src/a.py": "x=1\n"}, {"tests/test_a.py": head, "src/a.py": "x=2\n"}), [])
        self.assertEqual(run({}, {}), [])
        self.assertEqual(run({"src/a.py": "x=1\n"}, {"src/a.py": "x=2\n"}), [])

    def test_deleted_test_file_unless_its_production_file_goes_too(self):
        got = run({"tests/test_a.py": PY_BASE, "src/a.py": "x=1\n"}, {"src/a.py": "x=1\n"})
        self.assertEqual(kinds(got), [("deleted_test_file", "tests/test_a.py")])
        self.assertEqual(run({"tests/test_a.py": PY_BASE, "src/a.py": "x=1\n"}, {}), [])

    def test_python_removed_test_with_class_and_line(self):
        head = PY_BASE.replace("def test_total", "def helper_total")
        got = run({"tests/test_a.py": PY_BASE}, {"tests/test_a.py": head})
        self.assertEqual([(f["kind"], f["line"]) for f in got], [("removed_test", 4)])
        self.assertIn("test test_total is gone", got[0]["detail"])
        got = run({"tests/test_a.py": PY_BASE}, {"tests/test_a.py": PY_BASE.replace("def test_ten_percent", "def check_ten")})
        self.assertIn("TestDiscount.test_ten_percent", got[0]["detail"])

    def test_a_renamed_or_moved_test_is_not_a_removal(self):
        renamed = PY_BASE.replace("test_total", "test_total_of_list")
        self.assertEqual(run({"tests/test_a.py": PY_BASE}, {"tests/test_a.py": renamed}), [])
        # a file move seen by git -M is an R status: its tests are compared with the old path's
        self.assertEqual(run({"tests/test_a.py": PY_BASE}, {"tests/test_b.py": PY_BASE}, [("R", "tests/test_b.py", "tests/test_a.py")]), [])
        # a test moved into another changed file keeps its body, so it is no removal
        a2 = PY_BASE.replace("def test_total", "def gone_total")
        b = "def test_total():\n    assert total([1, 2]) == 3\n    assert total([]) == 0\n"
        self.assertEqual([f["kind"] for f in run({"tests/test_a.py": PY_BASE, "tests/test_b.py": ""}, {"tests/test_a.py": a2, "tests/test_b.py": b})], [])

    def test_assertions_dropped_in_the_same_test(self):
        head = PY_BASE.replace("    assert total([]) == 0\n", "")
        got = run({"tests/test_a.py": PY_BASE}, {"tests/test_a.py": head})
        self.assertEqual([(f["kind"], f["line"]) for f in got], [("assertions_dropped", 4)])
        self.assertIn("holds 1 assertion(s), the base version had 2", got[0]["detail"])

    def test_skip_markers_per_language(self):
        cases = {
            "tests/test_a.py": ("def test_a():\n    assert 1\n", "@pytest.mark.skip(reason='x')\ndef test_a():\n    assert 1\n"),
            "tests/test_b.py": ("def test_b():\n    assert 1\n", "@pytest.mark.xfail\ndef test_b():\n    assert 1\n"),
            "tests/test_c.py": ("class T:\n    def test_c(self):\n        assert 1\n", "@unittest.skip('x')\nclass T:\n    def test_c(self):\n        assert 1\n"),
            "web/a.test.ts": ("it('a', () => { expect(1).toBe(1) })\n", "it.skip('a', () => { expect(1).toBe(1) })\n"),
            "web/b.test.ts": ("describe('b', () => {})\n", "describe.only('b', () => {})\n"),
            "web/c.test.js": ("it('c', () => { expect(1).toBe(1) })\n", "xit('c', () => { expect(1).toBe(1) })\n"),
            "pkg/a_test.go": ("func TestA(t *testing.T) {\n\tt.Fatal()\n}\n", "func TestA(t *testing.T) {\n\tt.Skip(\"later\")\n\tt.Fatal()\n}\n"),
            "src/test/java/ATest.java": ("class ATest {\n  @Test\n  void a() { assertEquals(1, 1); }\n}\n",
                                         "class ATest {\n  @Test\n  @Disabled\n  void a() { assertEquals(1, 1); }\n}\n"),
            "src/test/java/BTest.java": ("class BTest {\n  @Test\n  void b() { assertEquals(1, 1); }\n}\n",
                                         "class BTest {\n  @Ignore\n  @Test\n  void b() { assertEquals(1, 1); }\n}\n"),
            "src/lib.rs": ("#[test]\nfn r() { assert_eq!(1, 1); }\n", "#[test]\n#[ignore]\nfn r() { assert_eq!(1, 1); }\n"),
        }
        for path, (base, head) in cases.items():
            got = run({path: base}, {path: head})
            self.assertEqual([f["kind"] for f in got], ["skip_marker_added"], (path, got))
            self.assertEqual(got[0]["path"], path)
            self.assertGreaterEqual(got[0]["line"], 1)

    def test_a_marker_already_in_the_base_and_a_comment_are_not_new(self):
        base = "@pytest.mark.skip\ndef test_a():\n    assert 1\n"
        self.assertEqual(run({"tests/test_a.py": base}, {"tests/test_a.py": base + "# pytest.mark.skip in a comment\n"}), [])

    def test_removed_test_per_language(self):
        cases = {
            "web/a.test.ts": ("it('a', () => { expect(1).toBe(1) })\nit('b', () => { expect(2).toBe(2) })\n",
                              "it('a', () => { expect(1).toBe(1) })\n", "test b is gone"),
            "pkg/a_test.go": ("func TestA(t *testing.T) {\n\tt.Fatal()\n}\nfunc TestB(t *testing.T) {\n\tt.Fatal()\n}\n",
                              "func TestA(t *testing.T) {\n\tt.Fatal()\n}\n", "test TestB is gone"),
            "src/test/java/ATest.java": ("class ATest {\n  @Test\n  void a() { assertEquals(1, 1); }\n  @Test\n  void b() { assertEquals(2, 2); }\n}\n",
                                         "class ATest {\n  @Test\n  void a() { assertEquals(1, 1); }\n}\n", "test b is gone"),
            "src/lib.rs": ("#[test]\nfn r() { assert_eq!(1, 1); }\n#[test]\nfn s() { assert_eq!(2, 2); }\n",
                           "#[test]\nfn r() { assert_eq!(1, 1); }\n", "test s is gone"),
        }
        for path, (base, head, want) in cases.items():
            got = run({path: base}, {path: head})
            self.assertEqual([f["kind"] for f in got], ["removed_test"], (path, got))
            self.assertIn(want, got[0]["detail"])
        # a Go subtest and a duplicate name each count by name
        got = run({"pkg/a_test.go": 'func TestA(t *testing.T) {\n\tt.Run("one", func(t *testing.T) { t.Fatal() })\n\tt.Run("two", func(t *testing.T) { t.Error() })\n}\n'},
                  {"pkg/a_test.go": 'func TestA(t *testing.T) {\n\tt.Run("one", func(t *testing.T) { t.Fatal() })\n}\n'})
        self.assertIn("TestA/two", got[0]["detail"])

    def test_config_deselect(self):
        cases = [
            ("pytest.ini", "[pytest]\naddopts = -q\n", "[pytest]\naddopts = -q --deselect tests/test_slow.py::test_x\n"),
            ("pyproject.toml", '[tool.pytest.ini_options]\naddopts = "-q"\n', '[tool.pytest.ini_options]\naddopts = "-q -k not_flaky"\n'),
            ("pyproject.toml", '[tool.pytest.ini_options]\naddopts = "-q"\n', '[tool.pytest.ini_options]\naddopts = "-q -p no:cacheprovider"\n'),
            ("tests/conftest.py", "import pytest\n", "import pytest\ncollect_ignore = ['test_old.py']\n"),
            ("jest.config.js", "module.exports = {}\n", "module.exports = { testPathIgnorePatterns: ['/old/'] }\n"),
            ("vitest.config.ts", "export default {}\n", "export default { test: { passWithNoTests: true } }\n"),
            ("package.json", '{"jest": {}}\n', '{"jest": {"testPathIgnorePatterns": ["x"]}}\n'),
            ("pom.xml", "<project/>\n", "<project>\n<skipTests>true</skipTests>\n</project>\n"),
        ]
        for path, base, head in cases:
            got = run({path: base}, {path: head})
            self.assertEqual([f["kind"] for f in got], ["config_deselect"], (path, got))
        self.assertEqual(run({"pytest.ini": "[pytest]\naddopts = -q\n"}, {"pytest.ini": "[pytest]\naddopts = -q\n# --deselect x\n"}), [])

    def test_coverage_threshold_lowered_or_removed(self):
        cases = [
            ("pyproject.toml", "[tool.coverage.report]\nfail_under = 90\n", "[tool.coverage.report]\nfail_under = 70\n", "fail_under 90 -> 70"),
            ("tox.ini", "[pytest]\naddopts = --cov-fail-under=85\n", "[pytest]\naddopts = --cov-fail-under=50\n", "cov-fail-under 85 -> 50"),
            ("jest.config.js", "module.exports = { coverageThreshold: { global: { branches: 80, lines: 80 } } }\n",
             "module.exports = { coverageThreshold: { global: { branches: 80, lines: 60 } } }\n", "js threshold 80 -> 60"),
            (".coveragerc", "[report]\nfail_under = 80\n", "[report]\n", "threshold removed"),
            ("pom.xml", "<minimum>0.80</minimum>\n", "<minimum>0.50</minimum>\n", "jacoco minimum 0.8 -> 0.5"),
        ]
        for path, base, head, want in cases:
            got = run({path: base}, {path: head})
            self.assertEqual([f["kind"] for f in got], ["coverage_lowered"], (path, got))
            self.assertIn(want, got[0]["detail"])
        self.assertEqual(run({"pyproject.toml": "[x]\nfail_under = 80\n"}, {"pyproject.toml": "[x]\nfail_under = 90\n"}), [])

    def test_text_and_summary(self):
        got = run({"tests/test_a.py": PY_BASE, "src/a.py": "x"}, {"src/a.py": "x"})
        self.assertEqual(testguard.text(got[0]), "tests/test_a.py:1 deleted_test_file: the test file is deleted and the code it tests is not")
        self.assertEqual(testguard.summary(got), "1 fact(s): 1 deleted_test_file")


class CiTest(unittest.TestCase):
    """RV1 signals through a real git diff, then RV2, RV4, RV8 and the comment text."""
    R = load(block()).value
    TIERS = {"tier0_classes": ["docs"], "tier0_max_changed_lines": 200, "large_change_lines": 1000, "tier2_classes": ["security"]}

    def pr(self, base_files, head_files):
        r = h.Repo(base_files, install=False)
        self.addCleanup(r.cleanup)
        base = r.commit("base")
        h.git(r.dir, "checkout", "-q", "-b", "f")
        r.write(head_files)
        head = r.commit("head")
        d = gitio.Diff(r.dir, base, head)
        st = d.name_status()
        sig = rv1.signals(d, st, d.hunks(), {}, [], rv1.TEST_GLOBS, base, [])
        return d, st, sig

    def tier(self, d, st, sig):
        got, always = rv2.run(d, st, {}, self.R, {"verbs": {}}, sig, pr=None, checks=None)
        return rv4.rules_tier(got, always, {"human_confirmation": "not_required"}, {"classes": ["fix"]}, 10, self.TIERS), always

    def test_signals_and_tier_two_with_the_facts(self):
        d, st, sig = self.pr({"tests/test_a.py": PY_BASE, "src/a.py": "x=1\n"},
                             {"tests/test_a.py": PY_BASE.replace("def test_total", "@pytest.mark.skip\ndef test_total"), "src/a.py": "x=2\n"})
        self.assertEqual(len(sig["test_weakened"]), 1)
        self.assertRegex(sig["test_weakened"][0], r"^tests/test_a\.py:\d+ skip_marker_added: @pytest\.mark\.skip$")
        self.assertFalse(sig["test_only_deletion"])
        (tier, reasons), always = self.tier(d, st, sig)
        self.assertEqual(tier, 2)
        self.assertIn("test_weakened", reasons)
        self.assertTrue(any(x.startswith("test_weakened: 1 test-weakening fact(s) in the diff: tests/test_a.py:") for x in reasons), reasons)

    def test_the_old_test_only_deletion_signal_is_the_deleted_file_fact(self):
        d, st, sig = self.pr({"tests/test_a.py": PY_BASE, "src/a.py": "x=1\n"}, {"tests/test_a.py": None})
        self.assertTrue(sig["test_only_deletion"])
        self.assertEqual([x.split(" ")[1] for x in sig["test_weakened"]], ["deleted_test_file:"])
        self.assertEqual(self.tier(d, st, sig)[0][0], 2)

    def test_a_clean_test_change_stays_tier_zero_or_one(self):
        d, st, sig = self.pr({"tests/test_a.py": PY_BASE}, {"tests/test_a.py": PY_BASE + "\n\ndef test_more():\n    assert 1\n"})
        self.assertEqual(sig["test_weakened"], [])
        (tier, reasons), always = self.tier(d, st, sig)
        self.assertNotIn("test_weakened", reasons)
        self.assertLess(tier, 2)

    def test_the_list_is_capped_and_says_how_many_more(self):
        base = {f"tests/test_{i}.py": PY_BASE for i in range(120)}
        d, st, sig = self.pr(dict(base, **{"src/a.py": "x=1\n"}), {p: None for p in base})
        self.assertEqual(len(sig["test_weakened"]), rv1.TEST_WEAKENED_CAP + 1)
        self.assertTrue(sig["test_weakened"][-1].startswith("... and 20 more"))

    def test_rv8_gate_and_comment(self):
        gt = GateTest()
        r = gt.decide(tier=2, rv4_reasons=["test_weakened", "test_weakened: 2 test-weakening fact(s) in the diff: a.py:1 removed_test: x"],
                      explainer=({k: True for k in ("section", "first", "before", "after", "before_mermaid", "after_mermaid")}, []))
        self.assertEqual((r["decision"], r["auto_merge"]), ("human_required", False))
        self.assertIn("test_weakened", r["reason_codes"])
        self.assertTrue(any(x.startswith("test_weakened: 2 test-weakening fact(s)") for x in r["reasons"]))
        report = {"head_sha": "a" * 40, "rv3": {"classes": ["fix"], "method": "rules"}, "rv4": {"tier": 2, "reasons": r["reasons"]},
                  "rv1": {"signals": {"test_weakened": ["a.py:1 removed_test: test x is gone"]}}, "review_round": {"round": 1, "mode": "full"},
                  "findings": [], "rv8": r, "rv2": {"hits": [], "confirm_scope_hits": []}}
        items = post.why_items(report)
        self.assertTrue(any("test_weakened" in it["codes"] and it["text"].startswith("Tests may have been weakened") for it in items), items)
        self.assertEqual(post.verdict(report, items)[1], "needs a human review — tests weakened")
        self.assertIn("a.py:1 removed_test: test x is gone", "\n".join(post.detail_lines(report)))


if __name__ == "__main__":
    unittest.main()
