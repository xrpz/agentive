# agentive-unit: selftest.component
# design: spec.review_pipeline.convergence.finding_identity, spec.review_pipeline.severity_blocking.reply_line, docs/IMPLEMENTATION.md §6
"""FINDINGID (2026-10-29): a finding id is minted once from path, a closed bug_class and a script-computed anchor key; the
same finding is found again by same_as, equal anchor_key or an anchor moved by at most 3 lines; fixed needs proof."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers as h  # noqa: E402

from agentive.review import findings as fm  # noqa: E402
from agentive.review import gitio, tasks  # noqa: E402

SRC_V1 = "import os\n\n\ndef run(x):\n    return 10 / x\n"
HEAD1, HEAD2 = "a" * 40, "b" * 40


class Files:
    """An Anchors source over a dict of path -> text."""

    def __init__(self, files):
        self.files = files

    def anchors(self):
        return fm.Anchors(self.files.get)


def raw(line=5, file="src/app.py", **kw):
    return dict({"file": file, "line": line, "trigger": "x == 0", "summary": "division by zero", "severity": "important",
                 "bug_class": "error-handling"}, **kw)


def posted_entry(fid, ak, line=5, path="src/app.py", head=HEAD1, replies=(), **data):
    d = dict({"severity": "important", "trigger": "x == 0", "summary": "division by zero", "reviewers": ["correctness-reviewer"],
              "round": 1, "verdict": "confirmed", "verifier_session_id": "v1", "bug_class": "error-handling", "anchor_key": ak,
              "anchor_excerpt": "return 10 / x", "head_sha": head}, **data)
    return {fid: {"comment_id": 1, "path": path, "line": line, "data": d, "replies": list(replies)}}


class IdentityTest(unittest.TestCase):
    def clean(self, findings, files=None, posted=None, renames=None, **kw):
        out = {"findings": findings}
        out.update(kw)
        return fm.clean_reviewer_output(out, "correctness-reviewer", posted=posted, anchors=Files(files or {"src/app.py": SRC_V1}).anchors(),
                                        renames=renames)

    def test_bug_class_is_a_closed_enum_and_free_text_is_a_pattern(self):
        self.assertEqual(fm.BUG_CLASSES, ("logic", "error-handling", "concurrency", "data-contract", "security", "test-gap", "spec-drift", "other"))
        for given, want in (("logic", ("logic", None)), ("Error Handling", ("error-handling", None)), ("data_contract", ("data-contract", None)),
                            ("unchecked divisor", ("other", "unchecked divisor")), (None, ("other", None))):
            self.assertEqual(fm.normalize_bug_class(given), want, given)
        got, _, _ = self.clean([raw(bug_class="unchecked divisor")])
        self.assertEqual((got[0]["bug_class"], got[0]["pattern"]), ("other", "unchecked divisor"))
        # the id uses the enum value, so rewording the free text cannot change it
        a, _, _ = self.clean([raw(bug_class="unchecked divisor")])
        b, _, _ = self.clean([raw(bug_class="possible zero division")])
        self.assertEqual(a[0]["id"], b[0]["id"])

    def test_id_is_minted_from_path_class_and_anchor_and_is_stable_under_moves_and_whitespace(self):
        got, _, _ = self.clean([raw()])
        f = got[0]
        ak = fm.Anchors(Files({"src/app.py": SRC_V1}).files.get).key("src/app.py", 5)[0]
        self.assertEqual(f["anchor_key"], ak)
        self.assertRegex(ak, r"^[0-9a-f]{16}:0$")
        self.assertEqual(f["id"], fm.finding_id("src/app.py", "error-handling", ak))
        self.assertEqual(f["anchor_excerpt"], "return 10 / x")
        # the same line with other indentation and spacing, 3 lines further down: same hash (whitespace is stripped)
        moved = "import os\n\n\n# note\n# note\n# note\ndef run(x):\n\treturn   10 /   x\n"
        ak2 = fm.Anchors(Files({"src/app.py": moved}).files.get).key("src/app.py", 8)[0]
        self.assertEqual(ak2, ak)
        # a file the head tree cannot read falls back to the position (a line past the end of a readable file is moved, E2E-39)
        got, _, _ = self.clean([raw(line=99, file="gone.py")])
        self.assertEqual(got[0]["anchor_key"], "line:99")
        got, _, _ = self.clean([raw(line=99)])
        self.assertEqual((got[0]["line"], got[0]["reviewer_line"], got[0]["file_level"]), (1, 99, True))
        # only the first 100 characters count
        long1, long2 = "x" * 100 + "AAA", "x" * 100 + "BBB"
        self.assertEqual(fm.line_hash(long1), fm.line_hash(long2))
        # n counts earlier lines with the same hash
        dup = "a = 1\nb = 2\na = 1\n"
        self.assertEqual([fm.Anchors({"f": dup}.get).key("f", n)[0].split(":")[1] for n in (1, 3)], ["0", "1"])

    def test_m1_same_as_needs_the_same_path(self):
        ak = fm.Anchors({"src/app.py": SRC_V1}.get).key("src/app.py", 5)[0]
        posted = posted_entry("F-aaaaaaaaaa", ak)
        got, _, _ = self.clean([raw(same_as="F-aaaaaaaaaa", bug_class="logic")], posted=posted)
        self.assertEqual((got[0]["id"], got[0]["matched_by"]), ("F-aaaaaaaaaa", "same_as"))
        # same_as naming a finding of another file is not trusted; with another anchor too this is a new finding
        other = "def other():\n    pass\n    pass\n    pass\n    pass\n    pass\n"
        got, _, _ = self.clean([raw(file="src/other.py", line=1, same_as="F-aaaaaaaaaa")], files={"src/other.py": other}, posted=posted)
        self.assertNotEqual(got[0]["id"], "F-aaaaaaaaaa")
        self.assertNotIn("matched_by", got[0])

    def test_m2_equal_anchor_key_ignores_bug_class(self):
        ak = fm.Anchors({"src/app.py": SRC_V1}.get).key("src/app.py", 5)[0]
        posted = posted_entry("F-aaaaaaaaaa", ak)
        got, _, _ = self.clean([raw(bug_class="logic")], posted=posted)
        self.assertEqual((got[0]["id"], got[0]["matched_by"]), ("F-aaaaaaaaaa", "anchor_key"))
        # a different anchor line is a different finding
        got, _, _ = self.clean([raw(line=4)], posted=posted)
        self.assertNotEqual(got[0]["id"], "F-aaaaaaaaaa")

    def test_m3_moved_anchor_within_three_lines(self):
        old = fm.Anchors({"src/app.py": SRC_V1}.get).key("src/app.py", 5)[0]           # return 10 / x, n 0
        # lines added above; the same line now has an earlier twin (n differs) and sits 2 lines lower
        v2 = "import os\n\n\ndef run(x):\n    return 10 / x\n    y = 1\n    return 10 / x\n"
        new_ak = fm.Anchors({"src/app.py": v2}.get).key("src/app.py", 7)[0]
        self.assertNotEqual(new_ak, old)
        posted = posted_entry("F-aaaaaaaaaa", old, line=5)
        got, _, _ = self.clean([raw(line=7)], files={"src/app.py": v2}, posted=posted)
        self.assertEqual((got[0]["id"], got[0]["matched_by"]), ("F-aaaaaaaaaa", "moved_anchor"))
        # 4 lines away is not the same finding
        posted4 = posted_entry("F-aaaaaaaaaa", old, line=3)
        got, _, _ = self.clean([raw(line=7)], files={"src/app.py": v2}, posted=posted4)
        self.assertNotIn("matched_by", got[0])

    def test_renames_are_followed(self):
        repo = h.Repo({"src/old_name.py": SRC_V1 + "\n".join("# pad %d" % i for i in range(30)) + "\n"}, install=False)
        try:
            h1 = repo.commit("one")
            h.git(repo.dir, "mv", "src/old_name.py", "src/new_name.py")
            h2 = repo.commit("rename")
            diff = gitio.Diff(repo.dir, h1, h2)
            self.assertEqual(diff.rename_map(h1), {"src/old_name.py": "src/new_name.py"})
            self.assertEqual(diff.rename_map("nothex"), {})
            self.assertEqual(gitio.Diff(repo.dir, h1, None).rename_map(h1), {})
            text = diff.show_head("src/new_name.py")
            ak = fm.Anchors({"src/old_name.py": text}.get).key("src/old_name.py", 5)[0]
            posted = posted_entry("F-aaaaaaaaaa", ak, path="src/old_name.py", head=h1)
            renames = lambda p, sha: diff.rename_map(sha).get(p, p)          # noqa: E731
            got, _, _ = fm.clean_reviewer_output({"findings": [raw(file="src/new_name.py")]}, "correctness-reviewer", posted=posted,
                                                 anchors=fm.Anchors(diff.show_head), renames=renames)
            self.assertEqual((got[0]["id"], got[0]["matched_by"]), ("F-aaaaaaaaaa", "anchor_key"))
            # without the rename map the path differs and it is a new finding
            got, _, _ = fm.clean_reviewer_output({"findings": [raw(file="src/new_name.py")]}, "correctness-reviewer", posted=posted,
                                                 anchors=fm.Anchors(diff.show_head))
            self.assertNotIn("matched_by", got[0])
        finally:
            repo.cleanup()

    def test_unmentioned_finding_is_unconfirmed_not_fixed(self):
        ak = fm.Anchors({"src/app.py": SRC_V1}.get).key("src/app.py", 5)[0]
        posted = posted_entry("F-aaaaaaaaaa", ak)
        files = Files({"src/app.py": SRC_V1})
        # a new head, a reviewer output, but the anchor line is still there: not confirmed
        got = fm.carried_findings(posted, set(), {}, HEAD2, files.anchors(), None, True)
        self.assertEqual([(f["id"], f.get("unconfirmed"), "response" in f) for f in got], [("F-aaaaaaaaaa", True, False)])
        # the same head: nothing can have fixed it
        got = fm.carried_findings(posted, set(), {}, HEAD1, Files({"src/app.py": "x\n"}).anchors(), None, True)
        self.assertTrue(got[0].get("unconfirmed"))
        # no reviewer output in this round
        got = fm.carried_findings(posted, set(), {}, HEAD2, Files({"src/app.py": "x\n"}).anchors(), None, False)
        self.assertTrue(got[0].get("unconfirmed"))
        # a marker from before FINDINGID has no anchor: cannot be confirmed
        legacy = posted_entry("F-aaaaaaaaaa", None)
        legacy["F-aaaaaaaaaa"]["data"].pop("anchor_key")
        self.assertTrue(fm.carried_findings(legacy, set(), {}, HEAD2, Files({"src/app.py": "x\n"}).anchors(), None, True)[0].get("unconfirmed"))
        # all conditions hold: new head, reviewer output, nothing matches, the anchor line changed -> fixed (next_round)
        changed = "import os\n\n\ndef run(x):\n    return 10 / x if x else 0\n"
        got = fm.carried_findings(posted, set(), {}, HEAD2, Files({"src/app.py": changed}).anchors(), None, True)
        self.assertEqual((got[0]["response"], got[0].get("unconfirmed")), ({"kind": "fixed", "reason": "fixed", "source": "next_round"}, None))
        # the file is gone: the anchor is gone
        got = fm.carried_findings(posted, set(), {}, HEAD2, Files({}).anchors(), None, True)
        self.assertEqual(got[0]["response"]["source"], "next_round")
        # a reviewer that still sees it keeps it open even when the line changed; not unconfirmed either
        got = fm.carried_findings(posted, set(), {"F-aaaaaaaaaa": {"status": "still_present", "evidence": ""}}, HEAD2,
                                  Files({"src/app.py": changed}).anchors(), None, True)
        self.assertEqual(("response" in got[0], got[0].get("unconfirmed")), (False, None))
        # a finding a report matches this round is not carried at all
        self.assertEqual(fm.carried_findings(posted, {"F-aaaaaaaaaa"}, {}, HEAD2, files.anchors(), None, True), [])

    def test_fixed_from_a_reviewer_needs_evidence(self):
        ak = fm.Anchors({"src/app.py": SRC_V1}.get).key("src/app.py", 5)[0]
        posted = posted_entry("F-aaaaaaaaaa", ak)
        _, st, _ = self.clean([], posted=posted, open_findings=[{"id": "F-aaaaaaaaaa", "status": "fixed"}])
        self.assertEqual(st, {})                                                      # no evidence: no claim
        _, st, _ = self.clean([], posted=posted, open_findings=[{"id": "F-aaaaaaaaaa", "status": "fixed", "evidence": "guard added at app.py:5"}])
        self.assertEqual(st["F-aaaaaaaaaa"]["status"], "fixed")
        got = fm.carried_findings(posted, set(), st, HEAD1, Files({"src/app.py": SRC_V1}).anchors(), None, True)
        self.assertEqual((got[0]["response"]["source"], got[0]["fixed_evidence"]), ("next_round", "guard added at app.py:5"))
        # an id the task did not list is ignored
        _, st, _ = self.clean([], posted=posted, open_findings=[{"id": "F-bbbbbbbbbb", "status": "fixed", "evidence": "x"}])
        self.assertEqual(st, {})

    def test_a_fixed_reply_never_replaces_a_fix_the_review_proved(self):
        """E2E-60: carried_findings proved the fix (source next_round); the author's `fixed` reply line is a claim and must not replace it."""
        posted = posted_entry("F-aaaaaaaaaa", fm.Anchors({"src/app.py": SRC_V1}.get).key("src/app.py", 5)[0], replies=[(102, "fixed", "", "dev")])
        got = fm.carried_findings(posted, set(), {}, HEAD2, Files({"src/app.py": "import os\n\n\ndef run(x):\n    return 10 / x if x else 0\n"}).anchors(), None, True)
        self.assertEqual(got[0]["response"]["source"], "next_round")
        f = fm.attach_responses(got, posted)[0]
        self.assertEqual(f["response"], {"kind": "fixed", "reason": "fixed", "source": "next_round"})
        fm.set_blocking([f], "ci")
        self.assertFalse(f["blocking"])
        # nothing proved it: the reply stays a claim (kind open) and the finding still blocks
        unproven = fm.carried_findings(posted, set(), {}, HEAD2, Files({"src/app.py": SRC_V1}).anchors(), None, True)
        f = fm.attach_responses(unproven, posted)[0]
        self.assertEqual((f["response"]["kind"], f["response"]["source"], f.get("unconfirmed")), ("open", "reply_line", True))

    def test_answered_thread_is_inherited_and_a_fixed_claim_reopens(self):
        ak = fm.Anchors({"src/app.py": SRC_V1}.get).key("src/app.py", 5)[0]
        replies = [(101, "intentional", "callers never pass 0", "m")]
        posted = posted_entry("F-aaaaaaaaaa", ak, replies=replies)
        got, _, _ = self.clean([raw(bug_class="logic")], posted=posted)
        f = fm.attach_responses(got, posted)[0]
        self.assertEqual((f["id"], f["response"]["reason"], f["response"]["kind"]), ("F-aaaaaaaaaa", "intentional", "replied_resolved"))
        self.assertEqual(fm.to_post([f], set(posted)), [])                               # nothing is posted again
        fixed = posted_entry("F-aaaaaaaaaa", ak, replies=[(102, "fixed", "", "dev")])
        got, _, _ = self.clean([raw()], posted=fixed)
        f = fm.attach_responses(got, fixed)[0]
        self.assertEqual((f["id"], f.get("reopened"), f["response"]["kind"]), ("F-aaaaaaaaaa", True, "open"))
        self.assertEqual(fm.to_post([f], set(fixed)), [])

    def test_marker_data_roundtrip_keeps_the_issued_id_and_anchor(self):
        got, _, _ = self.clean([raw()])
        f = got[0]
        f.update({"verification": {"verdict": "confirmed", "verifier_session_id": "v"}, "reviewers": ["correctness-reviewer"]})
        data = fm.finding_data(f, 1, HEAD1)
        self.assertEqual((data["anchor_key"], data["anchor_excerpt"], data["head_sha"], data["bug_class"]),
                         (f["anchor_key"], "return 10 / x", HEAD1, "error-handling"))
        body = "x\n<!-- agentive-finding: %s -->\n<!-- agentive-finding-data: %s -->" % (f["id"], json.dumps(data))
        posted = fm.posted_findings([{"id": 5, "body": body, "path": "src/app.py", "line": 5}])
        self.assertEqual(posted[f["id"]]["data"]["anchor_key"], f["anchor_key"])
        # never recomputed: a later round with another bug class still finds the same id through the stored anchor
        got2, _, _ = self.clean([raw(bug_class="security")], posted=posted)
        self.assertEqual(got2[0]["id"], f["id"])

    def test_reviewer_output_schema_matches_the_code(self):
        schema = json.loads(h.read(os.path.join(h.ROOT, "design", "schemas", "reviewer-output.schema.json")))
        self.assertEqual(tuple(schema["$defs"]["bug_class"]["enum"]), fm.BUG_CLASSES)
        rep = json.loads(h.read(os.path.join(h.ROOT, "design", "schemas", "review-report.schema.json")))
        self.assertEqual(tuple(rep["$defs"]["finding"]["properties"]["bug_class"]["enum"]), fm.BUG_CLASSES)
        v = h.schema_validator("reviewer-output")
        ok = {"findings": [dict(raw(), pattern="unchecked divisor", same_as=None)],
              "open_findings": [{"id": "F-0123456789", "status": "still_present"},
                                {"id": "F-0123456789", "status": "fixed", "evidence": "guard added"}]}
        self.assertEqual(v(ok), [])
        self.assertTrue(v({"findings": [dict(raw(), bug_class="unchecked divisor")]}))                       # free text is not a bug_class
        self.assertTrue(v({"findings": [], "open_findings": [{"id": "F-0123456789", "status": "fixed"}]}))   # fixed needs evidence
        self.assertTrue(v({"findings": [dict(raw(), same_as="a-hash-the-model-made-up")]}))
        text = json.dumps(tasks.OUTPUT_FORMATS["reviewer"])
        for c in fm.BUG_CLASSES:
            self.assertIn(c, text)
        self.assertIn("evidence", json.dumps(tasks.OUTPUT_FORMATS["reviewer"]["open_findings"]))

    def test_reviewer_prompts_name_the_rules(self):
        # the finding rules are written once (tasks.FINDING_RULES) and travel in every reviewer task as `finding_rules`;
        # the three reviewer prompts point to them instead of repeating them
        rules = " ".join(tasks.FINDING_RULES)
        fmt = tasks.OUTPUT_FORMATS["reviewer"]["findings"][0]["bug_class"]
        for c in fm.BUG_CLASSES:
            self.assertIn(c, fmt)
        self.assertIn("`pattern`", rules)
        self.assertIn("evidence", rules)
        self.assertIn("NOT treated as fixed", rules)
        for name in ("correctness-reviewer", "security-reviewer", "testing-reviewer"):
            t = h.read(os.path.join(h.SRC, "dot-agents", "agents", name + ".md"))
            self.assertIn("`finding_rules`", t, name)
            self.assertNotIn("spec-drift", t, name)          # the bug_class list is not repeated in the prompt
            self.assertNotIn('"bug_class": "..."', t)
        t = h.read(os.path.join(h.SRC, "dot-agents", "agents", "correctness-reviewer.md"))
        self.assertIn("anchor_excerpt", t)


if __name__ == "__main__":
    unittest.main()
