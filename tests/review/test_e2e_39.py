# agentive-unit: selftest.component
# design: spec.review_pipeline.stages[RV5].finding_lines, spec.review_pipeline.convergence.finding_identity, decisions.accepted[COMMENTUX], docs/impl/decisions-log.md §24
"""E2E-39 (end-to-end run 2026-11-01, PR #9): an AI finding named `tests/test_auth.py:44` and `:58` for a 26-line file, and the
inline comment landed on line 1. Root cause: the reviewer read the patch file (`diff_file`) with a line-numbered viewer and
named lines of that patch file; nothing checked the line against the head file. The task file now gives every changed file's
length and added ranges, the prompts say which numbering to use, and the script validates every line (and every instance)
against the head file: a patch-file line number is mapped back to the head line it shows, else the nearest changed line, else
the finding is a file-level comment, and the move is noted."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers as h  # noqa: E402
from test_pipeline import PipelineBase, body  # noqa: E402

from agentive.review import findings as fm  # noqa: E402
from agentive.review import github, post, tasks  # noqa: E402

# the shape of PR #9's first head: src/shop/auth/__init__.py (+16) then tests/test_auth.py (+26, new file)
TEST_FILE = "".join(f"line {i}\n" for i in range(1, 27))


def anchors(files):
    return fm.Anchors(files.get)


def raw(file, line, **kw):
    return dict({"file": file, "line": line, "trigger": "t", "summary": "s", "severity": "important", "bug_class": "test-gap"}, **kw)


class PlaceLineTest(unittest.TestCase):
    def test_e2e_39_patch_numbers_of_the_reproduction(self):
        """The numbers of PR #9: in the combined patch the new test file starts at patch line 29, so file line N is patch line
        34 + N: patch lines 44 and 58 are file lines 10 and 24 of a 26-line file."""
        auth = "".join(f"a{i}\n" for i in range(1, 8))
        patch = ("diff --git a/src/shop/auth/__init__.py b/src/shop/auth/__init__.py\nindex 1..2 100644\n--- a/src/shop/auth/__init__.py\n"
                 "+++ b/src/shop/auth/__init__.py\n@@ -1,7 +1,23 @@\n" + "".join(f" {x}\n" for x in auth.splitlines()) +
                 "".join(f"+n{i}\n" for i in range(16)) +
                 "diff --git a/tests/test_auth.py b/tests/test_auth.py\nnew file mode 100644\nindex 0..3\n--- /dev/null\n"
                 "+++ b/tests/test_auth.py\n@@ -0,0 +1,26 @@\n" + "".join(f"+line {i}\n" for i in range(1, 27)))
        pm = fm.patch_line_map(patch)
        self.assertEqual(pm["tests/test_auth.py"][35], 1)
        self.assertEqual((pm["tests/test_auth.py"][44], pm["tests/test_auth.py"][58]), (10, 24))
        self.assertEqual(pm["src/shop/auth/__init__.py"][6], 1)
        self.assertNotIn(5, pm["src/shop/auth/__init__.py"])           # a header line has no head line

    def test_e2e_39_line_in_range_stays(self):
        a = anchors({"t.py": TEST_FILE})
        self.assertEqual(fm.place_line(a, {"t.py": [1, 2]}, "t.py", 26), (26, False, None))
        self.assertEqual(fm.place_line(None, None, "t.py", 400), (400, False, None))                 # nothing to check against
        self.assertEqual(fm.place_line(a, None, "gone.py", 400), (400, False, None))                 # not readable at head

    def test_e2e_39_patch_line_is_read_back_then_nearest_changed_line_then_file_level(self):
        a = anchors({"t.py": TEST_FILE})
        line, fl, note = fm.place_line(a, {"t.py": list(range(1, 27))}, "t.py", 44, {"t.py": {44: 10}})
        self.assertEqual((line, fl), (10, False))
        self.assertIn("past the end of t.py (26 lines)", note)
        self.assertIn("line of the diff file", note)
        line, fl, note = fm.place_line(a, {"t.py": [3, 4, 20]}, "t.py", 44)
        self.assertEqual((line, fl), (20, False))
        self.assertIn("nearest changed line 20", note)
        line, fl, note = fm.place_line(a, {"t.py": []}, "t.py", 44)            # the diff adds nothing there
        self.assertEqual((line, fl), (1, True))
        self.assertIn("file-level comment", note)
        line, fl, _ = fm.place_line(anchors({"empty.py": ""}), {}, "empty.py", 3)
        self.assertEqual((line, fl), (1, True))

    def test_e2e_39_clean_reviewer_output_places_lines_and_instances_and_notes_it(self):
        a = anchors({"tests/test_auth.py": TEST_FILE})
        out = {"findings": [raw("tests/test_auth.py", 44, instances=[{"file": "tests/test_auth.py", "line": 58},
                                                                      {"file": "tests/test_auth.py", "line": 5}])]}
        got, _, prob = fm.clean_reviewer_output(out, "testing-reviewer", anchors=a, changed={"tests/test_auth.py": list(range(1, 27))},
                                                patch_map={"tests/test_auth.py": {44: 10, 58: 24}})
        f = got[0]
        self.assertEqual((f["line"], f["reviewer_line"]), (10, 44))
        self.assertIn("past the end of tests/test_auth.py (26 lines)", f["line_note"])
        self.assertTrue(f["line_note"].endswith("."))
        self.assertEqual(f["instances"], [{"file": "tests/test_auth.py", "line": 24}, {"file": "tests/test_auth.py", "line": 5}])
        self.assertEqual(f["anchor_key"], a.key("tests/test_auth.py", 10)[0])        # the id comes from the placed line, not `line:44`
        self.assertEqual(f["anchor_excerpt"], "line 10")
        self.assertIn("1 finding(s) named a line past the end of the file", prob)
        self.assertNotIn("file_level", f)
        # no patch map and no changed line: a file-level finding, the instance past the end goes
        got, _, _ = fm.clean_reviewer_output(out, "testing-reviewer", anchors=a, changed={})
        f = got[0]
        self.assertEqual((f["line"], f["file_level"], f["reviewer_line"]), (1, True, 44))
        self.assertEqual(f["instances"], [{"file": "tests/test_auth.py", "line": 5}])
        # a line that is inside the file is left alone and carries no note
        got, _, prob = fm.clean_reviewer_output({"findings": [raw("tests/test_auth.py", 12)]}, "testing-reviewer", anchors=a, changed={})
        self.assertEqual((got[0]["line"], "line_note" in got[0], prob), (12, False, None))

    def test_e2e_39_comment_table_and_post_show_the_move(self):
        a = anchors({"tests/test_auth.py": TEST_FILE})
        got, _, _ = fm.clean_reviewer_output({"findings": [raw("tests/test_auth.py", 44)]}, "testing-reviewer", anchors=a, changed={})
        f = got[0]
        self.assertIn("- Location: The reviewer named line 44, past the end of tests/test_auth.py (26 lines), and the diff adds no line "
                      "there; shown as a file-level comment.", post.finding_comment(f, 1))
        self.assertEqual(post.loc(f), "tests/test_auth.py")
        self.assertEqual(post.loc(dict(f, file_level=False, line=10)), "tests/test_auth.py:10")
        self.assertNotIn("- Location:", post.finding_comment(dict(f, line_note=None), 1))
        # the file-level finding is posted without a line (github.create_review_comment file_level)
        calls = []
        saved = github.api
        github.api = lambda path, method="GET", data=None, **k: calls.append((method, data)) or {}
        try:
            github.create_review_comment("o/r", 7, "c" * 40, "tests/test_auth.py", 1, "b", file_level=True)
            github.create_review_comment("o/r", 7, "c" * 40, "tests/test_auth.py", 10, "b")
        finally:
            github.api = saved
        self.assertEqual(calls[0][1].get("subject_type"), "file")
        self.assertNotIn("line", calls[0][1])
        self.assertEqual(calls[1][1]["line"], 10)

    def test_e2e_39_schema_knows_the_new_finding_fields(self):
        a = anchors({"tests/test_auth.py": TEST_FILE})
        got, _, _ = fm.clean_reviewer_output({"findings": [raw("tests/test_auth.py", 44)]}, "testing-reviewer", anchors=a, changed={})
        ref = json.loads(h.read(os.path.join(h.ROOT, "design", "schemas", "review-report.schema.json")))["$id"] + "#/$defs/finding"
        self.assertEqual(h.validate_ref(ref, got[0]), [])
        self.assertTrue(h.validate_ref(ref, dict(got[0], file_level="yes")))

    def test_e2e_39_prompts_and_task_format_say_which_numbering(self):
        fmt = tasks.OUTPUT_FORMATS["reviewer"]["findings"][0]["line"]
        self.assertIn("file at head", fmt)
        self.assertIn("never a line number of the diff file", fmt)
        rules = " ".join(tasks.FINDING_RULES)
        self.assertIn("Never use a line number of `diff_file` itself", rules)
        self.assertIn("changed_files", rules)
        for name in ("correctness-reviewer", "security-reviewer", "testing-reviewer"):
            t = h.read(os.path.join(h.SRC, "dot-agents", "agents", name + ".md"))
            self.assertIn("changed_files", t, name)
            self.assertIn("`finding_rules`", t, name)


class ReviewerTaskTest(PipelineBase):
    def test_e2e_39_pipeline_gives_the_reviewer_the_lengths_and_corrects_a_patch_line(self):
        """End to end through `agentive-review ci`: the reviewer task lists each changed file's length and added ranges; a finding
        that names a line of the patch file lands on the head line it shows and the report says so."""
        self.repo.write({"src/app.py": "import os\n\n\ndef run(x):\n    return 10 / x\n", "tests/test_more.py": TEST_FILE})
        head = self.repo.commit("code")
        seen = {}

        def responder(agent, inp):
            if agent == "testing-reviewer":
                seen.update(inp)
                patch = h.read(inp["diff_file"]).splitlines()
                n = patch.index("+line 10") + 1                              # what a numbered viewer shows for file line 10
                assert n > 26
                return {"findings": [raw("tests/test_more.py", n, instances=[{"file": "tests/test_more.py", "line": n + 14}])],
                        "open_findings": []}
            return h.default_responder()(agent, inp)
        rep, _, _ = self.ci(head, responder, body())
        cf = seen["changed_files"]
        self.assertEqual(seen["finding_rules"], tasks.FINDING_RULES)          # A-6: the rules travel in the task input
        self.assertEqual(cf["tests/test_more.py"], {"lines": 26, "added": "1-26"})
        self.assertEqual(cf["src/app.py"], {"lines": 5, "added": "4-5"})
        f = [x for x in rep["findings"] if x["file"] == "tests/test_more.py"][0]
        self.assertEqual(f["line"], 10)
        self.assertGreater(f["reviewer_line"], 26)
        self.assertEqual(f["instances"], [{"file": "tests/test_more.py", "line": 24}])
        self.assertIn("read as line 10 of tests/test_more.py", f["line_note"])


class RangesTest(unittest.TestCase):
    def test_e2e_39_line_ranges(self):
        self.assertEqual(fm.line_ranges([]), "")
        self.assertEqual(fm.line_ranges([1, 2, 3, 7, 9, 10]), "1-3, 7, 9-10")
        self.assertEqual(fm.added_lines({"a": {"added": [(3, "x"), (4, "y")], "removed": []}, "b": {"added": [], "removed": [(1, "z")]}}),
                         {"a": [3, 4]})


if __name__ == "__main__":
    unittest.main()
