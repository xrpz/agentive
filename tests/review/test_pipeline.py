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

REVIEW = ".agents/bin/agentive-review"
RULES = os.path.join(h.SRC, "code_review.md.regions", "rules.md")
DECISIONS = os.path.join(h.SRC, "dot-agents", "decisions.yaml")
EXPLAINER = ("## Explainer\n\nChanges the flow (src/app.py:1).\n\n### Before\n\n```mermaid\nflowchart LR\n a-->b\n```\n\n"
             "### After\n\n```mermaid\nflowchart LR\n a-->c\n```\n\n")
TAIL = "## Verification\n\n- test -> 0\n\n## Risk\n\nlow\n\n## How to revert\n\ngit revert\n"


def body(explainer=False, u2=None, extra=None):
    blk = {"implementer": {"tool": "claude_code", "model": "m", "session_id": "impl-1", "started_at": "2026-10-28T00:00:00Z"},
           "reviews": [{"role": "reviewer", "tool": "claude_code", "model": "m", "session_id": "pre-1", "context": "fresh",
                        "source": "precommit-review", "verdict": "pass", "reviewer_id": "correctness-reviewer"}]}
    if u2:
        blk["u2"] = u2
    blk.update(extra or {})
    return (EXPLAINER if explainer else "") + TAIL + "\n```yaml agentive-review\n" + ayaml.dumps({"agentive-review": blk}) + "```\n"


class PipelineBase(unittest.TestCase):
    def setUp(self):
        rules = ("# Review criteria\n\nUser text.\n\n<!-- AGENTIVE START rules -->\n" + h.read(RULES) + "<!-- AGENTIVE END rules -->\n"
                 if os.path.exists(RULES) else "# Review criteria\n")
        dec = h.read(DECISIONS) if os.path.exists(DECISIONS) else "U1: {value: C, decided_by: user}\n"
        self.repo = h.Repo({"code_review.md": rules, ".agents/decisions.yaml": dec, "README.md": "# r\n",
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
        self.assertEqual((rep["rv8"]["decision"], rep["rv8"].get("shadow_would_auto_merge")), ("human_required", True))
        self.assertIn("classifier", agents)
        bug = {"correctness-reviewer": [{"file": "src/app.py", "line": 5, "trigger": "x == 0", "summary": "division by zero",
                                         "severity": "important", "bug_class": "unchecked divisor"}]}
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
        self.repo.write({".agents/decisions.yaml": "U1: {value: A, decided_by: user}\n", "code_review.md": "# loosened\n",
                         "README.md": "# r\n\nx\n"})
        head = self.repo.commit("loosen")
        rep, _, _ = self.ci(head, h.default_responder(), body(explainer=True))
        self.assertEqual(rep["rv8"]["u1_value"], "C")
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

    def test_round_two_marks_fixed_findings(self):
        self.repo.write({"src/app.py": "import os\n\n\ndef run(x):\n    return 10 / x\n"})
        h1 = self.repo.commit("r1")
        self.repo.write({"src/app.py": "import os\n\n\ndef run(x):\n    return 10 / x if x else 0\n"})
        h2 = self.repo.commit("r2")
        prev = {"review_round": 1, "pipeline": {"classification": {"classes": ["fix"], "method": "llm"}, "tier": {"value": 1, "reasons": []},
                                                "verified_findings": [], "risk_score": {"score": None, "reason": "not_tier1"},
                                                "gate": {"decision": "block", "check": "agentive-review-gate", "auto_merge": False, "head_sha": h1}}}
        data = json.dumps({"severity": "important", "trigger": "x == 0", "summary": "division by zero", "reviewers": ["correctness-reviewer"],
                           "round": 1, "verdict": "confirmed", "verifier_session_id": "v1"})
        root = {"id": 200, "body": "x\n<!-- agentive-finding: F-0123456789 -->\n<!-- agentive-finding-data: %s -->" % data, "path": "src/app.py", "line": 5}

        def responder(agent, inp):
            if agent == "correctness-reviewer":
                self.assertEqual((inp["review_mode"], inp["diff_range"]["base"]), ("incremental", h1))
                self.assertEqual([o["id"] for o in inp["open_findings"]], ["F-0123456789"])
                patch = h.read(inp["diff_file"])
                self.assertIn("if x else 0", patch)
                self.assertNotIn("-def run():", patch)                   # only the diff since the last reviewed head
                return {"findings": [], "open_findings": [{"id": "F-0123456789", "status": "fixed"}]}
            return h.default_responder()(agent, inp)
        rep, _, _ = self.ci(h2, responder, body(extra=prev), comments=[root])
        self.assertEqual(rep["review_round"], {"round": 2, "mode": "incremental", "since_sha": h1})
        f = rep["findings"][0]
        self.assertEqual((f["id"], f["response"]["source"], f["blocking"]), ("F-0123456789", "next_round", False))

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

    def test_independence_cli(self):
        self.repo.write({"README.md": "# r\n\nmore\n"})
        head = self.repo.commit("docs")
        rc, out = self.repo.run(REVIEW, "independence", "7", "--pr-json", self.pr(head, body()), "--work", self.repo.path(".agents/state/none"))
        self.assertEqual(rc, 0, out)
        rc, out = self.repo.run(REVIEW, "independence", "7", "--pr-json", self.pr(head, "## Verification\n"), "--work", self.repo.path(".agents/state/none"))
        self.assertEqual(rc, 1, out)
        self.assertIn("AGENTIVE_INDEPENDENCE=fail", out)

    def test_checks_helper(self):
        out_path = self.repo.path(".agents/state/checks.json")
        rc, out = self.repo.run(REVIEW, "checks", "--out", out_path, "--outer", '[{"name":"test","status":"pass"},{"name":"lint","status":"skipped"}]',
                                "--job", "dependency-review=", "--job", "agentive-independence=failure")
        self.assertEqual(rc, 0, out)
        self.assertEqual(json.loads(h.read(out_path)), [{"name": "test", "status": "pass"}, {"name": "lint", "status": "skipped"},
                                                         {"name": "agentive-independence", "status": "fail"}])


class PrecommitTest(PipelineBase):
    def verify_json(self, ok=True):
        head = h.git(self.repo.dir, "rev-parse", "HEAD")
        v = {"run_id": "r1", "ts": "2026-10-28T00:00:00Z", "total_failures": 0 if ok else 1, "head_sha": head, "worktree_dirty": True,
             "verbs": {"test": {"ok": ok, "failures": 0 if ok else 1, "duration_ms": 5}}}
        self.assertEqual(h.schema_validator("verify-result")(v), [])
        with open(self.repo.path(".agents/state/verify.json"), "w", encoding="utf-8") as f:
            json.dump(v, f)

    def run_pre(self, responder, *extra, env=None):
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


if __name__ == "__main__":
    unittest.main()
