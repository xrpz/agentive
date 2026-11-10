# agentive-unit: selftest.component
# design: spec.review_pipeline.profiles, spec.review_pipeline.gate_protection, spec.review_pipeline.convergence, spec.spec_stage.chain.ci_check, spec.work_loop.precommit_review, docs/IMPLEMENTATION.md §6
"""End-to-end runs of .agents/bin/agentive-review on throwaway git repos (no network: PR context and review
comments come from JSON files, the LLM side of the task protocol is a stand-in). Every report and record the
script writes is validated against its design schema."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers as h  # noqa: E402

from agentive import ayaml  # noqa: E402
from agentive.review import findings as fm  # noqa: E402
from agentive.review import post  # noqa: E402

REVIEW = ".agents/bin/agentive-review"
RULES = os.path.join(h.SRC, "code_review.md.regions", "rules.md")
EXPLAINER = ("## Explainer\n\nChanges the flow (src/app.py:1).\n\n### Before\n\n```mermaid\nflowchart LR\n a-->b\n```\n\n"
             "### After\n\n```mermaid\nflowchart LR\n a-->c\n```\n\n")
TAIL = "## Verification\n\n- test -> 0\n\n## Risk\n\nlow\n\n## How to revert\n\ngit revert\n"


def body(explainer=False, u2=None, extra=None):
    blk = {"implementer": {"tool": "claude_code", "model": "m", "session_id": "impl-1", "started_at": "2026-10-28T00:00:00Z"},
           "reviews": [{"role": "reviewer", "tool": "claude_code", "model": "m", "session_id": "pre-1", "context": "unverified",
                        "executed_by": "unknown", "attestation": "self_reported", "source": "precommit-review", "verdict": "pass", "reviewer_id": "correctness-reviewer"}]}
    if u2:
        blk["u2"] = u2
    blk.update(extra or {})
    return (EXPLAINER if explainer else "") + TAIL + "\n```yaml agentive-review\n" + ayaml.dumps({"agentive-review": blk}) + "```\n"


class PipelineBase(unittest.TestCase):
    def setUp(self):
        rules = ("# Review criteria\n\nUser text.\n\n<!-- AGENTIVE START rules -->\n" + h.rules_region_text() + "<!-- AGENTIVE END rules -->\n"
                 if os.path.exists(RULES) else "# Review criteria\n")
        self.repo = h.Repo({"code_review.md": rules, "README.md": "# r\n",
                            "src/app.py": "import os\n\n\ndef run():\n    return 1\n", "tests/test_app.py": "def test_run():\n    assert 1\n"})
        self.base = self.repo.commit("base", date="2026-01-01T00:00:00Z")
        h.git(self.repo.dir, "checkout", "-q", "-b", "feat")
        self.rep_schema = h.schema_validator("review-report")

    def tearDown(self):
        self.repo.cleanup()

    def pr(self, head, body_text="", author="dev", assoc="OWNER", labels=()):
        p = {"number": 7, "title": "t", "body": body_text, "author": author, "author_association": assoc, "is_fork": False,
             "labels": list(labels), "base_sha": self.base, "head_sha": head, "base_ref": "main"}
        path = self.repo.path(".agents/state/pr.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(p, f)
        return path

    def comments(self, items):
        path = self.repo.path(".agents/state/comments.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(items, f)
        return path

    def ci(self, head, responder, body_text="", work="w", comments=(), gate=True, **pr_kw):
        args = [REVIEW, "ci", "7", "--pr-json", self.pr(head, body_text, **pr_kw), "--work", self.repo.path(f".agents/state/{work}"),
                "--comments-json", self.comments(list(comments))]
        rc, out, agents = h.drive(self.repo, args, responder)
        self.assertEqual(rc, 0, out)
        rep = json.loads(h.read(self.repo.path(f".agents/state/{work}/review-report.json")))
        self.assertEqual(self.rep_schema(rep), [], json.dumps(rep, indent=1)[:3000])
        g = None
        if gate:
            grc, gout = self.repo.run(REVIEW, "gate", "7", "--pr-json", self.repo.path(".agents/state/pr.json"),
                                      "--work", self.repo.path(f".agents/state/{work}"), "--comments-json", self.repo.path(".agents/state/comments.json"))
            g = json.loads(h.read(self.repo.path(f".agents/state/{work}/review-report.json")))
            self.assertEqual(self.rep_schema(g), [])
            self.assertEqual(g["rv8"]["decision"], rep["rv8"]["decision"], gout)
            self.assertEqual(grc, 1 if g["rv8"]["decision"] == "block" else 0, gout)
        return rep, agents, g


class CiProfileTest(PipelineBase):
    def test_tier0_docs_pr_auto_merges(self):
        self.repo.write({"README.md": "# r\n\nMore words.\n"})
        head = self.repo.commit("docs")
        rep, agents, _ = self.ci(head, h.default_responder(), body())
        self.assertEqual(rep["rv3"], {"classes": ["docs"], "method": "deterministic"})
        self.assertEqual(rep["rv4"]["tier"], 0)
        self.assertEqual(agents, ["raise-only", "correctness-reviewer", "security-reviewer"])   # no testing-reviewer, no RV7/RV6
        self.assertEqual((rep["rv8"]["decision"], rep["rv8"]["auto_merge"]), ("pass", True))
        self.assertEqual(rep["stages_run"], ["RV1", "RV2", "RV3", "RV4", "RV7b", "RV5", "RV8"])

    def test_code_pr_tier1_shadow_then_block_then_disputed(self):
        self.repo.write({"src/app.py": "import os\n\n\ndef run(x):\n    return 10 / x\n"})
        head = self.repo.commit("code")
        rep, agents, _ = self.ci(head, h.default_responder(), body())
        self.assertEqual(rep["rv4"]["tier"], 1)
        self.assertEqual(rep["rv7b"]["reason"], "computed")
        self.assertEqual((rep["rv8"]["decision"], rep["rv8"].get("would_auto_merge")), ("human_required", True))
        self.assertEqual(rep["rv8"]["auto_merge_setting"], "low_risk")      # DEFAULTS: no decisions.yaml, the framework default applies
        self.assertIn("classifier", agents)
        bug = {"correctness-reviewer": [{"file": "src/app.py", "line": 5, "trigger": "x == 0", "summary": "division by zero",
                                         "severity": "important", "bug_class": "error-handling", "pattern": "unchecked divisor"}]}
        rep, agents, _ = self.ci(head, h.default_responder(bug), body(), work="w2")
        f = rep["findings"][0]
        self.assertEqual((f["blocking"], f["verification"]["verdict"], rep["rv8"]["decision"]), (True, "confirmed", "block"))
        self.assertIn("verifier", agents)
        root = {"id": 100, "body": "x\n<!-- agentive-finding: %s -->" % f["id"], "path": "src/app.py", "line": 5}
        reply = {"id": 101, "in_reply_to_id": 100, "author_association": "MEMBER",
                 "body": "agentive-reply: %s intentional callers never pass 0 (see src/app.py:1)" % f["id"]}
        rep, _, _ = self.ci(head, h.default_responder(bug), body(), work="w3", comments=[root, reply])
        self.assertEqual(rep["rv8"]["decision"], "human_required")
        self.assertIn("finding_disputed", rep["rv8"]["reasons"])

    def test_ci_records_name_the_ci_tool(self):
        """CITOOL: reviews recorded by the ci job name the effective CI tool (framework.init_tool, overridden by review.pipeline.ci_tool)."""
        def tools(work):
            rep = json.loads(h.read(self.repo.path(f".agents/state/{work}/review-report.json")))
            reviews = json.loads(h.read(self.repo.path(f".agents/state/{work}/reviews.json")))
            return {r["tool"] for r in rep["rv5"]["reviewers"]}, {r["tool"] for r in reviews}
        for n, (files, want) in enumerate([
                ({}, "claude_code"),
                ({".agents/manifest.yaml": "schema_version: 1\nframework: {plugin: agentive, version: 0.1.0, init_tool: codex}\n"}, "codex"),
                ({".agents/manifest.yaml": "schema_version: 1\nframework: {plugin: agentive, version: 0.1.0, init_tool: codex}\n",
                  ".agents/framework.yaml": "review:\n  pipeline:\n    ci_tool: claude\n"}, "claude_code"),
                ({".agents/framework.yaml": "review:\n  pipeline:\n    ci_tool: codex\n"}, "codex")]):
            h.git(self.repo.dir, "checkout", "-q", "main")
            self.repo.write(dict({".agents/manifest.yaml": None, ".agents/framework.yaml": None}, **files))
            self.base = self.repo.commit(f"base{n}", date="2026-01-02T00:00:00Z")
            h.git(self.repo.dir, "checkout", "-q", "-b", f"feat{n}")
            self.repo.write({"src/app.py": "import os\n\n\ndef run(x):\n    return x + %d\n" % n})
            head = self.repo.commit("code")
            self.ci(head, h.default_responder(), body(), work=f"w{n}", gate=False)
            self.assertEqual(tools(f"w{n}"), ({want}, {want}), files)

    def test_ci_reviews_are_honest_ci_records_with_run_info(self):
        """INDEPCI: reviews.json holds source ci records (run id, attempt, sha); ci-run.json carries the action session id for the same sha."""
        self.repo.write({"src/app.py": "import os\n\n\ndef run(x):\n    return x + 5\n"})
        head = self.repo.commit("code")
        env = {"GITHUB_RUN_ID": "4242", "GITHUB_RUN_ATTEMPT": "2", "AGENTIVE_ACTION_SESSION_ID": "sess-77", "GITHUB_ACTIONS": "true"}
        old = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        try:
            self.ci(head, h.default_responder(), body(), gate=False)
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        reviews = json.loads(h.read(self.repo.path(".agents/state/w/reviews.json")))
        info = json.loads(h.read(self.repo.path(".agents/state/w/ci-run.json")))
        self.assertTrue(reviews)
        for r in reviews:
            self.assertEqual((r["source"], r["attestation"], r["executed_by"], r["context"]), ("ci", "ci_job", "process", "isolated"))
            self.assertEqual(r["run"], {"run_id": "4242", "run_attempt": "2", "sha": head})
            self.assertEqual(r["session_id"], "sess-77")
            self.assertNotIn("fresh", json.dumps(r))
        self.assertEqual(info["run"]["sha"], head)
        self.assertEqual(info["session_id"], "sess-77")
        rc, out = self.repo.run(REVIEW, "independence", "7", "--pr-json", self.repo.path(".agents/state/pr.json"), "--work", self.repo.path(".agents/state/w"))
        self.assertEqual(rc, 0, out)

    def test_bot_pr_is_exempt_tier1_and_never_auto_merged(self):
        """BOTPR: a docs-only (tier 0) bot PR is raised to tier 1 and goes to a human with bot_pr_exempt."""
        self.repo.write({"README.md": "# r\n\nbump\n"})
        head = self.repo.commit("bump")
        for kw in ({"author": "dependabot[bot]"},):
            rep, _, g = self.ci(head, h.default_responder(), "bumps x from 1 to 2", work="wb", **kw)
            for r in (rep, g):
                self.assertGreaterEqual(r["rv4"]["tier"], 1)
                self.assertEqual((r["rv8"]["decision"], r["rv8"]["auto_merge"]), ("human_required", False))
                self.assertIn("bot_pr_exempt", r["rv8"]["reasons"])
                self.assertIn("bot_pr_exempt", r["rv4"]["reasons"])

    def test_human_pr_without_block_is_exempt_tier1(self):
        self.repo.write({"README.md": "# r\n\nhuman edit\n"})
        head = self.repo.commit("human")
        rep, _, g = self.ci(head, h.default_responder(), "A plain human PR description.", work="wh")
        for r in (rep, g):
            self.assertGreaterEqual(r["rv4"]["tier"], 1)
            self.assertEqual((r["rv8"]["decision"], r["rv8"]["auto_merge"]), ("human_required", False))
            self.assertIn("human_pr_exempt", r["rv8"]["reasons"])

    def test_agent_pr_with_block_is_not_exempt(self):
        self.repo.write({"README.md": "# r\n\nagent edit\n"})
        head = self.repo.commit("agent")
        rep, _, _ = self.ci(head, h.default_responder(), body())
        self.assertEqual((rep["rv4"]["tier"], rep["rv8"]["decision"]), (0, "pass"))
        self.assertNotIn("human_pr_exempt", rep["rv8"]["reasons"])

    def test_verifier_gets_execution_results_not_a_command_runner_in_ci(self):
        """CISPLIT: in CI the verifier payload says read_results (the outer-verbs results); locally it runs commands."""
        self.repo.write({"src/app.py": "import os\n\n\ndef run(x):\n    return 10 / x\n"})
        head = self.repo.commit("code")
        bug = {"correctness-reviewer": [{"file": "src/app.py", "line": 5, "trigger": "x == 0", "summary": "division by zero",
                                         "severity": "important", "bug_class": "error-handling", "pattern": "unchecked divisor"}]}
        seen = {}
        base = h.default_responder(bug)

        def responder(agent, inp):
            if agent == "verifier":
                seen["execution"] = inp.get("execution")
            return base(agent, inp)
        outer = self.repo.path(".agents/state/outer.json")
        os.makedirs(os.path.dirname(outer), exist_ok=True)
        with open(outer, "w", encoding="utf-8") as f:
            json.dump([{"name": "test", "status": "fail", "log_tail": "AssertionError: x"}], f)
        args = [REVIEW, "ci", "7", "--pr-json", self.pr(head, body()), "--work", self.repo.path(".agents/state/we"),
                "--comments-json", self.comments([]), "--outer-results", outer]
        rc, out, _ = h.drive(self.repo, args, responder)
        self.assertEqual(rc, 0, out)
        ex = seen["execution"]
        self.assertEqual(ex["mode"], "read_results")
        self.assertEqual(ex["results"][0]["name"], "test")
        self.assertIn("AssertionError", ex["results"][0]["log_tail"])

    def test_refuted_finding_is_not_blocking(self):
        self.repo.write({"src/app.py": "import os\n\n\ndef run(x):\n    return x + 1\n"})
        head = self.repo.commit("code")
        bug = {"security-reviewer": [{"file": "src/app.py", "line": 5, "trigger": "t", "summary": "s", "severity": "important"}]}
        rep, _, _ = self.ci(head, h.default_responder(bug, verdict="refuted"), body())
        self.assertFalse(rep["findings"][0]["blocking"])
        self.assertNotEqual(rep["rv8"]["decision"], "block")

    def test_ci_change_without_confirmation_is_tier2_and_needs_explainer(self):
        self.repo.write({".github/workflows/x.yml": "name: x\n"})
        head = self.repo.commit("ci")
        rep, agents, _ = self.ci(head, h.default_responder(), "")
        self.assertEqual(rep["u2"], {"run": "unknown", "human_confirmation": "not_obtained", "action": "pr_only_tier2", "source": "pr_body_missing"})
        self.assertEqual(rep["rv4"]["tier"], 2)
        self.assertNotIn("raise-only", agents)                       # rules already gave tier 2
        self.assertEqual(rep["rv8"]["decision"], "block")
        self.assertIn("explainer_missing", rep["rv8"]["reasons"])
        u2 = {"run": "interactive", "human_confirmation": "obtained", "confirm_scope_hits": [".github/workflows/x.yml"]}
        rep, _, _ = self.ci(head, h.default_responder(), body(explainer=True, u2=u2), work="w2")
        self.assertEqual(rep["u2"]["action"], "commit")
        self.assertEqual((rep["rv4"]["tier"], rep["rv8"]["decision"]), (2, "human_required"))   # ci_config glob still tier 2

    def test_policy_comes_from_the_base_commit(self):
        self.repo.write({".agents/framework.yaml": "review:\n  auto_merge: off\n", "code_review.md": "# loosened\n",
                         "README.md": "# r\n\nx\n"})
        head = self.repo.commit("loosen")
        rep, _, _ = self.ci(head, h.default_responder(), body(explainer=True))
        self.assertEqual(rep["rv8"]["auto_merge_setting"], "low_risk")      # review.auto_merge: off in the PR is not read (policy comes from the base)
        self.assertEqual(rep["rv4"]["tier"], 2)
        self.assertTrue(any(x["reason"] == "check_config" for x in rep["rv2"]["hits"]))

    def test_chain_order(self):
        self.repo.write({"docs/plans/feat-x/spec.md": "Intent: docs/plans/feat-x/intent.md\n"})
        head = self.repo.commit("spec")
        rep, _, _ = self.ci(head, h.default_responder(), body(explainer=True))
        self.assertEqual(rep["rv1"]["chain"]["problems"], ["intent_not_on_base"])
        self.assertIn("chain_approval", rep["rv4"]["reasons"])
        self.assertEqual(rep["rv8"]["decision"], "block")
        self.assertIn("chain_order", rep["rv8"]["reasons"])
        self.assertIn("/chain-spec feat-x", rep["rv1"]["chain"]["next_action"])

    def round_two(self, reviewer_answer, v2_src="import os\n\n\ndef run(x):\n    return 10 / x if x else 0\n", comments_extra=(), work="w"):
        """A finding posted at h1 (anchor line `return 10 / x`); round 2 on a new head h2 with the given reviewer answer."""
        self.repo.write({"src/app.py": "import os\n\n\ndef run(x):\n    return 10 / x\n"})
        h1 = self.repo.commit("r1")
        self.repo.write({"src/app.py": v2_src})
        h2 = self.repo.commit("r2")
        prev = {"review_round": 1, "pipeline": {"classification": {"classes": ["fix"], "method": "llm"}, "tier": {"value": 1, "reasons": []},
                                                "verified_findings": [], "risk_score": {"score": None, "reason": "not_tier1"},
                                                "gate": {"decision": "block", "check": "agentive-review-gate", "auto_merge": False, "head_sha": h1}}}
        data = json.dumps({"severity": "important", "trigger": "x == 0", "summary": "division by zero", "reviewers": ["correctness-reviewer"],
                           "round": 1, "verdict": "confirmed", "verifier_session_id": "v1", "bug_class": "error-handling",
                           "anchor_key": fm.line_hash("    return 10 / x") + ":0", "anchor_excerpt": "return 10 / x", "head_sha": h1})
        root = {"id": 200, "body": "x\n<!-- agentive-finding: F-0123456789 -->\n<!-- agentive-finding-data: %s -->" % data, "path": "src/app.py", "line": 5}
        seen = {}

        def responder(agent, inp):
            if agent == "correctness-reviewer":
                seen["input"] = inp
                seen["patch"] = h.read(inp["diff_file"])
                return reviewer_answer
            return h.default_responder()(agent, inp)
        rep, _, _ = self.ci(h2, responder, body(extra=prev), comments=[root] + list(comments_extra), work=work)
        return rep, seen, h1, h2

    def test_round_two_marks_fixed_findings(self):
        """FINDINGID: fixed needs a new head, a reviewer output and the anchor line gone or changed (or the reviewer's own fixed with evidence)."""
        rep, seen, h1, _ = self.round_two({"findings": [], "open_findings": []})
        inp = seen["input"]
        self.assertEqual((inp["review_mode"], inp["diff_range"]["base"]), ("incremental", h1))
        self.assertEqual(inp["open_findings"][0]["id"], "F-0123456789")
        self.assertEqual(inp["open_findings"][0]["anchor_excerpt"], "return 10 / x")               # previous findings verbatim
        self.assertEqual(inp["open_findings"][0]["trigger"], "x == 0")
        self.assertIn("if x else 0", seen["patch"])
        self.assertNotIn("-def run():", seen["patch"])                                              # only the diff since the last reviewed head
        self.assertEqual(rep["review_round"], {"round": 2, "mode": "incremental", "since_sha": h1})
        f = rep["findings"][0]
        # the reviewer did not mention it, but the anchor line changed on the new head: fixed
        self.assertEqual((f["id"], f["response"]["source"], f["blocking"]), ("F-0123456789", "next_round", False))

    def test_unmentioned_finding_with_its_anchor_line_unchanged_stays_open_unconfirmed(self):
        rep, _, _, _ = self.round_two({"findings": [], "open_findings": []},
                                      v2_src="import os\n\n\ndef run(x):\n    return 10 / x\n\n\ndef other():\n    return 2\n")
        f = rep["findings"][0]
        self.assertEqual((f["id"], f.get("unconfirmed"), "response" in f, f["blocking"]), ("F-0123456789", True, False, True))
        self.assertEqual(rep["rv8"]["decision"], "block")                                            # it still blocks: not mentioned is NOT fixed

    def test_reviewer_fixed_claim_needs_evidence(self):
        same = "import os\n\n\ndef run(x):\n    return 10 / x\n\n\ndef other():\n    return 2\n"
        rep, _, _, _ = self.round_two({"findings": [], "open_findings": [{"id": "F-0123456789", "status": "fixed"}]}, v2_src=same)
        self.assertTrue(rep["findings"][0].get("unconfirmed"))
        rep, _, _, _ = self.round_two({"findings": [], "open_findings": [{"id": "F-0123456789", "status": "fixed", "evidence": "guarded in caller.py:9"}]},
                                      v2_src=same, work="w2")
        f = rep["findings"][0]
        self.assertEqual((f["response"]["source"], f["fixed_evidence"], f["blocking"]), ("next_round", "guarded in caller.py:9", False))

    SAME = "import os\n\n\ndef run(x):\n    return 10 / x\n\n\ndef other():\n    return 2\n"       # the anchor line is unchanged

    @staticmethod
    def fixed_reply(assoc="OWNER"):
        return {"id": 201, "in_reply_to_id": 200, "author_association": assoc, "user": {"login": "dev"}, "created_at": "2026-11-02T00:00:01Z",
                "body": "agentive-reply: F-0123456789 fixed guarded x == 0 in run()"}

    def test_fixed_reply_after_a_fix_that_changed_the_anchor_line_unblocks_the_gate(self):
        """E2E-60 (PR #6 of the end-to-end repo): the author pushed the fix and replied `fixed`; round 2 proved the fix (new head, a
        reviewer output, the anchor line changed). The reply line used to replace that proof, so the gate still said unanswered_finding."""
        rep, _, _, _ = self.round_two({"findings": [], "open_findings": []}, comments_extra=[self.fixed_reply()])
        f = rep["findings"][0]
        self.assertEqual((f["id"], f["response"], f["blocking"]), ("F-0123456789", {"kind": "fixed", "reason": "fixed", "source": "next_round"}, False))
        self.assertNotIn("unanswered_finding", rep["rv8"]["reasons"])
        self.assertNotEqual(rep["rv8"]["decision"], "block")
        self.assertEqual(self.rep_schema(rep), [])
        text = post.summary_comment(rep, 0, {})
        self.assertIn("| F-0123456789 | `src/app.py:5` | confirmed | fixed (next_round) |", text)

    def test_fixed_reply_with_the_reviewers_evidence_unblocks_a_fix_made_elsewhere(self):
        """E2E-60 (PR #8): a test-gap is fixed by a new test, the flagged line stays; the reviewer's evidence proves it, the reply must not undo it."""
        ans = {"findings": [], "open_findings": [{"id": "F-0123456789", "status": "fixed", "evidence": "test_boundary covers 7 and 8 characters"}]}
        rep, _, _, _ = self.round_two(ans, v2_src=self.SAME, comments_extra=[self.fixed_reply()])
        f = rep["findings"][0]
        self.assertEqual((f["response"]["source"], f["fixed_evidence"], f["blocking"]), ("next_round", "test_boundary covers 7 and 8 characters", False))
        self.assertNotEqual(rep["rv8"]["decision"], "block")

    def test_fixed_reply_alone_is_a_claim_and_the_comment_says_so(self):
        """The design keeps a `fixed` reply a claim: the flagged line is unchanged and no reviewer confirmed it, so the gate still blocks,
        and the table and the reason say the same thing (E2E-60: the table said `fixed (reply_line)`, the reason `no fix or answer yet`)."""
        rep, _, _, _ = self.round_two({"findings": [], "open_findings": []}, v2_src=self.SAME, comments_extra=[self.fixed_reply()])
        f = rep["findings"][0]
        self.assertEqual((f["response"]["reason"], f["response"]["source"], f["blocking"], f.get("unconfirmed")), ("fixed", "reply_line", True, True))
        self.assertEqual((rep["rv8"]["decision"], "unanswered_finding" in rep["rv8"]["reason_codes"]), ("block", True))
        text = post.summary_comment(rep, 0, {})
        self.assertIn("| F-0123456789 | `src/app.py:5` | confirmed | fixed claimed, not confirmed (reply_line) |", text)
        self.assertIn('the author replied "fixed", but the review has not confirmed it', text)
        self.assertIn("blocked — the fix for 1 AI finding is not confirmed", text)
        self.assertIn("agentive:full-review", text.split("<details>")[0])
        self.assertNotIn("no fix or answer yet", text)

    def test_fixed_reply_of_an_untrusted_author_changes_nothing(self):
        rep, _, _, _ = self.round_two({"findings": [], "open_findings": []}, v2_src=self.SAME, comments_extra=[self.fixed_reply("NONE")])
        f = rep["findings"][0]
        self.assertEqual(("response" in f, f["blocking"], rep["rv8"]["decision"]), (False, True, "block"))

    def test_rereported_finding_keeps_its_issued_id_and_is_not_posted_again(self):
        same = "import os\n\n\ndef run(x):\n    return 10 / x\n\n\ndef other():\n    return 2\n"
        again = {"findings": [{"file": "src/app.py", "line": 5, "trigger": "x == 0", "summary": "reworded summary", "severity": "important",
                               "bug_class": "logic", "pattern": "zero division"}], "open_findings": []}
        rep, _, _, _ = self.round_two(again, v2_src=same)
        ids = [f["id"] for f in rep["findings"]]
        self.assertEqual(ids, ["F-0123456789"])                                                      # M2: same anchor, another bug_class
        self.assertEqual(rep["findings"][0]["matched_by"], "anchor_key")
        from agentive.review import findings as fmod
        self.assertEqual(fmod.to_post(rep["findings"], {"F-0123456789"}), [])

    def test_gate_without_llm_outputs_is_fail_safe(self):
        self.repo.write({"README.md": "# r\n\nfork change\n"})
        head = self.repo.commit("docs")
        pr = self.pr(head, body(explainer=True), assoc="CONTRIBUTOR")
        rc, out = self.repo.run(REVIEW, "gate", "7", "--pr-json", pr, "--work", self.repo.path(".agents/state/empty"),
                                "--comments-json", self.comments([]))
        rep = json.loads(h.read(self.repo.path(".agents/state/empty/review-report.json")))
        self.assertEqual(self.rep_schema(rep), [])
        self.assertEqual((rc, rep["rv4"]["tier"], rep["rv8"]["decision"]), (0, 2, "human_required"), out)
        self.assertIn("AGENTIVE_GATE=human_required", out.splitlines()[-1])

    def test_e2e_07_gate_without_the_ai_review_report_says_so_and_never_merges(self):
        # the ai-review job failed (no artifact agentive-review-<sha>): the gate still decides, fail-safe, with a readable reason
        self.repo.write({"README.md": "# r\n\nMore words.\n"})
        head = self.repo.commit("docs")
        pr = self.pr(head, body(explainer=True))           # without it the fail-safe tier 2 blocks (explainer_missing): also no merge
        rc, out = self.repo.run(REVIEW, "gate", "7", "--pr-json", pr, "--work", self.repo.path(".agents/state/none"),
                                "--comments-json", self.comments([]))
        rep = json.loads(h.read(self.repo.path(".agents/state/none/review-report.json")))
        self.assertEqual(self.rep_schema(rep), [])
        self.assertEqual((rc, rep["rv8"]["decision"], rep["rv8"]["auto_merge"]), (0, "human_required", False), out)
        self.assertTrue(any(r.startswith("ai_review_missing: ") for r in rep["rv8"]["reasons"]), rep["rv8"]["reasons"])
        last = out.splitlines()[-1]
        self.assertIn("AGENTIVE_GATE=human_required", last)
        self.assertIn("ai_review_missing", last.split("reasons=", 1)[1].split(";", 1)[0])
        self.assertIn("NEXT=open the failed ai-review job log", last)
        # with the ai-review report present (the normal case) the reason is absent
        _, _, g = self.ci(head, h.default_responder(), body(explainer=True), work="ok")
        self.assertFalse(any(r.startswith("ai_review_missing") for r in g["rv8"]["reasons"]))

    def ci_reviews_file(self, head, work="none", sha=None):
        d = self.repo.path(f".agents/state/{work}")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "reviews.json"), "w", encoding="utf-8") as f:
            json.dump([{"role": "reviewer", "tool": "claude_code", "source": "ci", "executed_by": "process", "attestation": "ci_job",
                        "context": "isolated", "run": {"run_id": "9", "run_attempt": "1", "sha": sha or head}}], f)
        return d

    def test_independence_cli(self):
        self.repo.write({"README.md": "# r\n\nmore\n"})
        head = self.repo.commit("docs")
        work = self.ci_reviews_file(head)
        rc, out = self.repo.run(REVIEW, "independence", "7", "--pr-json", self.pr(head, body()), "--work", work)
        self.assertEqual(rc, 0, out)
        self.assertIn("attested locally", out)
        rc, out = self.repo.run(REVIEW, "independence", "7", "--pr-json", self.pr(head, "## Verification\n\n```yaml agentive-review\nagentive-review: {}\n```\n"), "--work", work)
        self.assertEqual(rc, 1, out)
        self.assertIn("AGENTIVE_INDEPENDENCE=fail", out)
        # INDEPCI: no CI-produced review for this head -> fail even though the body carries a local record
        rc, out = self.repo.run(REVIEW, "independence", "7", "--pr-json", self.pr(head, body()), "--work", self.repo.path(".agents/state/none2"))
        self.assertEqual(rc, 1, out)
        self.assertIn("not counted", out)
        work2 = self.ci_reviews_file(head, "stale", sha="f" * 40)
        rc, out = self.repo.run(REVIEW, "independence", "7", "--pr-json", self.pr(head, body()), "--work", work2)
        self.assertEqual(rc, 1, out)

    def test_independence_cli_exempt(self):
        self.repo.write({"README.md": "# r\n\nmore\n"})
        head = self.repo.commit("docs")
        work = self.repo.path(".agents/state/none")
        rc, out = self.repo.run(REVIEW, "independence", "7", "--pr-json", self.pr(head, "plain body", author="dependabot[bot]"), "--work", work)
        self.assertEqual(rc, 0, out)
        self.assertIn("AGENTIVE_INDEPENDENCE=exempt reason=bot_pr_exempt", out)
        rc, out = self.repo.run(REVIEW, "independence", "7", "--pr-json", self.pr(head, "plain body"), "--work", work)
        self.assertEqual(rc, 0, out)
        self.assertIn("reason=human_pr_exempt", out)

    def test_checks_helper(self):
        out_path = self.repo.path(".agents/state/checks.json")
        rc, out = self.repo.run(REVIEW, "checks", "--out", out_path, "--outer", '[{"name":"test","status":"pass"},{"name":"lint","status":"skipped"}]',
                                "--job", "dependency-review=", "--job", "agentive-independence=failure")
        self.assertEqual(rc, 0, out)
        self.assertEqual(json.loads(h.read(out_path)), [{"name": "test", "status": "pass"}, {"name": "lint", "status": "skipped"},
                                                         {"name": "agentive-independence", "status": "fail"}])

    def test_checks_must_run_counts_a_skipped_job_as_failed(self):
        out_path = self.repo.path(".agents/state/checks2.json")
        rc, out = self.repo.run(REVIEW, "checks", "--out", out_path, "--must-run", "agentive-independence",
                                "--job", "agentive-independence=skipped", "--job", "dependency-review=skipped")
        self.assertEqual(rc, 0, out)
        self.assertEqual(json.loads(h.read(out_path)), [{"name": "agentive-independence", "status": "fail"}])
        rc, out = self.repo.run(REVIEW, "checks", "--out", out_path, "--must-run", "agentive-independence", "--job", "agentive-independence=")
        self.assertEqual(json.loads(h.read(out_path)), [{"name": "agentive-independence", "status": "fail"}])
        rc, out = self.repo.run(REVIEW, "checks", "--out", out_path, "--must-run", "agentive-independence", "--job", "agentive-independence=success")
        self.assertEqual(json.loads(h.read(out_path)), [{"name": "agentive-independence", "status": "pass"}])

    def test_outputs_hash_rejects_changed_outputs(self):
        tasks = self.repo.path(".agents/state/oh/tasks")
        os.makedirs(tasks, exist_ok=True)
        for n, t in (("a.output.json", "{\"x\": 1}"), ("b.output.json", "{\"y\": 2}")):
            with open(os.path.join(tasks, n), "w", encoding="utf-8") as f:
                f.write(t)
        work = self.repo.path(".agents/state/oh")
        rc, out = self.repo.run(REVIEW, "outputs-hash", "--work", work)
        self.assertEqual(rc, 0, out)
        sha = out.split("sha256=")[1].split()[0]
        rc, out = self.repo.run(REVIEW, "outputs-hash", "--work", work, "--expect", sha)
        self.assertIn("AGENTIVE_OUTPUTS=ok", out)
        self.assertTrue(os.path.exists(os.path.join(tasks, "a.output.json")))
        with open(os.path.join(tasks, "a.output.json"), "w", encoding="utf-8") as f:
            f.write("{\"x\": 99}")                                                   # changed after the hash was recorded
        rc, out = self.repo.run(REVIEW, "outputs-hash", "--work", work, "--expect", sha)
        self.assertEqual(rc, 0, out)
        self.assertIn("AGENTIVE_OUTPUTS=rejected", out)
        self.assertFalse(os.path.exists(os.path.join(tasks, "a.output.json")))
        self.assertTrue(os.path.exists(os.path.join(tasks, "a.output.json.rejected")))


