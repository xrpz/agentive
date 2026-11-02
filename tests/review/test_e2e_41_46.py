# agentive-unit: selftest.component
# design: decisions.accepted[AISTEPFAIL], decisions.accepted[EXPLAIN], decisions.accepted[COMMENTUX], decisions.accepted[CIAUTH], spec.review_pipeline.reviewer_eval.ci, spec.review_pipeline.stages[RV8], docs/impl/decisions-log.md §29
"""End-to-end run 2026-11-01 (xrpz/agentive-e2e PRs #1-#5), the review-pipeline half:

E2E-42  a same-repository PR whose repository has no credential secret was described as "not run — fork PR"; the cause is
        named (fork, no_secret), in the reviewer-eval comment, in its workflow step and in the PR summary comment.
E2E-43  (a) the ai-review failure cause was only in the job log: a missing secret is its own code (no_secret, fork_pr) with the
        secrets to add; (b) a docs-only PR was blocked by explainer_missing because the failed AI step made it tier 2: that tier 2
        needs no Explainer (human_required, ai_step_failed names the cause); a tier 2 from a rule or a valid LLM raise still does.
E2E-45  the summary comment said the gate check succeeded while the gate failed: the ai-review job's first version does not
        claim the gate's conclusion, and the gate job posts the comment again from its own decision."""
import json
import os
import re
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers as h  # noqa: E402
from test_e2e_batch_e import CiRun, NEG, POS  # noqa: E402
from test_labels import Env, load_bin  # noqa: E402
from test_pipeline import REVIEW, PipelineBase, body  # noqa: E402

from agentive import ayaml  # noqa: E402
from agentive.review import evals, llmstate, post, rv4  # noqa: E402

ROOT = h.ROOT
WORKFLOWS = os.path.join(h.SRC, "dot-github", "workflows")
BOTH = ["CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY"]
SUMMARY_REF = "https://fw.local/schemas/reviewer-eval-case.schema.json#/$defs/summary"


def summary_of(cases=(), n=3, **kw):
    return evals.summarize(list(cases), {}, {}, n, "b" * 40, "c" * 40, **kw)


class E2E42NotRunCauseTest(CiRun):
    def test_e2e_42_cause_is_named_in_the_reviewer_eval_comment(self):
        validate = h.schema_validator("reviewer-eval-case")
        for cause, must, never in (("no_secret", ["no AI review credential secret", "this is not a fork PR", "not run — no AI credential secret",
                                                   "add the CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY repository secret"], ["fork PR gets"]),
                                   ("fork", ["A fork PR gets no repository secrets", "not run — fork PR"], ["add the"]),
                                   (None, ["could not run: the job had no repository secret"], ["fork PR", "add the"])):
            s = summary_of(not_run=True, not_run_cause=cause)
            self.assertEqual((s["reason"], s["adopt_suggested"], s.get("not_run_cause")), ("not_run", False, cause))
            self.assertEqual(h.validate_ref(SUMMARY_REF, s), [], cause)
            text = evals.comment(s, ["code_review.md (code_review.md)"], secrets=BOTH)
            for m in must:
                self.assertIn(m, text, (cause, m))
            for m in never:
                self.assertNotIn(m, text, (cause, m))
            self.assertEqual(ayaml.loads(re.search(r"```yaml agentive-reviewer-eval\n(.*?)```", text, re.S).group(1)), s)
            self.assertIn("eval-override", text)          # the human's way out is unchanged
        # the cause belongs to not_run only
        self.assertEqual(summary_of(no_baseline=True, not_run_cause="fork").get("not_run_cause"), None)
        bad = dict(summary_of(not_run=True, not_run_cause="fork"), reason="improved")
        self.assertNotEqual(h.validate_ref(SUMMARY_REF, bad), [])

    def test_e2e_42_workflow_step_passes_the_cause(self):
        """The `score and comment` step of the installed reviewer-eval workflow: key set -> no flag; another repository's head -> fork;
        the same repository without a key -> no_secret."""
        with open(os.path.join(WORKFLOWS, "reviewer-eval.yml"), encoding="utf-8") as f:
            text = f.read()
        run = re.search(r"- name: score and comment\n(?:.*\n)*?\s+run: \|\n((?:\s{10,}.*\n|\n)+)", text).group(1)
        script = "\n".join(ln[10:] for ln in run.splitlines())
        script = script[:script.index(".agents/bin/agentive-reviewer-eval score")] + 'printf "%s|%s" "$nr" "$nb"'
        def flags(has_key, head, repo, baseline="x"):
            env = {"PATH": os.environ["PATH"], "HAS_KEY": has_key, "HEAD_REPO": head, "GITHUB_REPOSITORY": repo, "BASELINE": baseline}
            return subprocess.run(["sh", "-c", script], env=env, capture_output=True, text=True, check=True).stdout
        self.assertEqual(flags("true", "o/r", "o/r"), "|")
        self.assertEqual(flags("false", "fork/r", "o/r"), "--not-run --not-run-cause fork|")
        self.assertEqual(flags("false", "o/r", "o/r"), "--not-run --not-run-cause no_secret|")
        self.assertEqual(flags("false", "o/r", "o/r", "none"), "--not-run --not-run-cause no_secret|--no-baseline")

    def test_e2e_42_summary_comment_says_the_same_as_the_reviewer_eval_comment(self):
        self.repo.write({"code_review.md": h.read(self.repo.path("code_review.md")) + "\nOne more rule.\n"})
        head = self.repo.commit("rule")
        env = {"AGENTIVE_CI_SECRET_SET": "false", "AGENTIVE_HEAD_REPO": "o/r", "GITHUB_REPOSITORY": "o/r"}
        # two cases are present, so the old line said "not scored"; the reviewer-eval comment says "not run" first
        self.repo.write({"evals/reviewer/p1.yaml": ayaml.dumps(POS), "evals/reviewer/n1.yaml": ayaml.dumps(NEG)})
        head = self.repo.commit("cases")
        rc, text = self.run_ci_post(head, env)
        line = next(ln for ln in text.splitlines() if ln.startswith("**Reviewer eval:** "))
        self.assertIn("not run (no AI credential secret in this repository)", line)
        self.assertNotIn("fork", line)
        self.assertIn("eval-override", line)
        rc, text = self.run_ci_post(head, dict(env, AGENTIVE_HEAD_REPO="fork/r"))
        self.assertIn("not run (fork PR: GitHub gives it no repository secrets)",
                      next(ln for ln in text.splitlines() if ln.startswith("**Reviewer eval:** ")))
        rc, text = self.run_ci_post(head, dict(env, AGENTIVE_CI_SECRET_SET="true"))
        self.assertNotIn("**Reviewer eval:**", text)          # a key is set and the cases are enough: the eval comment scores
        self.assertEqual(evals.summary_line(self.repo.dir, self.base, head, cause="no_secret").split(";")[0],
                         "not run (no AI credential secret in this repository)")


