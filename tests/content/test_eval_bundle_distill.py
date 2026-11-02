# agentive-unit: selftest.component
# design: decisions.accepted[EVALBUNDLE], spec.review_pipeline.reviewer_eval.intake, spec.maintenance.distill, contracts[reviewer-eval-case]
"""EVALBUNDLE (user 2026-11-01): /distill drafts a reviewer eval case with a location (eval-case.sh), dedups it, and writes none
for a revert alone."""
import os
import sys
import unittest

from helpers import ROOT, SKILLS, Sandbox, git, read, schema_errors, write

sys.path.insert(0, os.path.join(ROOT, "src", "repo-layer", "dot-agents", "lib"))
from agentive import ayaml  # noqa: E402
from agentive.review import evals  # noqa: E402

BUG = ["def get(session):", "    uid = session.user_id", "    if uid:", "        return uid", "    return None"]


def lines(n=30):
    return [f"line{i}" for i in range(1, n + 1)]


class EvalCaseDraft(unittest.TestCase):
    """History: base (30 lines) -> orig (adds BUG at lines 10-14) -> moved (3 lines inserted on top) -> fix (changes the bug line)."""

    @classmethod
    def setUpClass(cls):
        cls.sb = Sandbox()
        r = cls.sb.repo
        L = lines()
        write(r, "src/session.py", "\n".join(L) + "\nlast\n")
        git(r, "add", "-A")
        git(r, "commit", "-q", "-m", "base")
        cls.base = git(r, "rev-parse", "HEAD")
        L[9:14] = BUG
        write(r, "src/session.py", "\n".join(L) + "\nlast\n")
        write(r, "src/other.py", "a = 1\nb = 2\n")
        git(r, "add", "-A")
        git(r, "commit", "-q", "-m", "orig")
        cls.orig = git(r, "rev-parse", "HEAD")
        write(r, "src/session.py", "top1\ntop2\ntop3\n" + "\n".join(L) + "\nlast\n")          # an unrelated change above: lines move by 3
        git(r, "add", "-A")
        git(r, "commit", "-q", "-m", "moved")
        cls.moved = git(r, "rev-parse", "HEAD")
        M = read(r, "src/session.py").splitlines()
        M[13] = "    uid = session.user_id if session else None"                                  # the bug line, now line 14
        M[25] = "line23-fixed"                                                                   # a second hunk, outside the added lines
        write(r, "src/session.py", "\n".join(M) + "\n")
        git(r, "add", "-A")
        git(r, "commit", "-q", "-m", "fix")
        cls.fix = git(r, "rev-parse", "HEAD")

    @classmethod
    def tearDownClass(cls):
        cls.sb.close()

    def setUp(self):
        self.cases = os.path.join(self.sb.repo, "evals", "reviewer")
        if os.path.isdir(self.cases):
            for f in os.listdir(self.cases):
                os.remove(os.path.join(self.cases, f))

    def draft(self, *args):
        p = self.sb.run(["sh", self.sb.script("distill", "eval-case.sh"), "draft", *args])
        return p.returncode, p.stdout

    def positive(self, cid, *extra, pr="#412", fix=True):
        args = ["positive", "--id", cid, "--pr", pr, "--base-sha", self.base, "--head-sha", self.orig,
                "--origin", "fix_pr", "--origin-ref", "#431", "--created", "2026-11-02"]
        if fix:
            args += ["--fix-base", self.moved, "--fix-head", self.fix]
        return self.draft(*args, *extra)

    def case(self, cid):
        text = read(self.cases, cid + ".yaml")
        return text, ayaml.loads(text)

    def test_eval_bundle_distill_positive_draft_uses_the_narrowest_fix_hunk_on_added_lines(self):
        rc, out = self.positive("login-null-session", "--bug-class", "error-handling", "--note", "session may be None")
        self.assertEqual(rc, 0, out)
        # the fix changed line 14 of `moved` (= line 11 of orig, after the 3 inserted lines are mapped back) and line 26 (outside the PR)
        self.assertIn("EVALCASE=written id=login-null-session path=evals/reviewer/login-null-session.yaml label=positive "
                      "range=src/session.py:11-11; NEXT=", out)
        self.assertIn("EVALCASE_BODY=- `evals/reviewer/login-null-session.yaml` (positive, from #412): `src/session.py:11-11`", out)
        text, doc = self.case("login-null-session")
        self.assertTrue(text.startswith("# agentive-check: eval-412 ("))
        self.assertEqual(doc["expected_findings"], [{"file": "src/session.py", "line_from": 11, "line_to": 11}])
        self.assertEqual((doc["base_sha"], doc["head_sha"], doc["label"], doc["drafted_by"], doc["created"]),
                         (self.base, self.orig, "positive", "distill", "2026-11-02"))
        self.assertEqual((doc["bug_class"], doc["note"], doc["origin"]), ("error-handling", "session may be None", {"kind": "fix_pr", "ref": "#431"}))
        self.assertEqual(doc["source"], {"range_basis": "proposed"})
        self.assertEqual(evals.validate_case(doc, "login-null-session"), [])
        self.assertEqual(schema_errors("reviewer-eval-case", doc), [])
        # the bug line is where the case says it is on head_sha
        self.assertEqual(git(self.sb.repo, "show", f"{self.orig}:src/session.py").splitlines()[10], BUG[1])

    def test_eval_bundle_distill_a_given_location_and_revert_origin(self):
        rc, out = self.draft("positive", "--id", "given", "--pr", "#9", "--base-sha", self.base, "--head-sha", self.orig, "--origin", "revert",
                             "--origin-ref", "#10", "--range", "src/session.py:10-12", "--slack", "0", "--created", "2026-11-02")
        self.assertEqual(rc, 0, out)
        _, doc = self.case("given")
        self.assertEqual(doc["expected_findings"], [{"file": "src/session.py", "line_from": 10, "line_to": 12, "slack": 0}])
        self.assertEqual(doc["origin"]["kind"], "revert")
        self.assertEqual(schema_errors("reviewer-eval-case", doc), [])

    def test_eval_bundle_distill_revert_only_gets_no_case_and_a_note(self):
        rc, out = self.positive("reverted", fix=False)
        self.assertEqual(rc, 0, out)
        self.assertIn("EVALCASE=skipped id=reverted reason=no_fix_location; NEXT=", out)
        self.assertIn("eval case not written: revert only, no fix location", out)
        self.assertFalse(os.path.exists(os.path.join(self.cases, "reverted.yaml")))
        # a fix that touches no line the PR added also gives no location
        rc, out = self.draft("positive", "--id", "elsewhere", "--pr", "#5", "--base-sha", self.base, "--head-sha", self.orig, "--origin", "fix_pr",
                             "--origin-ref", "#6", "--fix-base", self.base, "--fix-head", self.base)
        self.assertIn("reason=no_fix_location", out)
        self.assertFalse(os.path.exists(os.path.join(self.cases, "elsewhere.yaml")))

    def test_eval_bundle_distill_dedup(self):
        self.assertIn("EVALCASE=written", self.positive("first")[1])
        # the same original PR (the marker): a second case is not written
        rc, out = self.positive("second")
        self.assertIn("EVALCASE=duplicate id=second existing=first reason=it carries the marker agentive-check: eval-412", out)
        self.assertFalse(os.path.exists(os.path.join(self.cases, "second.yaml")))
        # the same id
        self.assertIn("existing=first reason=a case file with this id exists", self.positive("first", pr="#413")[1])
        # a case a human wrote (no marker) on the same head with an overlapping range
        write(self.sb.repo, "evals/reviewer/by-hand.yaml", f"id: by-hand\npr: \"#77\"\nhead_sha: {self.orig}\nbase_sha: {self.base}\nlabel: positive\n"
              "origin: {kind: revert, ref: \"#78\"}\nexpected_findings:\n  - {file: src/session.py, line_from: 11, line_to: 13}\n")
        os.remove(os.path.join(self.cases, "first.yaml"))
        rc, out = self.positive("third", pr="#500")
        self.assertIn("EVALCASE=duplicate id=third existing=by-hand reason=same PR and an overlapping range", out)
        # another range on the same head is not a duplicate
        rc, out = self.draft("positive", "--id", "other-range", "--pr", "#500", "--base-sha", self.base, "--head-sha", self.orig, "--origin", "revert",
                             "--origin-ref", "#10", "--range", "src/other.py:1-2")
        self.assertIn("EVALCASE=written id=other-range", out)

    def test_eval_bundle_distill_negative_draft(self):
        rc, out = self.draft("negative", "--id", "retry-noise", "--pr", "#398", "--base-sha", self.base, "--head-sha", self.orig, "--forbid",
                             "src/session.py:12", "--reply", "wrong", "--finding-id", "F-1a2b3c4d5e", "--bug-class", "logic", "--created", "2026-11-02")
        self.assertEqual(rc, 0, out)
        self.assertIn("EVALCASE=written id=retry-noise path=evals/reviewer/retry-noise.yaml label=negative range=src/session.py:12-12", out)
        text, doc = self.case("retry-noise")
        self.assertEqual(doc["forbidden_findings"], [{"file": "src/session.py", "line_from": 12, "line_to": 12}])
        self.assertEqual(doc["source"], {"finding_id": "F-1a2b3c4d5e", "reply": "wrong", "range_basis": "proposed"})
        self.assertNotIn("expected_findings", doc)
        self.assertNotIn("slack", text)
        self.assertEqual(evals.validate_case(doc, "retry-noise"), [])
        self.assertEqual(schema_errors("reviewer-eval-case", doc), [])
        # the same finding again
        rc, out = self.draft("negative", "--id", "retry-noise-2", "--pr", "#399", "--base-sha", self.base, "--head-sha", self.orig, "--forbid",
                             "src/other.py:1", "--reply", "intentional", "--finding-id", "F-1a2b3c4d5e")
        self.assertIn("existing=retry-noise reason=it is for the same finding F-1a2b3c4d5e", out)
        # not_important raises the bar in code_review.md instead of becoming a case
        rc, out = self.draft("negative", "--id", "ni", "--pr", "#400", "--base-sha", self.base, "--head-sha", self.orig, "--forbid",
                             "src/other.py:1", "--reply", "not_important")
        self.assertIn("EVALCASE=skipped id=ni reason=reply_not_forbidden", out)

    def test_eval_bundle_distill_stops_on_bad_arguments(self):
        rc, out = self.draft("positive", "--id", "Bad_ID", "--pr", "#1", "--base-sha", self.base, "--head-sha", self.orig, "--origin", "revert",
                             "--origin-ref", "#2", "--range", "src/session.py:1-2")
        self.assertEqual(rc, 2)
        self.assertIn("EVALCASE=stop reason=bad_id", out)
        rc, out = self.draft("positive", "--id", "far", "--pr", "#1", "--base-sha", self.base, "--head-sha", self.orig, "--origin", "revert",
                             "--origin-ref", "#2", "--range", "src/session.py:900-910")
        self.assertEqual(rc, 2)
        self.assertIn("reason=range_outside_file", out)
        rc, out = self.draft("positive", "--id", "nofile", "--pr", "#1", "--base-sha", self.base, "--head-sha", self.orig, "--origin", "revert",
                             "--origin-ref", "#2", "--range", "src/nope.py:1-2")
        self.assertIn("reason=range_file_missing", out)
        rc, out = self.draft("positive", "--id", "sha", "--pr", "#1", "--base-sha", "abc", "--head-sha", self.orig, "--origin", "revert",
                             "--origin-ref", "#2", "--range", "src/session.py:1-2")
        self.assertIn("reason=bad_base_sha", out)
        rc, out = self.draft("positive", "--id", "sha", "--pr", "#1", "--base-sha", "0" * 40, "--head-sha", self.orig, "--origin", "revert",
                             "--origin-ref", "#2", "--range", "src/session.py:1-2")
        self.assertIn("reason=missing_base_sha", out)
        self.assertEqual(os.listdir(self.cases) if os.path.isdir(self.cases) else [], [])

    def test_eval_bundle_distill_warns_about_wide_and_unadded_ranges(self):
        rc, out = self.draft("positive", "--id", "wide", "--pr", "#7", "--base-sha", self.base, "--head-sha", self.orig, "--origin", "revert",
                             "--origin-ref", "#8", "--range", "src/session.py:1-30")
        self.assertEqual(rc, 0, out)
        self.assertIn("EVALCASE_WARN=range_wide src/session.py:1-30 (30 lines;", out)
        self.assertNotIn("range_not_in_added_lines", out)                                  # it overlaps the added lines
        os.remove(os.path.join(self.cases, "wide.yaml"))                                  # (else 1-30 would cover 20-21: a duplicate)
        rc, out = self.draft("positive", "--id", "unadded", "--pr", "#17", "--base-sha", self.base, "--head-sha", self.orig, "--origin", "revert",
                             "--origin-ref", "#8", "--range", "src/session.py:20-21")
        self.assertIn("EVALCASE_WARN=range_not_in_added_lines src/session.py:20-21", out)