SECRET = "sk-ant-api03-SECRETVALUE-do-not-print"


class E2EBatchATest(PipelineBase):
    """E2E-10b (AISTEPFAIL) and E2E-11 (WFCHANGE), user decisions 2026-10-30."""
    WF = ".github/workflows/agentive-review.yml"

    def exec_file(self, items):
        path = self.repo.path(".agents/state/exec.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(items, f)
        return path

    def test_e2e_10b_codes_from_the_execution_file(self):
        from agentive.review import llmstate
        res = lambda **k: [{"type": "system"}, dict({"type": "result", "is_error": True}, **k)]      # noqa: E731
        for items, code in ((res(result=f"Credit balance is too low ({SECRET})"), "insufficient_credits"),
                            (res(result="Invalid API key · Please run /login"), "auth"),
                            (res(result="API Error: 429 rate_limit_error"), "rate_limit"),
                            (res(result="Prompt is too long"), "context_too_long"),
                            (res(result="Request timed out"), "timeout"),
                            (res(subtype="error_max_turns"), "max_turns"),
                            (res(result="something else"), "unclassified"),
                            ([{"type": "system"}], "no_result")):
            self.assertEqual(llmstate.classify_claude(self.exec_file(items)), code, items)
        self.assertEqual(llmstate.classify_claude(self.repo.path("missing.json")), "no_result")
        st = llmstate.record("claude", "failure", "", self.exec_file(res(result=f"Credit balance is too low {SECRET}")))
        self.assertEqual((st["failed"], st["code"], st["started"]), (True, "insufficient_credits", True))
        self.assertEqual(llmstate.record("codex", "failure", "", "")["code"], "codex_exit")
        ok = llmstate.record("claude", "success", "success", "")
        self.assertEqual((ok["failed"], ok["started"], "code" in ok), (False, True, False))
        self.assertIs(llmstate.record("claude", "success", "", "")["started"], False)     # the action ended before Claude ran
        self.assertEqual(sorted(llmstate.SENTENCES), sorted(llmstate.CODES))

    def test_e2e_10b_failure_shows_code_sentence_and_url_never_the_text(self):
        self.repo.write({"README.md": "# r\n\nwords\n"})
        head = self.repo.commit("docs")
        work = self.repo.path(".agents/state/wfail")
        env = {"AGENTIVE_LLM_OUTCOME": "failure", "AGENTIVE_LLM_CONCLUSION": "",
               "AGENTIVE_LLM_EXECUTION_FILE": self.exec_file([{"type": "result", "is_error": True, "result": f"Credit balance is too low {SECRET}"}]),
               "GITHUB_RUN_ID": "42", "GITHUB_REPOSITORY": "o/r", "GITHUB_SERVER_URL": "https://github.com"}
        rc, out = self.repo.run(REVIEW, "llm-state", "--work", work, env=env)
        self.assertEqual(rc, 0, out)
        ann = [ln for ln in out.splitlines() if ln.startswith("::error")]
        self.assertEqual(len(ann), 1, out)
        self.assertIn("ai_step_failed:insufficient_credits", ann[0])
        self.assertIn("https://github.com/o/r/actions/runs/42", ann[0])
        state = h.read(os.path.join(work, "llm-state.json"))
        rc, gout = self.repo.run(REVIEW, "gate", "7", "--pr-json", self.pr(head, body(explainer=True)), "--work", work,
                                 "--comments-json", self.comments([]), env={"GITHUB_RUN_ID": "42", "GITHUB_REPOSITORY": "o/r"})
        rep = json.loads(h.read(os.path.join(work, "review-report.json")))
        self.assertEqual(self.rep_schema(rep), [])
        self.assertEqual((rc, rep["rv8"]["decision"], rep["rv8"]["auto_merge"]), (0, "human_required", False), gout)
        self.assertIn("ai_step_failed", rep["rv8"]["reason_codes"])
        last = gout.splitlines()[-1]
        self.assertIn("NEXT=ai_step_failed:insufficient_credits: the Claude API account of the CI credential has too little credit", last)
        self.assertIn("(run https://github.com/o/r/actions/runs/42)", last)
        from agentive.review import llmstate, post
        comment = post.summary_comment(rep, 0, {"ai_step_failed": llmstate.failure_text(llmstate.read(work), "https://x/runs/42")})
        # COMMENTUX: the cause is a Why line, the fix with the run link the What to do line, the code only in Details
        self.assertIn("- The AI review step failed: the CI credential's Claude API account has too little credit.", comment)
        self.assertIn("**What to do:** Maintainer: add credit, or store the subscription token", comment)
        self.assertIn("([run](https://x/runs/42))", comment)
        self.assertIn("- Error code: `ai_step_failed:insufficient_credits`", comment)
        self.assertNotIn("ai_step_failed", comment.split("<details>")[0])
        for text in (out, state, gout, json.dumps(rep), comment):          # no error text anywhere
            self.assertNotIn("SECRETVALUE", text)
            self.assertNotIn("Credit balance is too low", text)

    # ---------------- E2E-11
    def skip(self, run_rev, base_ref="main", base_sha=None, **kw):
        from agentive.review import llmstate
        return llmstate.workflow_skip(self.repo.dir, kw.pop("tool", "claude"), kw.pop("state", None), kw.pop("have", False), "main",
                                      base_ref, base_sha or self.base, run_rev, **kw)

    def test_e2e_11_detection_compares_the_run_commit_with_the_default_branch(self):
        g = lambda *a: h.git(self.repo.dir, *a)      # noqa: E731
        g("update-ref", "refs/remotes/origin/main", self.base)
        # first install: the default branch has no workflow, the PR adds it
        self.repo.write({self.WF: "name: agentive-review\n"})
        add = self.repo.commit("install")
        k = self.skip(add)
        self.assertEqual((k["kind"], k["exempt"]), ("workflow_change", True))
        self.assertIn("first install", k["detail"])
        self.assertEqual(k["next"], "workflow-change PR: AI review skipped by the action; human review (merge it, then later PRs get the AI review)")
        # hybrid: Claude ran, or the CI produced a review -> no skip reason although the blobs differ; codex path: none
        self.assertIsNone(self.skip(add, state={"started": True}))
        self.assertIsNone(self.skip(add, have=True))
        self.assertIsNone(self.skip(add, tool="codex"))
        # fork first
        self.assertEqual(self.skip(add, head_repo="x/fork", repository="o/r")["kind"], "fork")
        self.assertFalse(self.skip(add, head_repo="x/fork", repository="o/r")["exempt"])
        # the default branch now has the workflow; a PR that changes it
        g("update-ref", "refs/remotes/origin/main", add)
        self.repo.write({self.WF: "name: agentive-review\n# changed\n"})
        change = self.repo.commit("change workflow")
        self.assertEqual(self.skip(change, base_sha=add)["kind"], "workflow_change")
        self.assertIsNone(self.skip(add, base_sha=add))                                   # identical: the action runs
        # an old branch whose head differs but whose merge commit has the default copy: no false positive
        g("checkout", "-q", "-b", "old", add)
        self.repo.write({"src/app.py": "import os\n\n\ndef run():\n    return 2\n"})
        old_head = self.repo.commit("old branch work")
        g("update-ref", "refs/remotes/origin/main", change)
        g("checkout", "-q", "-b", "merge-sim", change)
        g("merge", "-q", "--no-ff", "old", "-m", "merge commit of the PR")
        merge = g("rev-parse", "HEAD")
        self.assertIsNotNone(self.skip(old_head, base_sha=change))                       # the head blob differs from the default
        self.assertIsNone(self.skip(merge, base_sha=change))                             # the run's merge commit does not
        # the PR does not change the file, its base branch differs from the default branch
        k = self.skip(old_head, base_ref="develop", base_sha=add)
        self.assertEqual((k["kind"], k["exempt"]), ("base_differs", True))
        self.assertIn("bring the default branch workflow into the base", k["next"])
        # same, base == default: the run's merge commit is older than the default branch's workflow
        k = self.skip(old_head, base_ref="main", base_sha=add)
        self.assertEqual(k["kind"], "stale_merge")
        self.assertTrue(k["next"].startswith("push a new commit or close and reopen the PR"))
        self.assertNotIn("rerun", k["next"])
        self.assertNotIn("re-run", k["next"])
        # facts that cannot be read: no reason (fail-safe, earlier behaviour)
        from agentive.review import llmstate
        self.assertIsNone(llmstate.workflow_skip(self.repo.dir, "claude", None, False, "trunk", "main", add, old_head))   # no origin/trunk

    def test_wfskip_not_started_success_is_an_exempt_skip_whichever_files_changed(self):
        """WFSKIP (talent-ms PR #13): the workflow file is identical on the default branch, base and head, yet the action skipped."""
        g = lambda *a: h.git(self.repo.dir, *a)      # noqa: E731
        self.repo.write({self.WF: "name: agentive-review\n"})
        wf = self.repo.commit("workflow on main")
        g("update-ref", "refs/remotes/origin/main", wf)
        self.repo.write({"docs/note.md": "a docs-only PR\n"})
        head = self.repo.commit("docs only")
        ns = {"tool": "claude", "outcome": "success", "started": False, "failed": False}
        k = self.skip(head, base_sha=wf, state=ns)
        self.assertEqual((k["kind"], k["exempt"]), ("action_skipped", True))
        self.assertIn("workflow validation refused by the token server", k["detail"])
        self.assertIn("human review", k["next"])
        # the blob rules keep their precise kind when the workflow file did change
        self.repo.write({self.WF: "name: agentive-review\n# changed\n"})
        change = self.repo.commit("change workflow")
        self.assertEqual(self.skip(change, base_sha=wf, state=ns)["kind"], "workflow_change")
        # fork first
        self.assertEqual(self.skip(head, base_sha=wf, state=ns, head_repo="x/fork", repository="o/r")["kind"], "fork")
        # Claude started: no skip; step failure: no skip (the AISTEPFAIL path); a CI review exists: no skip; codex: none
        self.assertIsNone(self.skip(head, base_sha=wf, state=dict(ns, started=True)))
        self.assertIsNone(self.skip(head, base_sha=wf, state=dict(ns, outcome="failure", failed=True)))
        self.assertIsNone(self.skip(head, base_sha=wf, state=ns, have=True))
        self.assertIsNone(self.skip(head, base_sha=wf, state=ns, tool="codex"))
        # unreadable state or outcome: None (fail-safe)
        self.assertIsNone(self.skip(head, base_sha=wf, state=None))
        self.assertIsNone(self.skip(head, base_sha=wf, state=dict(ns, started=None)))
        self.assertIsNone(self.skip(head, base_sha=wf, state={"tool": "claude", "started": False}))
        self.assertIsNone(self.skip(head, base_sha=wf, state=dict(ns, outcome="unknown")))

    def gate_env(self):
        return {"AGENTIVE_DEFAULT_BRANCH": "main", "GITHUB_REPOSITORY": "o/r", "AGENTIVE_HEAD_REPO": "o/r"}

    def test_e2e_11_install_pr_ends_human_required_not_block(self):
        h.git(self.repo.dir, "update-ref", "refs/remotes/origin/main", self.base)
        self.repo.write({self.WF: "name: agentive-review\n"})
        head = self.repo.commit("install")
        work = self.repo.path(".agents/state/wskip")
        os.makedirs(work, exist_ok=True)
        rc, out = self.repo.run(REVIEW, "llm-state", "--work", work, env={"AGENTIVE_LLM_OUTCOME": "success", "AGENTIVE_LLM_CONCLUSION": ""})
        self.assertIn("AGENTIVE_LLM_STATE=not_started", out)
        env = dict(self.gate_env(), GITHUB_SHA=head)
        # agentive-independence: the missing CI review is a note, the job passes
        rc, iout = self.repo.run(REVIEW, "independence", "7", "--pr-json", self.pr(head, body(explainer=True)), "--work", work, env=env)
        self.assertEqual(rc, 0, iout)
        self.assertIn("AGENTIVE_INDEPENDENCE=ok note=ci_review_skipped:workflow_change", iout)
        self.assertIn("::notice title=agentive AI review skipped::workflow-change PR", iout)
        # headings missing: still a failure (only the CI-review rule is exempted)
        rc, bad = self.repo.run(REVIEW, "independence", "7", "--pr-json", self.pr(head, "```yaml agentive-review\nagentive-review: {}\n```\n"),
                                "--work", work, env=env)
        self.assertEqual(rc, 1, bad)
        # the gate: an independence job that ran an older script failed; the gate re-evaluates it with the exemption
        checks = self.repo.path(".agents/state/checks-skip.json")
        with open(checks, "w", encoding="utf-8") as f:
            json.dump([{"name": "agentive-independence", "status": "fail"}], f)
        rc, gout = self.repo.run(REVIEW, "gate", "7", "--pr-json", self.pr(head, body(explainer=True)), "--work", work, "--checks", checks,
                                 "--comments-json", self.comments([]), env=env)
        rep = json.loads(h.read(os.path.join(work, "review-report.json")))
        self.assertEqual(self.rep_schema(rep), [])
        self.assertEqual((rc, rep["rv8"]["decision"], rep["rv8"]["check_conclusion"], rep["rv8"]["auto_merge"]),
                         (0, "human_required", "success", False), gout)
        self.assertIn("ai_review_skipped", rep["rv8"]["reason_codes"])
        self.assertNotIn("failing_required_check", rep["rv8"]["reason_codes"])
        self.assertIn("::notice title=agentive AI review skipped::", gout)
        self.assertIn("NEXT=workflow-change PR: AI review skipped by the action; human review", gout.splitlines()[-1])
        # without a skip (the workflow equals the default branch's copy and the state is unreadable) the missing CI review still blocks
        h.git(self.repo.dir, "update-ref", "refs/remotes/origin/main", head)
        os.remove(os.path.join(work, "llm-state.json"))
        rc, gout = self.repo.run(REVIEW, "gate", "7", "--pr-json", self.pr(head, body(explainer=True)), "--work", work, "--checks", checks,
                                 "--comments-json", self.comments([]), env=env)
        rep = json.loads(h.read(os.path.join(work, "review-report.json")))
        self.assertEqual((rc, rep["rv8"]["decision"]), (1, "block"), gout)
        self.assertNotIn("ai_review_skipped", rep["rv8"]["reason_codes"])


class PrecommitTest(PipelineBase):
    def verify_json(self, ok=True, tree=None):
        """A green (or red) verify record; run_pre rewrites it for the staged tree, as if verify ran on what is committed
        (VERIFYTREE: freshness is the content tree, tested in tests/review/test_regressions.py E2E-08)."""
        self._verify_ok = ok
        head = h.git(self.repo.dir, "rev-parse", "HEAD")
        v = {"run_id": "r1", "ts": "2026-10-28T00:00:00Z", "total_failures": 0 if ok else 1, "head_sha": head, "worktree_dirty": True,
             "verbs": {"test": {"ok": ok, "failures": 0 if ok else 1, "duration_ms": 5}}}
        if tree:
            v["content_tree"] = tree
        self.assertEqual(h.schema_validator("verify-result")(v), [])
        with open(self.repo.path(".agents/state/verify.json"), "w", encoding="utf-8") as f:
            json.dump(v, f)

    def run_pre(self, responder, *extra, env=None):
        if hasattr(self, "_verify_ok"):
            self.verify_json(self._verify_ok, tree=h.git(self.repo.dir, "write-tree"))
        args = [REVIEW, "precommit"] + list(extra)
        for _ in range(4):
            rc, out = self.repo.run(*args, env=env)
            if rc != 3:
                break
            h.answer_tasks(out, responder)
        rec = json.loads(h.read(self.repo.path(".agents/state/precommit-review.json")))
        self.assertEqual(h.schema_validator("precommit-review")(rec), [])
        rep = json.loads(h.read(self.repo.path(".agents/state/review-report.json")))
        self.assertEqual(self.rep_schema(rep), [])
        self.assertEqual(rep["stages_run"], ["RV1", "RV2", "RV5"])
        return rc, out, rec

    def test_e2e_59_task_next_lines_never_tell_the_agent_to_pass_a_tool(self):
        """E2E-59: the precommit skill passes no --tool, so the NEXT of the task lines repeats the command as it was typed (no flag)."""
        os.makedirs(self.repo.path(".agents/state"), exist_ok=True)
        self.verify_json()
        self.repo.write({"src/app.py": "import os\n\n\ndef run():\n    return 2\n"})
        h.git(self.repo.dir, "add", "-A")
        env = {"AGENTIVE_TOOL": "", "CLAUDECODE": ""}
        rc, out = self.repo.run(REVIEW, "precommit", env=env)
        self.assertEqual(rc, 3, out)
        nexts = [ln for ln in out.splitlines() if "NEXT=" in ln]
        self.assertTrue(nexts)
        for ln in nexts:
            self.assertNotIn("--tool", ln)
        self.assertIn(".agents/bin/agentive-review precommit", nexts[-1])
        # a flag the caller typed still works and is repeated as typed; the record keeps the tool
        rc, out, rec = self.run_pre(h.default_responder(), "--tool", "codex", env=env)
        self.assertEqual(rc, 0, out)
        self.assertEqual(rec["reviewer"]["tool"], "codex")
        # no flag: AGENTIVE_TOOL decides, else CLAUDECODE, else other
        for add, want in (({"AGENTIVE_TOOL": "codex"}, "codex"), ({"CLAUDECODE": "1"}, "claude_code"), ({}, "other")):
            rc, out, rec = self.run_pre(h.default_responder(), env=dict(env, **add))
            self.assertEqual(rec["reviewer"]["tool"], want, add)

    def test_commit_blocked_and_confirm(self):
        os.makedirs(self.repo.path(".agents/state"), exist_ok=True)
        self.verify_json()
        self.repo.write({"src/app.py": "import os\n\n\ndef run():\n    return 2\n"})
        h.git(self.repo.dir, "add", "-A")
        rc, out, rec = self.run_pre(h.default_responder())
        self.assertEqual(rc, 0, out)
        self.assertIn("AGENTIVE_PRECOMMIT=commit", out.splitlines()[-1])
        bug = {"correctness-reviewer": [{"file": "src/app.py", "line": 5, "trigger": "t", "summary": "s", "severity": "important"}]}
        self.repo.write({"src/app.py": "import os\n\n\ndef run():\n    return 3\n"})
        h.git(self.repo.dir, "add", "-A")
        rc, out, rec = self.run_pre(h.default_responder(bug))
        self.assertEqual((rc, len(rec["blocking"])), (1, 1), out)
        self.repo.write({"src/app.py": "import os\n\n\ndef run():\n    return 4\n", ".github/workflows/x.yml": "name: x\n"})
        h.git(self.repo.dir, "add", "-A")
        rc, out, rec = self.run_pre(h.default_responder())
        self.assertIn("AGENTIVE_PRECOMMIT=confirm", out.splitlines()[-1])
        self.assertEqual(rec["u2"], {"run": "interactive", "human_confirmation": "not_obtained", "action": "pr_only_tier2"})
        rc, out, rec = self.run_pre(h.default_responder(), "--human-confirmation", "obtained")
        self.assertEqual(rec["u2"]["action"], "commit")
        self.assertIn("AGENTIVE_PRECOMMIT=commit", out.splitlines()[-1])
        rc, out, rec = self.run_pre(h.default_responder(), env={"AGENTIVE_HEADLESS": "1"})
        self.assertEqual(rec["u2"]["run"], "non_interactive")
        self.assertIn("do not commit to the default branch", out.splitlines()[-1])
        rc, out = self.repo.run(REVIEW, "precommit", "--human-confirmation", "obtained", env={"AGENTIVE_HEADLESS": "1"})
        self.assertEqual(rc, 2, out)

    def test_e2e_49_confirm_next_names_the_no_human_path_on_the_default_branch(self):
        """E2E-49: `AGENTIVE_PRECOMMIT=confirm` on the default branch also says what to do when no human can answer (a delegated agent
        the tool does not mark as headless): a work branch and /pr. Still one final line."""
        self.stage_confirm()
        h.git(self.repo.dir, "switch", "-q", "main")
        branch = "main"
        rc, out, rec = self.run_pre(h.default_responder())
        last = out.strip().splitlines()[-1]
        self.assertTrue(last.startswith("AGENTIVE_PRECOMMIT=confirm u2=interactive/not_obtained"), last)
        self.assertIn("then rerun with --human-confirmation obtained; if no human is present", last)
        self.assertIn(f"do not commit to {branch}, commit on a work branch (git switch -c <type>/<slug> --no-track origin/{branch}) and open a PR with /pr", last)
        self.assertEqual(sum(1 for ln in out.splitlines() if "NEXT=" in ln), 1, out)

    def stage_confirm(self):
        os.makedirs(self.repo.path(".agents/state"), exist_ok=True)
        self.verify_json()
        self.repo.write({"src/app.py": "import os\n\n\ndef run():\n    return 4\n", ".github/workflows/x.yml": "name: x\n"})
        h.git(self.repo.dir, "add", "-A")

    def test_e2e_52_confirm_next_names_the_no_human_path_on_a_work_branch_and_detached_head(self):
        """E2E-52: on a work branch the confirm NEXT also says that with no human present the agent commits on this branch and opens
        a PR with /pr (tier 2); it never says to move off the branch. Detached HEAD: create a work branch first. One final line,
        `--human-confirmation obtained` unchanged."""
        self.stage_confirm()
        rc, out, rec = self.run_pre(h.default_responder())               # setUp left the repo on the work branch `feat`
        last = out.strip().splitlines()[-1]
        self.assertTrue(last.startswith("AGENTIVE_PRECOMMIT=confirm u2=interactive/not_obtained"), last)
        self.assertIn("ask the human to confirm the change to ci (U2), then rerun with --human-confirmation obtained; if no human is present", last)
        self.assertTrue(last.endswith("commit on this work branch (feat) and open a PR with /pr (tier 2, a human merges it)"), last)
        self.assertNotIn("do not commit to", last)
        self.assertEqual(sum(1 for ln in out.splitlines() if "NEXT=" in ln), 1, out)
        h.git(self.repo.dir, "switch", "-q", "--detach")
        rc, out, rec = self.run_pre(h.default_responder())
        last = out.strip().splitlines()[-1]
        self.assertIn("then rerun with --human-confirmation obtained; if no human is present", last)
        self.assertTrue(last.endswith("HEAD is detached, so create a work branch first (git switch -c <type>/<slug>), commit there and open a PR with /pr (tier 2, a human merges it)"), last)
        self.assertEqual(sum(1 for ln in out.splitlines() if "NEXT=" in ln), 1, out)
        rc, out, rec = self.run_pre(h.default_responder(), "--human-confirmation", "obtained")     # not weakened
        self.assertEqual(rec["u2"]["action"], "commit")
        self.assertIn("AGENTIVE_PRECOMMIT=commit", out.strip().splitlines()[-1])


if __name__ == "__main__":
    unittest.main()