class E2E43AiStepFailureTest(PipelineBase):
    ENV = {"AGENTIVE_LLM_OUTCOME": "failure", "AGENTIVE_LLM_CONCLUSION": "", "GITHUB_RUN_ID": "42", "GITHUB_REPOSITORY": "o/r",
           "GITHUB_SERVER_URL": "https://github.com", "AGENTIVE_HEAD_REPO": "o/r"}

    def llm_state(self, work, **env):
        e = dict(self.ENV, **env)
        rc, out = self.repo.run(REVIEW, "llm-state", "--work", work, env=e)
        self.assertEqual(rc, 0, out)
        return out

    def test_e2e_43_a_missing_secret_is_its_own_code(self):
        work = self.repo.path(".agents/state/ns")
        out = self.llm_state(work, AGENTIVE_CI_SECRET_SET="false")
        st = llmstate.read(work)
        self.assertEqual((st["failed"], st["code"], st["secrets"]), (True, "no_secret", BOTH))
        ann = [ln for ln in out.splitlines() if ln.startswith("::error")]
        self.assertEqual(len(ann), 1, out)
        self.assertIn("ai_step_failed:no_secret", ann[0])
        self.assertIn("add the CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY repository secret", ann[0])
        self.assertNotIn("debug logging", ann[0])           # the cause is known: no hint to read the log
        self.assertIn("(run https://github.com/o/r/actions/runs/42)", ann[0])
        # a fork PR: adding a secret to the base repository does not help it
        out = self.llm_state(work, AGENTIVE_CI_SECRET_SET="false", AGENTIVE_HEAD_REPO="fork/r")
        self.assertEqual(llmstate.read(work)["code"], "fork_pr")
        self.assertNotIn("add the", out)
        # a secret is set (or the fact is unknown): the earlier classification stays
        self.llm_state(work, AGENTIVE_CI_SECRET_SET="true")
        self.assertEqual(llmstate.read(work)["code"], "no_result")
        self.llm_state(work)
        self.assertEqual(llmstate.read(work)["code"], "no_result")
        # Claude ran (an execution file with messages): a secret flag cannot hide the real reason
        ex = self.repo.path("exec.json")
        with open(ex, "w", encoding="utf-8") as f:
            json.dump([{"type": "system"}, {"type": "result", "is_error": True, "result": "Credit balance is too low"}], f)
        self.llm_state(work, AGENTIVE_CI_SECRET_SET="false", AGENTIVE_LLM_EXECUTION_FILE=ex)
        self.assertEqual(llmstate.read(work)["code"], "insufficient_credits")
        # the allowlist: both codes have a fixed sentence and a summary reason, and only known secret names are read back
        for code in ("no_secret", "fork_pr"):
            self.assertIn(code, llmstate.CODES)
            self.assertIn(code, post.AI_STEP_WHY)
        with open(os.path.join(work, "llm-state.json"), "w", encoding="utf-8") as f:
            json.dump({"tool": "claude", "outcome": "failure", "failed": True, "code": "no_secret", "secrets": ["EVIL_TOKEN", "ANTHROPIC_API_KEY"]}, f)
        self.assertEqual(llmstate.read(work)["secrets"], ["ANTHROPIC_API_KEY"])
        # codex: the one secret of that path
        self.assertEqual(llmstate.secret_names("codex", "api_key"), ["OPENAI_API_KEY"])
        self.assertEqual(llmstate.secret_names("claude", "api_key"), ["ANTHROPIC_API_KEY"])
        self.assertEqual(llmstate.secret_names("claude", "both"), BOTH)         # OAuth token first (E2E-12)

    def ci_no_tasks(self, head, work, body_text, env=None, checks=None):
        args = ["ci", "7", "--pr-json", self.pr(head, body_text), "--work", work, "--comments-json", self.comments([]), "--no-tasks"]
        rc, out = self.repo.run(REVIEW, *args, env=env)
        self.assertEqual(rc, 0, out)
        return json.loads(h.read(os.path.join(work, "review-report.json")))

    def gate(self, work, env=None):
        rc, out = self.repo.run(REVIEW, "gate", "7", "--pr-json", self.repo.path(".agents/state/pr.json"), "--work", work,
                                "--comments-json", self.repo.path(".agents/state/comments.json"), env=env)
        return rc, out, json.loads(h.read(os.path.join(work, "review-report.json")))

    def test_e2e_43_docs_pr_with_a_failed_ai_step_is_human_required_not_blocked_by_the_explainer(self):
        """The reproduction of PR #1: docs only, no Explainer (the rules say tier 0), the AI step could not run (no secret)."""
        self.repo.write({"README.md": "# r\n\nMore words.\n"})
        head = self.repo.commit("docs")
        work = self.repo.path(".agents/state/d1")
        self.llm_state_for(work)
        rep = self.ci_no_tasks(head, work, body(explainer=False))
        self.assertEqual(self.rep_schema(rep), [], json.dumps(rep["rv8"], indent=1)[:2000])
        self.assertEqual(rep["rv4"]["tier"], 2)
        self.assertEqual(rep["rv4"]["reasons"], ["tier0", "llm_raise", "llm_output_invalid"])
        self.assertTrue(rv4.ai_unavailable_only(rep["rv4"]["reasons"]))
        rc, out, g = self.gate(work, env={"GITHUB_RUN_ID": "42", "GITHUB_REPOSITORY": "o/r"})
        self.assertEqual(self.rep_schema(g), [], json.dumps(g["rv8"], indent=1)[:2000])
        rv8 = g["rv8"]
        self.assertEqual((rc, rv8["decision"], rv8["auto_merge"], rv8["check_conclusion"]), (0, "human_required", False, "success"), out)
        self.assertEqual(rv8["explainer"], {"required": False, "result": "not_required"})
        self.assertNotIn("explainer_missing", rv8["reason_codes"])
        self.assertIn("ai_step_failed", rv8["reason_codes"])
        last = out.splitlines()[-1]
        self.assertTrue(last.startswith("AGENTIVE_GATE=human_required tier=2 "), last)
        self.assertNotIn("explainer_missing", last)
        self.assertIn("NEXT=ai_step_failed:no_secret: ", last)
        self.assertIn("add the CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY repository secret", last)
        # (a) the summary comment and the gate summary carry the cause, not "ended without a result"
        extras = {"ai_step_failed": llmstate.failure_text(llmstate.read(work), "https://x/runs/42")}
        comment = post.summary_comment(g, 0, extras)
        self.assertIn("- The AI review could not run: this repository has no AI review credential secret.", comment)
        self.assertIn("**What to do:** Maintainer: add the CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY repository secret", comment)
        self.assertIn("- Error code: `ai_step_failed:no_secret`.", comment)
        self.assertNotIn("debug logging", comment)
        self.assertNotIn("PR body needs an Explainer", comment)
        self.assertNotIn("without a result", comment)

    def llm_state_for(self, work, **env):
        self.llm_state(work, AGENTIVE_CI_SECRET_SET="false", **env)

    def test_e2e_43_a_tier2_from_a_rule_or_a_valid_raise_still_needs_the_explainer(self):
        # a rule: a trust-boundary path
        self.repo.write({"src/auth/login.py": "x = 1\n"})
        head = self.repo.commit("auth")
        work = self.repo.path(".agents/state/d2")
        rep = self.ci_no_tasks(head, work, body(explainer=False))
        self.assertEqual(rep["rv4"]["tier"], 2)
        self.assertFalse(rv4.ai_unavailable_only(rep["rv4"]["reasons"]))
        rc, out, g = self.gate(work)
        self.assertEqual((rc, g["rv8"]["decision"], g["rv8"]["explainer"]["result"]), (1, "block", "explainer_missing"), out)
        # a valid LLM raise (the AI answered "a human is needed")
        h.git(self.repo.dir, "checkout", "-q", "-b", "raise", self.base)
        self.repo.write({"README.md": "# r\n\nMore words.\n"})
        head = self.repo.commit("docs")
        rep, _, g = self.ci(head, h.default_responder(raise_needed=True), body(explainer=False), work="d3")
        self.assertEqual(rep["rv4"]["tier"], 2)
        self.assertIn("llm_raise:a", rep["rv4"]["reasons"])
        self.assertFalse(rv4.ai_unavailable_only(rep["rv4"]["reasons"]))
        self.assertEqual((g["rv8"]["decision"], g["rv8"]["explainer"]["result"]), ("block", "explainer_missing"))
        # the rule helper alone
        for reasons, want in ((["tier0", "llm_raise", "llm_output_invalid"], True), (["tier1", "llm_raise", "llm_output_invalid"], True),
                              (["trust_boundary", "llm_raise", "llm_output_invalid"], False), (["tier1", "llm_raise", "llm_raise:a"], False),
                              (["tier0"], False), ([], False)):
            self.assertEqual(rv4.ai_unavailable_only(reasons), want, reasons)

    def test_e2e_43_unreadable_ai_answer_is_the_same_case(self):
        """The AI step ran but its raise-only answer was not valid: still no Explainer requirement."""
        self.repo.write({"README.md": "# r\n\nMore words.\n"})
        head = self.repo.commit("docs")
        bad = lambda agent, inp: ({"human_needed": "maybe"} if agent == "raise-only" else h.default_responder()(agent, inp))      # noqa: E731
        rep, _, g = self.ci(head, bad, body(explainer=False), work="d4")
        self.assertEqual(rep["rv4"]["reasons"], ["tier0", "llm_raise", "llm_output_invalid"])
        self.assertEqual((g["rv8"]["decision"], g["rv8"]["explainer"]["required"]), ("human_required", False))
        self.assertIn("llm_output_invalid", g["rv4"]["reasons"])


