# agentive-unit: selftest.component
# design: decisions.accepted[EVALOVERRIDE], decisions.accepted[NOBASELINE], decisions.accepted[WFCHANGE], spec.review_pipeline.reviewer_eval.scoring, spec.review_pipeline.reviewer_eval.ci, spec.work_loop.ci_ai_review_floor.workflow_change, docs/impl/decisions-log.md §21
"""End-to-end recheck (2026-10-31), wording and display only: E2E-25 a reviewer change with too few eval cases says what the
eval-override line is (no check reads it) and what the human does, also in the PR summary comment; E2E-26 the skipped AI
review of a workflow-change PR is shown once, with its reason, in the summary comment and the gate lines."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers as h  # noqa: E402
from test_labels import Env, load_bin  # noqa: E402
from test_pipeline import REVIEW, PipelineBase, body  # noqa: E402

from agentive import ayaml  # noqa: E402
from agentive.review import evals, post  # noqa: E402

POS = {"id": "p1", "pr": "#1", "label": "positive", "origin": {"kind": "revert", "ref": "#2"},
       "expected_findings": [{"file": "a.py", "line_from": 10, "line_to": 12}]}
NEG = {"id": "n1", "pr": "#3", "label": "negative"}
WF = ".github/workflows/agentive-review.yml"
OVERRIDE = ("adopt_suggested: false — merge only with `eval-override: <reason>` in the PR body (a rule for the human who merges: "
            "no check reads the line, and the gate does not wait for it).")


class CiRun(PipelineBase):
    def run_ci_post(self, head, env=None):
        """`agentive-review ci --no-tasks --post` in process with gh stubbed; returns (rc, the summary comment body)."""
        mod = load_bin()
        posted = {}
        gh = mod.gh
        saved = {k: getattr(gh, k) for k in ("upsert_issue_comment", "create_review_comment", "remove_label")}
        gh.upsert_issue_comment = lambda slug, n, marker, text: posted.__setitem__(marker, text)
        gh.create_review_comment = lambda *a, **k: None
        gh.remove_label = lambda *a, **k: None
        cwd = os.getcwd()
        try:
            os.chdir(self.repo.dir)
            with Env(**(env or {})):
                rc = mod.main(["ci", "7", "--pr-json", self.pr(head, body(explainer=True)), "--work", self.repo.path(".agents/state/we"),
                               "--comments-json", self.comments([]), "--repo-slug", "o/r", "--no-tasks", "--post"])
        finally:
            os.chdir(cwd)
            for k, v in saved.items():
                setattr(gh, k, v)
        return rc, posted.get(post.SUMMARY_MARKER, "")


class E2E25ReviewerEvalTest(CiRun):
    def test_e2e_25_no_case_comment_says_what_the_human_does_and_that_no_check_reads_the_line(self):
        s = evals.summarize([], {}, {}, 3, "b" * 40, "c" * 40)
        self.assertEqual((s["reason"], s["adopt_suggested"]), ("insufficient_cases", False))
        text = evals.comment(s, ["code_review.md (code_review.md)"])
        # COMMENTUX: Result says why, What to do says what the human does and that no check reads the line
        self.assertIn("**Result:** Not scored: `evals/reviewer/` has no case yet, so nothing was compared.", text)
        self.assertIn("**What to do:** Review this reviewer change by hand. To merge it, add one line `eval-override: <reason>` "
                      "to the PR body (for example `eval-override: no eval cases yet; reviewed by hand`). No check reads that line; it "
                      "records why for later readers.", text)
        self.assertIn("**To get a score next time:** add one positive and one negative case under evals/reviewer/ "
                      "(evals/reviewer/README.md).", text)
        self.assertIn(OVERRIDE, text.split("<summary>Machine-read result")[1])          # the adopt line sits in the folded section
        self.assertEqual(ayaml.loads(h_block(text)), s)
        # one side only: the other wording, the same What to do and override line
        only_pos = evals.summarize([POS], {"p1": [[]] * 3}, {"p1": [[]] * 3}, 3, "b", "c")
        text = evals.comment(only_pos, ["code_review.md"])
        self.assertIn("needs a positive and a negative case, so the result is not compared.", text)
        self.assertIn("**What to do:** Review this reviewer change by hand.", text)
        self.assertIn(OVERRIDE, text)

    def test_e2e_25_the_gate_does_not_read_eval_override(self):
        """What the text claims: nothing outside the reviewer-eval comment mentions the line (the gate, RV8, the PR-body tools)."""
        lib = os.path.join(h.LIB, "agentive")
        hits = []
        for root, _, files in os.walk(lib):
            for f in files:
                if f.endswith(".py") and "eval-override" in h.read(os.path.join(root, f)):
                    hits.append(os.path.relpath(os.path.join(root, f), lib))
        self.assertEqual(sorted(hits), [os.path.join("review", "evals.py")])
        for b in ("agentive-review",):
            self.assertNotIn("eval-override", h.read(os.path.join(h.SRC, "dot-agents", "bin", b)))

    def test_e2e_25_summary_line_only_for_a_scorable_reviewer_change_with_too_few_cases(self):
        self.repo.write({"src/app.py": "import os\n\n\ndef run():\n    return 2\n"})
        plain = self.repo.commit("code")
        self.assertIsNone(evals.summary_line(self.repo.dir, self.base, plain))              # not a reviewer change
        self.repo.write({"code_review.md": h.read(self.repo.path("code_review.md")) + "\nOne more rule.\n"})
        head = self.repo.commit("rule")
        line = evals.summary_line(self.repo.dir, self.base, head)
        self.assertEqual(line, "not scored (evals/reviewer/ has no case yet); merging needs `eval-override: <reason>` in the PR body; "
                               "details in the reviewer-eval comment.")
        self.repo.write({"evals/reviewer/p1.yaml": ayaml.dumps(POS)})
        self.assertIn("evals/reviewer/ needs a positive and a negative case", evals.summary_line(self.repo.dir, self.base, head))
        self.repo.write({"evals/reviewer/n1.yaml": ayaml.dumps(NEG)})
        self.assertIsNone(evals.summary_line(self.repo.dir, self.base, head))               # scorable: the eval comment decides
        # no baseline (E2E-16 first install): that comment needs no eval-override, so no line here
        bare = h.Repo({"code_review.md": "# r\n", "README.md": "# r\n"}, install=False)
        self.addCleanup(bare.cleanup)
        b0 = bare.commit("base")
        bare.write({"code_review.md": "# r\n\nrule\n"})
        self.assertIsNone(evals.summary_line(bare.dir, b0, bare.commit("rule")))

    def test_e2e_25_summary_comment_repeats_the_line(self):
        self.repo.write({"code_review.md": h.read(self.repo.path("code_review.md")) + "\nOne more rule.\n"})
        head = self.repo.commit("rule")
        rc, text = self.run_ci_post(head)
        self.assertEqual(rc, 0)
        lines = [ln for ln in text.splitlines() if ln.startswith("**Reviewer eval:** ")]     # COMMENTUX: one clause
        self.assertEqual(len(lines), 1, text)
        self.assertIn("`eval-override: <reason>`", lines[0])
        self.assertIn("details in the reviewer-eval comment", lines[0])         # E2E-28: the long parts stay in that comment
        # a PR that does not change the reviewer gets no such line
        h.git(self.repo.dir, "checkout", "-q", "-b", "docs-only", self.base)
        self.repo.write({"README.md": "# r\n\nwords\n"})
        rc, text = self.run_ci_post(self.repo.commit("docs"))
        self.assertNotIn("reviewer eval", text)


def h_block(text):
    from agentive.review import util
    return util.fenced_blocks(text, "yaml agentive-reviewer-eval")[0]


class E2E26SkippedReviewTest(CiRun):
    ENV = {"AGENTIVE_DEFAULT_BRANCH": "main", "GITHUB_REPOSITORY": "o/r", "AGENTIVE_HEAD_REPO": "o/r", "GITHUB_SHA": None}
    SENTENCE = "workflow-change PR: AI review skipped by the action; human review."

    def workflow_pr(self):
        h.git(self.repo.dir, "update-ref", "refs/remotes/origin/main", self.base)
        self.repo.write({WF: "name: agentive-review\n"})
        return self.repo.commit("add the review workflow")

    def test_e2e_26_skipped_view_replaces_the_not_run_lines(self):
        skip = {"kind": "workflow_change", "detail": "workflow-change PR: AI review skipped by the action; human review. why"}
        reasons = ["tier2", "trust_boundary", "llm_raise", "llm_raise: rv5-correctness-reviewer: not run",
                   "llm_raise", "llm_raise: rv5-security-reviewer: no output"]
        probs = ["rv5-correctness-reviewer: not run", "rv5-security-reviewer: no output"]
        out, notes = post.skipped_view(reasons, probs, skip, ["tier2"])
        self.assertEqual(post.reason_lines(out), ["tier2", "trust_boundary", "ai_review_skipped: workflow_change: " + skip["detail"]])
        self.assertEqual(notes, [])
        # RV4 raised llm_raise itself: the bare code stays; another problem stays a reason
        out, _ = post.skipped_view(reasons + ["llm_raise: rv6-coordinator-c1: output invalid"], probs, skip, ["llm_raise"])
        self.assertIn("llm_raise: rv6-coordinator-c1: output invalid", post.reason_lines(out))
        # no skip: nothing changes
        self.assertEqual(post.skipped_view(reasons, probs, None), (reasons, probs))

    def test_e2e_26_summary_comment_shows_the_skip_once_with_its_reason(self):
        head = self.workflow_pr()
        rc, text = self.run_ci_post(head, self.ENV)
        self.assertEqual(rc, 0)
        self.assertEqual(text.count(self.SENTENCE), 1, text)               # the detail once, in Details
        visible = text.split("<details>")[0]                                 # COMMENTUX: a plain Why line, no code
        self.assertIn("- It adds the CI workflow `.github/workflows/agentive-review.yml`.", visible)
        self.assertIn("### \u26a0\ufe0f agentive review: needs a human review — install PR", visible)
        self.assertNotIn("ai_review_skipped", visible)
        self.assertIn("- AI review skipped (`workflow_change`): " + self.SENTENCE, text)
        self.assertNotIn(": not run", text)
        self.assertNotIn("llm_raise", text)
        self.assertNotIn("- Note: rv5-", text)
        # without the facts (no default branch in the env) the skip is unknown: each problem is still shown once
        rc, text = self.run_ci_post(head, dict(self.ENV, AGENTIVE_DEFAULT_BRANCH=None))
        self.assertNotIn("ai_review_skipped", text)
        lines = [ln for ln in text.splitlines() if "rv5-correctness-reviewer: not run" in ln]
        self.assertEqual(len(lines), 1, text)

    def test_e2e_26_gate_lines_show_the_skip_once(self):
        head = self.workflow_pr()
        work = self.repo.path(".agents/state/wg")
        rc, out = self.repo.run(REVIEW, "gate", "7", "--pr-json", self.pr(head, body(explainer=True)), "--work", work,
                                "--comments-json", self.comments([]), env={k: v for k, v in dict(self.ENV, GITHUB_SHA=head).items()})
        self.assertEqual(rc, 0, out)
        rep = json.loads(h.read(os.path.join(work, "review-report.json")))
        self.assertIn("ai_review_skipped", rep["rv8"]["reason_codes"])       # the record is unchanged: display only
        self.assertTrue(any(r.startswith("llm_raise: ") for r in rep["rv8"]["reasons"]))
        gate_lines = out.split("agentive-review-gate:", 1)[1].split("AGENTIVE_GATE=", 1)[0]
        self.assertNotIn("rv5-", gate_lines)
        self.assertNotIn("llm_raise", gate_lines)
        self.assertEqual(gate_lines.count(self.SENTENCE), 1, gate_lines)

    def test_e2e_26_missing_merge_commit_is_fetched_or_fail_safe(self):
        from agentive.review import llmstate
        head = self.workflow_pr()
        self.assertEqual(llmstate.workflow_skip(self.repo.dir, "claude", None, False, "main", "main", self.base, head)["kind"],
                         "workflow_change")
        # a sha this clone does not have and cannot fetch (no origin remote): None, the earlier behaviour
        self.assertIsNone(llmstate.workflow_skip(self.repo.dir, "claude", None, False, "main", "main", self.base, "f" * 40))


if __name__ == "__main__":
    unittest.main()