class DistillSkillText(unittest.TestCase):
    def test_eval_bundle_skill_describes_the_procedure(self):
        text = read(SKILLS, "distill", "SKILL.md")
        self.assertLessEqual(len(text.splitlines()), 500)
        for needle in ("scripts/eval-case.sh draft positive", "scripts/eval-case.sh", "draft negative", "EVALCASE=skipped reason=no_fix_location",
                       "revert only, no fix location", "EVALCASE_BODY=", "## Eval cases", "section 3a", "never write `confirmed` yourself",
                       "narrowest hunk of the fix", "A `not_important` answer gets no case"):
            self.assertIn(needle, text, needle)
        self.assertNotIn("Also draft a negative reviewer eval case `evals/reviewer/<id>.yaml` (label negative", text)
        self.assertTrue(os.access(os.path.join(SKILLS, "distill", "scripts", "eval-case.sh"), os.X_OK))

    def test_eval_bundle_readme_documents_the_fields_and_the_rules(self):
        text = read(ROOT, "src", "repo-layer", "evals", "reviewer", "README.md")
        for needle in ("base_sha", "head_sha", "bug_class", "range_basis", "drafted_by", "slack", "[line_from - slack, line_to + slack]",
                       "Reference only: fewer than 10 positive cases", "wrong place", "a default chosen to absorb"):
            self.assertIn(needle, text, needle)


if __name__ == "__main__":
    unittest.main()