class E2E45SummaryMatchesGateTest(CiRun):
    def gate_in_process(self, head, work, checks, upserts):
        mod = load_bin()
        gh = mod.gh
        names = ("upsert_issue_comment", "patch_pr_body", "add_labels", "remove_label", "enable_auto_merge", "demotion_issues", "merged_prs",
                 "create_issue")
        saved = {k: getattr(gh, k, None) for k in names}
        gh.upsert_issue_comment = lambda slug, n, marker, text: upserts.append((marker, text))
        gh.patch_pr_body = lambda *a, **k: None
        gh.add_labels = gh.remove_label = gh.enable_auto_merge = gh.create_issue = lambda *a, **k: None
        gh.demotion_issues = lambda *a, **k: []
        gh.merged_prs = lambda *a, **k: []
        cwd = os.getcwd()
        try:
            os.chdir(self.repo.dir)
            with Env(GITHUB_STEP_SUMMARY=None, GITHUB_REPOSITORY=None, AGENTIVE_DEFAULT_BRANCH=None, AGENTIVE_HEAD_REPO=None):
                rc = mod.main(["gate", "7", "--pr-json", self.repo.path(".agents/state/pr.json"), "--work", work, "--comments-json",
                               self.repo.path(".agents/state/comments.json"), "--repo-slug", "o/r", "--checks", checks, "--apply"])
        finally:
            os.chdir(cwd)
            for k, v in saved.items():
                setattr(gh, k, v)
        return rc

    def test_e2e_45_summary_comment_follows_the_gate_decision(self):
        self.repo.write({"README.md": "# r\n\nMore words.\n"})
        head = self.repo.commit("docs")
        work = self.repo.path(".agents/state/we")
        # 1. the ai-review job (finish step): the gate has not decided yet, so the comment does not claim its conclusion
        rc, first = self.run_ci_post(head)
        self.assertEqual(rc, 0)
        self.assertNotIn("required check `agentive-review-gate`", first)
        self.assertIn("the `agentive-review-gate` job makes the final decision and updates this comment", first)
        # 2. the gate job: agentive-independence failed after that comment was written -> block; the comment is posted again
        checks = self.repo.path(".agents/state/gate-checks.json")
        with open(checks, "w", encoding="utf-8") as f:
            json.dump([{"name": "agentive-independence", "status": "fail", "source": "job"}], f)
        upserts = []
        rc = self.gate_in_process(head, work, checks, upserts)
        self.assertEqual(rc, 1)                       # the required check agentive-review-gate fails
        report = json.loads(h.read(os.path.join(work, "review-report.json")))
        self.assertEqual((report["rv8"]["decision"], report["rv8"]["check_conclusion"]), ("block", "failure"))
        self.assertEqual([m for m, _ in upserts], [post.SUMMARY_MARKER])
        final = upserts[0][1]
        self.assertIn("(required check `agentive-review-gate`: failure)", final)
        self.assertNotIn("(required check `agentive-review-gate`: success)", final)
        self.assertIn("### ⛔ agentive review: blocked", final)
        self.assertEqual(final, post.summary_comment(report, 0, {"reviewer_eval": None}))      # the gate's report is the single source
        # 3. a passing gate says success, with the same words
        checks_ok = self.repo.path(".agents/state/gate-checks-ok.json")
        with open(checks_ok, "w", encoding="utf-8") as f:
            json.dump([{"name": "agentive-independence", "status": "pass", "source": "job"}], f)
        upserts.clear()
        self.assertEqual(self.gate_in_process(head, work, checks_ok, upserts), 0)
        report = json.loads(h.read(os.path.join(work, "review-report.json")))
        self.assertIn(f"Decision `{report['rv8']['decision']}` (required check `agentive-review-gate`: success)", upserts[0][1])

    def test_e2e_45_workflow_gate_job_names_no_secret(self):
        """The gate job keeps the tool-neutral shape (tests/ci compares both tools); the Reviewer eval clause reaches it through the
        artifact written by the ai-review finish step."""
        with open(os.path.join(WORKFLOWS, "agentive-review.yml"), encoding="utf-8") as f:
            text = f.read()
        gate_job = text[text.index("\n  agentive-review-gate:"):]
        self.assertNotIn("AGENTIVE_CI_SECRET_SET", gate_job)
        self.assertIn("--apply", gate_job)
        self.assertEqual(text.count("AGENTIVE_CI_SECRET_SET"), 4)          # two comments + the llm-state and finish steps


class E2E44WriterTest(unittest.TestCase):
    def test_e2e_44_precommit_record_names_the_reviewed_tree(self):
        """`agentive-review precommit` writes `tree` (git write-tree of the staged change) next to the reviewer's reviewed_ref; the /pr
        draft copies the record only when that tree is HEAD^{tree} (tests/content/test_e2e_44_46.py)."""
        import test_pipeline as tp

        class T(tp.PrecommitTest):
            pass
        t = T("test_commit_blocked_and_confirm")
        t.setUp()
        try:
            os.makedirs(t.repo.path(".agents/state"), exist_ok=True)
            t.verify_json()
            t.repo.write({"src/app.py": "import os\n\n\ndef run():\n    return 5\n"})
            h.git(t.repo.dir, "add", "-A")
            rc, out, rec = t.run_pre(h.default_responder(), "--tool", "claude_code")
            tree = h.git(t.repo.dir, "write-tree")
            self.assertEqual(rec["tree"], tree)
            self.assertEqual(rec["reviewer"]["reviewed_ref"], tree)
            self.assertEqual(h.schema_validator("precommit-review")(rec), [])
            # the commit made from that index has the same tree: this is what binds the record to the PR content
            h.git(t.repo.dir, "commit", "-q", "-m", "reviewed")
            self.assertEqual(h.git(t.repo.dir, "rev-parse", "HEAD^{tree}"), rec["tree"])
        finally:
            t.tearDown()


if __name__ == "__main__":
    unittest.main()
