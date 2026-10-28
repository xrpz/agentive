# agentive-unit: selftest.component
# design: spec.review_pipeline (RV1-RV8, convergence, reply_line, kill switch, reviewer_eval.scoring), docs/IMPLEMENTATION.md §6
"""Pure stage logic of the review pipeline: globs, rules loading, RV3/RV4/RV7b/RV8, kill switch, reply lines,
PR body checks, reviewer eval scoring. Every record checked here is also validated against its design schema."""
import datetime
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers as h  # noqa: E402

from agentive import ayaml  # noqa: E402
from agentive.review import config as cfg, evals, findings as fm, independence, killswitch, pr_body, rv3, rv4, rv7b, rv8, util  # noqa: E402

RULES_REGION = os.path.join(h.SRC, "code_review.md.regions", "rules.md")


class GlobTest(unittest.TestCase):
    def test_design_default_globs(self):
        cases = [("**/auth/**", "auth/login.py", True), ("**/auth/**", "src/auth/x/y.ts", True), ("**/auth/**", "authx/a", False),
                 ("**/.env*", ".env", True), ("**/.env*", "app/.env.local", True), ("**/permissions*", "permissions.json", True),
                 (".github/workflows/**", ".github/workflows/ci.yml", True), (".github/workflows/**", "x/.github/workflows/a", False),
                 (".claude/settings*.json", ".claude/settings.local.json", True), ("**/payment*/**", "svc/payments/api.go", True),
                 ("*.md", "README.md", True), ("*.md", "docs/a.md", False), ("**/*secret*", "cfg/my_secret.yaml", True),
                 ("code_review.md", "code_review.md", True), ("evals/reviewer/**", "evals/reviewer/a.yaml", True)]
        for g, p, want in cases:
            self.assertEqual(util.glob_match(g, p), want, (g, p))


class RulesTest(unittest.TestCase):
    def test_c_default_block_loads_and_validates(self):
        text = h.read(RULES_REGION) if os.path.exists(RULES_REGION) else None
        if text is None:
            self.skipTest("C's code_review.md#rules region not present yet")
        loaded = cfg.load_rules(lambda p: text)
        self.assertEqual(loaded.problems, [])
        doc = ayaml.loads(util.fenced_blocks(text, "yaml agentive-review-rules")[0])
        self.assertEqual(h.schema_validator("review-rules")(doc), [])
        globs = {e["glob"] for e in loaded.value["trust_boundary"]["never_ai_approve"]}
        self.assertTrue({e["glob"] for e in cfg.DEFAULT_RULES["trust_boundary"]["never_ai_approve"]} <= globs)
        self.assertIn(".agents/hooks/**", loaded.value["trust_boundary"]["confirm_scope_globs"]["hooks"])

    def test_builtin_defaults_match_schema(self):
        self.assertEqual(h.schema_validator("review-rules")({"agentive-review-rules": cfg.DEFAULT_RULES}), [])
        self.assertEqual(h.schema_validator("review-config")(cfg.DEFAULT_CONFIG), [])
        self.assertEqual(h.schema_validator("rubric")(cfg.DEFAULT_RUBRIC), [])

    def test_missing_and_invalid_blocks_fall_back_to_defaults(self):
        self.assertEqual(cfg.load_rules(lambda p: None).value, cfg.DEFAULT_RULES)
        bad = "```yaml agentive-review-rules\nagentive-review-rules:\n  tiers: {tier0_classes: [feature]}\n```\n"
        loaded = cfg.load_rules(lambda p: bad)
        self.assertTrue(loaded.problems)
        self.assertEqual(loaded.value["tiers"]["tier0_classes"], ["docs", "format"])

    def test_config_constants_rejected(self):
        self.assertTrue(cfg.validate_config({"auto_merge": {"kill_switch": {"count": 3}}}))
        self.assertTrue(cfg.validate_config({"stages": {"RV5": {"reviewers": ["correctness-reviewer", "testing-reviewer"]}}}))
        self.assertEqual(cfg.validate_config({"auto_merge": {"tier1": "on"}, "stages": {"RV3": {"llm_layer": False}}}), [])

    def test_rubric_override_merge_and_invalid(self):
        preset = ayaml.dumps(cfg.DEFAULT_RUBRIC)
        over = "extends: agentive:rubric@1\nitems:\n  - {id: recent_churn, enabled: false}\n  - {id: size, weight: 10}\n"
        eff = cfg.effective_rubric(lambda p: {".agents/rubric.yaml": preset, ".agents/custom/.agents/rubric.yaml": over}.get(p))
        self.assertTrue(eff.valid)
        by = {i["id"]: i for i in eff.value["items"]}
        self.assertFalse(by["recent_churn"]["enabled"])
        self.assertEqual(by["size"]["weight"], 10)
        self.assertEqual(h.schema_validator("rubric")(ayaml.loads(over)), [])
        bad = cfg.effective_rubric(lambda p: {".agents/rubric.yaml": preset, ".agents/custom/.agents/rubric.yaml": "extends: agentive:rubric@1\n"}.get(p))
        self.assertFalse(bad.valid)
        c_rubric = os.path.join(h.SRC, "dot-agents", "rubric.yaml")
        if os.path.exists(c_rubric):
            got = cfg.effective_rubric(lambda p: h.read(c_rubric) if p == ".agents/rubric.yaml" else None)
            self.assertTrue(got.valid, got.problems)
            self.assertEqual([(i["id"], i["weight"]) for i in got.value["items"]],
                             [("spread", 24), ("size", 24), ("incident_history", 24), ("change_type", 14), ("recent_churn", 14)])
            self.assertEqual(got.value["human_if_score_gt"], 28)


class TierTest(unittest.TestCase):
    T = cfg.DEFAULT_RULES["tiers"]

    def tier(self, hits=(), always=(), hc="not_required", classes=("docs",), lines=10):
        return rv4.rules_tier({"hits": list(hits)}, list(always), {"human_confirmation": hc}, {"classes": list(classes)}, lines, self.T)

    def test_rules(self):
        self.assertEqual(self.tier()[0], 0)
        self.assertEqual(self.tier(classes=("docs", "feature"))[0], 1)
        self.assertEqual(self.tier(lines=201)[0], 1)
        self.assertEqual(self.tier(classes=("lockfile",))[0], 1)          # lockfile is not tier 0 by default
        self.assertEqual(self.tier(classes=("security",)), (2, ["tier2_class"]))
        self.assertEqual(self.tier(lines=1001, classes=("feature",)), (2, ["large_change"]))
        t, r = self.tier(hc="not_obtained")
        self.assertEqual(t, 2)
        self.assertIn("U2.non_interactive", r)
        t, r = self.tier(always=[("chain_approval", "docs/plans/x/intent.md")])
        self.assertEqual(t, 2)
        self.assertIn("chain_approval", r)

    def test_raise_only_only_raises(self):
        self.assertEqual(rv4.finish(0, ["tier0"], {"human_needed": False, "criteria": []}, True)["tier"], 0)
        r = rv4.finish(1, ["tier1"], {"human_needed": True, "criteria": ["b"]}, True)
        self.assertEqual((r["tier"], r["raised_by_llm"]), (2, True))
        self.assertEqual(rv4.finish(0, ["tier0"], {"oops": 1}, True)["tier"], 2)        # invalid -> fail-safe tier 2
        self.assertEqual(rv4.finish(0, ["tier0"], None, True)["tier"], 2)
        self.assertEqual(rv4.finish(2, ["x"], {"human_needed": False, "criteria": []}, True)["tier"], 2)

    def test_rv3_llm_layer_never_gives_tier0_classes(self):
        self.assertEqual(rv3.finish({"a.md": "docs"}, ["x.py"], True, {"classes": ["docs"]})["classes"], ["docs", "other"])
        self.assertEqual(rv3.finish({}, ["x.py"], True, {"classes": ["fix"]}), {"classes": ["fix"], "method": "llm", "llm_valid": True})
        self.assertEqual(rv3.finish({}, ["x.py"], False, None), {"classes": ["other"], "method": "deterministic"})
        self.assertEqual(rv3.finish({}, ["x.py"], True, "not json")["llm_valid"], False)


class RubricScoreTest(unittest.TestCase):
    def score(self, **sig):
        lines = sig.pop("lines", 10)
        classes = sig.pop("classes", ["refactor"])
        s = {"modules_touched": ["a"], "shared_path_touched": False, "incident_history_90d": False, "recent_churn_30d": False}
        s.update(sig)
        return rv7b.run(1, "on", cfg.Loaded(cfg.effective_rubric(lambda p: None).value), s, classes, lines)

    def test_weights_and_threshold_effect(self):
        self.assertEqual(self.score()["score"], 0)
        self.assertEqual(self.score(modules_touched=["a", "b"])["score"], 24)
        self.assertEqual(self.score(lines=401)["score"], 24)
        self.assertEqual(self.score(classes=["feature"], recent_churn_30d=True)["score"], 28)   # 14+14 can auto-merge
        self.assertEqual(self.score(lines=401, classes=["deps"])["score"], 38)                  # 24 + any other -> human
        r = self.score(incident_history_90d=True)
        self.assertEqual((r["reason"], r["human_if_score_gt"], len(r["config_sha256"])), ("computed", 28, 64))
        self.assertEqual(rv7b.run(0, "on", cfg.Loaded(cfg.DEFAULT_RUBRIC), {}, [], 0), {"score": None, "reason": "not_tier1"})
        self.assertEqual(rv7b.run(1, "off", cfg.Loaded(cfg.DEFAULT_RUBRIC), {}, [], 0)["reason"], "tier1_off")
        self.assertEqual(rv7b.run(1, "on", cfg.Loaded(None, ["bad"]), {}, [], 0)["reason"], "config_invalid")


def finding(fid="F-1", sev="important", verdict="confirmed", response=None, shadow=False):
    f = {"id": fid, "reviewers": ["correctness-reviewer"], "severity": sev, "blocking": False, "file": "a.py", "line": 3,
         "trigger": "x is None", "summary": "s", "verification": {"verdict": verdict, "verifier_session_id": "v"}}
    if response:
        f["response"] = response
    if shadow:
        f["shadow"] = True
    return fm.set_blocking([f], "ci")[0]


class GateTest(unittest.TestCase):
    def decide(self, **kw):
        base = {"tier": 0, "rv4_reasons": ["tier0"], "rv2": {"hits": [], "confirm_scope_hits": []},
                "u2": {"run": "unknown", "human_confirmation": "not_required", "action": "commit", "source": "pr_body_missing"},
                "checks": [{"name": "test", "status": "pass"}], "required_checks": ["lint", "typecheck", "test"], "findings": [],
                "rv7b": {"score": None, "reason": "not_tier1"}, "u1": "C", "tier1_mode": "shadow", "config_valid": True,
                "explainer": None, "chain": {"result": "not_chain"}, "review_round": {"round": 1, "mode": "full"},
                "pending_verbs": [], "signals": {}, "llm_problems": [], "head_sha": "a" * 40, "trigger": "synchronize",
                "kill_switch": None, "source": "dev"}
        base.update(kw)
        out = rv8.decide(base)
        out.pop("_kill_switch_result", None)
        return out

    def report(self, rv8_out, tier, findings=(), u2=None, chain=None, rv7b_=None, rv2=None, rv4_reasons=("x",)):
        r = {"profile": "ci", "head_sha": "a" * 40, "stages_run": cfg.STAGES, "at": "2026-10-28T00:00:00Z",
             "rv1": {"checks": [], "excluded": [], "diff_stats": {"files": 1, "added": 1, "deleted": 0}, "chain": chain or {"result": "not_chain"}},
             "rv2": rv2 or {"hits": [], "confirm_scope_hits": []}, "rv3": {"classes": ["docs"], "method": "deterministic"},
             "rv4": {"tier": tier, "reasons": list(rv4_reasons)}, "rv7b": rv7b_ or {"score": None, "reason": "not_tier1"},
             "findings": list(findings), "rv8": rv8_out, "review_round": {"round": 1, "mode": "full"},
             "u2": u2 or {"run": "unknown", "human_confirmation": "not_required", "action": "commit", "source": "pr_body_missing"}}
        return h.schema_validator("review-report")(r)

    def test_tier0_auto_merge_and_policy(self):
        r = self.decide()
        self.assertEqual((r["decision"], r["auto_merge"], r["check_conclusion"]), ("pass", True, "success"))
        self.assertEqual(self.report(r, 0), [])
        self.assertEqual(self.decide(u1="A")["decision"], "human_required")
        r = self.decide(u1="B", tier=1, rv4_reasons=["tier1"])
        self.assertIn("u1_policy", r["reason_codes"])

    def test_block_rules(self):
        r = self.decide(checks=[{"name": "lint", "status": "fail"}])
        self.assertEqual((r["decision"], r["check_conclusion"]), ("block", "failure"))
        r = self.decide(findings=[finding()])
        self.assertEqual(r["decision"], "block")
        self.assertIn("unanswered_finding", r["reason_codes"])
        claim = finding(response={"kind": "open", "reason": "fixed", "source": "reply_line", "comment_id": 9})
        self.assertEqual(self.decide(findings=[claim])["decision"], "block")          # a fixed claim on this head still blocks
        fixed = finding(response={"kind": "fixed", "reason": "fixed", "source": "next_round"})
        self.assertEqual(self.decide(findings=[fixed])["decision"], "pass")
        self.assertEqual(self.report(self.decide(findings=[fixed]), 0, [fixed]), [])

    def test_explainer_and_chain_block_before_human_required(self):
        r = self.decide(tier=2, rv4_reasons=["trust_boundary"], explainer=({}, ["no_section"]))
        self.assertEqual(r["decision"], "block")
        self.assertIn("explainer_missing", r["reasons"])
        self.assertEqual(self.report(r, 2), [])
        ok_present = {k: True for k in ("section", "first", "before", "after", "before_mermaid", "after_mermaid")}
        r = self.decide(tier=2, rv4_reasons=["trust_boundary"], explainer=(ok_present, []))
        self.assertEqual(r["decision"], "human_required")
        self.assertEqual(self.report(r, 2), [])
        chain = {"result": "chain_order", "slug": "x", "stage": "feature", "problems": ["spec_not_on_base"], "next_action": "merge"}
        r = self.decide(chain=chain)
        self.assertEqual(r["decision"], "block")
        self.assertEqual(self.report(r, 0, chain=chain), [])

    def test_human_required_reasons(self):
        disputed = finding(response={"kind": "replied_resolved", "reason": "intentional", "source": "reply_line", "comment_id": 7})
        r = self.decide(findings=[disputed])
        self.assertEqual(r["decision"], "human_required")
        self.assertIn("finding_disputed", r["reasons"])
        self.assertEqual(self.report(r, 0, [disputed]), [])
        r = self.decide(findings=[finding(verdict="inconclusive")])
        self.assertIn("inconclusive_finding", r["reason_codes"])
        r = self.decide(findings=[finding()], review_round={"round": 3, "mode": "incremental", "since_sha": "b" * 40})
        self.assertEqual(r["decision"], "human_required")
        self.assertIn("review_rounds_exceeded", r["reason_codes"])
        self.assertEqual(self.decide(pending_verbs=["test"])["decision"], "human_required")
        self.assertEqual(self.decide(pending_verbs=["run"])["decision"], "pass")
        r = self.decide(signals={"public_api_change": "breaking"})
        self.assertEqual((r["decision"], r["gate_only"]), ("human_required", ["public_api_breaking"]))
        u2 = {"run": "non_interactive", "human_confirmation": "not_obtained", "action": "pr_only_tier2", "source": "pr_body"}
        r = self.decide(tier=2, rv4_reasons=["U2.non_interactive"], u2=u2, explainer=({k: True for k in ("section", "first", "before", "after", "before_mermaid", "after_mermaid")}, []))
        self.assertEqual(r["decision"], "human_required")
        rv2 = {"hits": [], "confirm_scope_hits": [{"path": ".github/workflows/x.yml", "category": "ci", "glob": ".github/workflows/**"}]}
        self.assertEqual(self.report(r, 2, u2=u2, rv2=rv2, rv4_reasons=["U2.non_interactive"]), [])

    def test_tier1_modes_and_kill_switch(self):
        rv7b_ok = {"score": 14, "reason": "computed", "preset": "agentive:rubric@1", "human_if_score_gt": 28, "config_sha256": "c" * 64,
                   "items": [{"id": "change_type", "hit": True, "weight": 14}]}
        t1 = dict(tier=1, rv4_reasons=["tier1"], rv7b=rv7b_ok)
        r = self.decide(**t1)
        self.assertEqual((r["decision"], r.get("shadow_would_auto_merge")), ("human_required", True))
        self.assertEqual(self.report(r, 1, rv7b_=rv7b_ok), [])
        r = self.decide(tier1_mode="on", **t1)
        self.assertEqual((r["decision"], r["auto_merge"], r["tier1_mode"]), ("pass", True, "on"))
        self.assertEqual(self.report(r, 1, rv7b_=rv7b_ok), [])
        high = dict(rv7b_ok, score=38)
        self.assertIn("score_above_threshold", self.decide(tier=1, rv4_reasons=["tier1"], rv7b=high, tier1_mode="on")["reason_codes"])
        r = self.decide(tier1_mode="on", kill_switch=lambda t: {"count": 2, "demoted": True}, **t1)
        self.assertEqual((r["decision"], r["demoted"], r["kill_switch"]["count"]), ("human_required", True, 2))
        self.assertEqual(self.report(r, 1, rv7b_=rv7b_ok), [])
        self.assertEqual(self.decide(tier1_mode="off", **t1)["reason_codes"], ["tier1_off"])


class HistorySignalsTest(unittest.TestCase):
    def test_revert_within_90_days_and_churn_within_30(self):
        from agentive.review import gitio, rv1
        r = h.Repo({"src/a.py": "x=1\n"})
        try:
            r.commit("init", date="2026-06-01T00:00:00Z")
            r.write({"src/a.py": "x=2\n"})
            r.commit('Revert "x"\n\nThis reverts commit ' + "a" * 40 + ".", date="2026-09-01T00:00:00Z")
            r.write({"src/b.py": "y=1\n"})
            base = r.commit("other", date="2026-10-20T00:00:00Z")
            h.git(r.dir, "checkout", "-q", "-b", "f")
            r.write({"src/a.py": "x=3\n", "src/c.py": "z=1\n"})
            head = r.commit("f", date="2026-10-21T00:00:00Z")
            d = gitio.Diff(r.dir, base, head)
            s = rv1.signals(d, d.name_status(), d.hunks(), {}, [], rv1.TEST_GLOBS, base, [])
            self.assertEqual((s["incident_history_90d"], s["recent_churn_30d"]), (True, False))
            self.assertEqual(s["modules_touched"], ["src"])
        finally:
            r.cleanup()


class KillSwitchTest(unittest.TestCase):
    def body(self, tier, auto=True, hotfix_of=None):
        blk = {"implementer": {"tool": "claude_code", "model": "m", "session_id": "s", "started_at": "2026-10-20T00:00:00Z"}, "reviews": [],
               "pipeline": {"classification": {"classes": ["docs"], "method": "deterministic"}, "tier": {"value": tier, "reasons": []},
                            "verified_findings": [], "risk_score": {"score": None, "reason": "not_tier1"},
                            "gate": {"decision": "pass", "check": "agentive-review-gate", "auto_merge": auto}}}
        if hotfix_of:
            blk["hotfix_of"] = hotfix_of
        return "## Verification\n\n```yaml agentive-review\n" + ayaml.dumps({"agentive-review": blk}) + "```\n"

    def test_counts_per_tier_and_source_after_last_close(self):
        now = datetime.datetime(2026, 10, 28, tzinfo=datetime.timezone.utc)
        ts = int((now - datetime.timedelta(days=2)).timestamp())
        merged = [{"number": 1, "author": "bot", "merged_at": "2026-10-20T00:00:00Z", "merge_commit_sha": "1" * 40, "body": self.body(0)},
                  {"number": 2, "author": "bot", "merged_at": "2026-10-21T00:00:00Z", "merge_commit_sha": "2" * 40, "body": self.body(0)},
                  {"number": 3, "author": "dev", "merged_at": "2026-10-21T00:00:00Z", "merge_commit_sha": "3" * 40, "body": self.body(0)},
                  {"number": 4, "author": "bot", "merged_at": "2026-10-27T00:00:00Z", "merge_commit_sha": "4" * 40, "body": self.body(1, hotfix_of=2)}]
        reverts = [("r" * 40, ts, "Revert \"x\"\n\nThis reverts commit " + "1" * 40 + "."), ("q" * 40, ts, "This reverts commit " + "3" * 40)]
        r = killswitch.evaluate(0, "bot", now, merged, reverts, [])
        self.assertEqual((r["count"], r["demoted"], r["open_issue"], r["prs"]), (2, True, True, [1, 2]))
        self.assertEqual(killswitch.evaluate(0, "dev", now, merged, reverts, [])["count"], 1)
        self.assertEqual(killswitch.evaluate(1, "bot", now, merged, reverts, [])["count"], 0)
        closed = [{"number": 9, "state": "closed", "closed_at": "2026-10-27T12:00:00Z", "body": killswitch.scope_marker(0, "bot")}]
        self.assertEqual(killswitch.evaluate(0, "bot", now, merged, reverts, closed)["count"], 0)
        opened = [{"number": 9, "state": "open", "closed_at": None, "body": killswitch.scope_marker(0, "bot")}]
        r = killswitch.evaluate(0, "bot", now, merged, [], opened)
        self.assertEqual((r["demoted"], r["open_issue"]), (True, False))


class ReplyLineTest(unittest.TestCase):
    def test_parse_and_trust(self):
        self.assertEqual(fm.parse_reply("thanks\nagentive-reply: F-1 wrong the guard is in caller.py:3"), ("F-1", "wrong", "the guard is in caller.py:3"))
        self.assertIsNone(fm.parse_reply("agentive-reply: F-1 intentional"))            # sentence required
        self.assertEqual(fm.parse_reply("agentive-reply: F-1 fixed"), ("F-1", "fixed", ""))
        root = {"id": 10, "body": "x\n<!-- agentive-finding: F-1 -->\n<!-- agentive-finding-data: {\"severity\": \"important\", \"round\": 1} -->", "path": "a.py", "line": 3}
        comments = [root,
                    {"id": 11, "in_reply_to_id": 10, "body": "agentive-reply: F-1 wrong reason here", "author_association": "NONE"},
                    {"id": 12, "in_reply_to_id": 10, "body": "agentive-reply: F-2 wrong id mismatch", "author_association": "OWNER"},
                    {"id": 13, "in_reply_to_id": 10, "body": "agentive-reply: F-1 not_important cosmetic only", "author_association": "MEMBER", "user": {"login": "m"}}]
        posted = fm.posted_findings(comments)
        self.assertEqual(posted["F-1"]["replies"], [(13, "not_important", "cosmetic only", "m")])
        f = fm.attach_responses([finding("F-1")], posted)[0]
        self.assertEqual(f["response"], {"kind": "replied_resolved", "reason": "not_important", "source": "reply_line", "comment_id": 13})

    def test_evidence_filter_and_stable_ids(self):
        out = {"findings": [{"file": "a.py", "line": 3, "trigger": "x", "summary": "s", "bug_class": "Null deref"},
                            {"file": "a.py", "summary": "no line"}, {"file": "b.py", "line": 1, "summary": "no trigger"}]}
        got, _, prob = fm.clean_reviewer_output(out, "correctness-reviewer")
        self.assertEqual(len(got), 1)
        self.assertIn("dropped 2", prob)
        again, _, _ = fm.clean_reviewer_output({"findings": [{"file": "a.py", "line": 9, "trigger": "y", "summary": "t", "bug_class": "null deref"}]}, "correctness-reviewer")
        self.assertEqual(got[0]["id"], again[0]["id"])

    def test_coordinator_cannot_drop_findings(self):
        a, b = finding("F-a"), finding("F-b", verdict="inconclusive")
        merged, prob = fm.coordinate([a, b], {"findings": [{"id": "F-a", "merged_from": ["F-b"], "severity": "important"}]})
        self.assertIsNone(prob)
        self.assertEqual((len(merged), merged[0]["merged_from"], merged[0]["verification"]["verdict"]), (1, ["F-b"], "confirmed"))
        same, prob = fm.coordinate([a, b], {"findings": [{"id": "F-a"}]})
        self.assertIsNotNone(prob)
        self.assertEqual(len(same), 2)


class PrBodyTest(unittest.TestCase):
    OK = ("## Explainer\n\nWhat changes (a.py:3).\n\n### Before\n\n```mermaid\nflowchart LR\n a-->b\n```\n\n### After\n\n"
          "```mermaid\nflowchart LR\n a-->c\n```\n\n## Verification\n\n- test -> 0\n\n## Risk\n\nlow\n\n## How to revert\n\ngit revert\n")

    def test_headings_match_schema(self):
        s = json.loads(h.read(os.path.join(h.ROOT, "design", "schemas", "pr-body-agentive-review.schema.json")))
        self.assertEqual(pr_body.ALL_TIERS, s["x-agentive-body-headings"]["all_tiers"])
        self.assertEqual(pr_body.EXPLAINER, s["x-agentive-body-headings"]["tier2_explainer"]["heading"])

    def test_explainer_presence(self):
        self.assertEqual(pr_body.explainer(self.OK)[1], [])
        self.assertEqual(pr_body.explainer("## Verification\n")[1], ["no_section"])
        self.assertIn("not_first", pr_body.explainer("## Verification\n\n" + self.OK)[1])
        self.assertIn("after_no_mermaid", pr_body.explainer(self.OK.replace("flowchart LR\n a-->c", "x").replace("```mermaid\nx", "```\nx"))[1])
        self.assertIn("no_before", pr_body.explainer("## Explainer\n\n### After\n\n```mermaid\nx\n```\n")[1])
        fenced = "```\n## Explainer\n```\n## Verification\n"
        self.assertEqual(pr_body.explainer(fenced)[1], ["no_section"])
        self.assertEqual(pr_body.all_tier_headings(self.OK), [])
        self.assertTrue(pr_body.all_tier_headings("## Risk\n## Verification\n## How to revert\n"))

    def test_independence(self):
        blk = {"implementer": {"tool": "claude_code", "model": "m", "session_id": "S1", "started_at": "2026-10-28T00:00:00Z"},
               "reviews": [{"role": "reviewer", "tool": "codex", "model": "m", "session_id": "S2", "context": "fresh", "source": "codex-review", "verdict": "pass"}]}
        self.assertEqual(h.schema_validator("pr-body-agentive-review")({"agentive-review": blk}), [])
        body = self.OK + "\n```yaml agentive-review\n" + ayaml.dumps({"agentive-review": blk}) + "```\n"
        self.assertEqual(independence.check(body), (True, []))
        blk["reviews"][0]["session_id"] = "S1"
        bad = self.OK + "\n```yaml agentive-review\n" + ayaml.dumps({"agentive-review": blk}) + "```\n"
        self.assertFalse(independence.check(bad)[0])
        self.assertFalse(independence.check("## Verification\n")[0])

    def test_with_pipeline_keeps_the_rest_of_the_body(self):
        blk = {"implementer": {"tool": "claude_code", "model": "m", "session_id": "S1", "started_at": "2026-10-28T00:00:00Z"}, "reviews": []}
        body = self.OK + "\n```yaml agentive-review\n" + ayaml.dumps({"agentive-review": blk}) + "```\n\ntrailer\n"
        pipe = {"classification": {"classes": ["docs"], "method": "deterministic"}, "tier": {"value": 0, "reasons": ["tier0"]},
                "verified_findings": [], "risk_score": {"score": None, "reason": "not_tier1"},
                "gate": {"decision": "pass", "check": "agentive-review-gate", "auto_merge": True, "head_sha": "a" * 40}}
        new = pr_body.with_pipeline(body, pipe, 1, [{"role": "reviewer", "tool": "claude_code", "model": "inherit", "session_id": "S3",
                                                     "context": "fresh", "source": "ci", "verdict": "pass", "reviewer_id": "correctness-reviewer"}])
        self.assertTrue(new.startswith(self.OK))
        self.assertTrue(new.endswith("trailer\n"))
        got, _ = pr_body.block(new)
        self.assertEqual(h.schema_validator("pr-body-agentive-review")({"agentive-review": got}), [])
        self.assertIsNone(pr_body.with_pipeline("no block", pipe))


class EvalScoringTest(unittest.TestCase):
    POS = {"id": "p1", "pr": "#1", "label": "positive", "origin": {"kind": "revert", "ref": "#2"},
           "expected_findings": [{"file": "a.py", "line_from": 10, "line_to": 12}]}
    NEG = {"id": "n1", "pr": "#3", "label": "negative", "forbidden_findings": [{"file": "b.py", "line_from": 1, "line_to": 5}]}

    def test_case_schema_agrees(self):
        v = h.schema_validator("reviewer-eval-case")
        for c in (self.POS, self.NEG, {"id": "n2", "pr": "a..b", "label": "negative"}):
            self.assertEqual(v(c), [])
            self.assertEqual(evals.validate_case(c, c["id"]), [])
        bad = dict(self.POS)
        bad.pop("origin")
        self.assertTrue(v(bad))
        self.assertTrue(evals.validate_case(bad, "p1"))

    def summary(self, base_runs, cand_runs, n=3):
        s = evals.summarize([self.POS, self.NEG], base_runs, cand_runs, n, "b" * 40, "c" * 40)
        self.assertEqual(h.validate_ref("https://fw.local/schemas/reviewer-eval-case.schema.json#/$defs/summary", s), [])
        return s

    def test_scoring_rules(self):
        hit = [{"file": "a.py", "line": 11, "instances": [], "blocking": True}]
        inst = [{"file": "z.py", "line": 1, "instances": [{"file": "a.py", "line": 12}], "blocking": True}]
        miss = [{"file": "a.py", "line": 30, "instances": [], "blocking": True}]
        noisy = [{"file": "b.py", "line": 3, "instances": [], "blocking": False}]
        self.assertTrue(evals.run_result(self.POS, hit)[0])
        self.assertTrue(evals.run_result(self.POS, inst)[0])
        self.assertFalse(evals.run_result(self.POS, miss)[0])
        self.assertFalse(evals.run_result(self.NEG, noisy)[0])
        self.assertTrue(evals.run_result(self.NEG, [])[0])
        self.assertFalse(evals.run_result(self.POS, None)[0])
        s = self.summary({"p1": [hit, hit, miss], "n1": [[], [], []]}, {"p1": [miss, miss, hit], "n1": [[], [], []]})
        self.assertEqual((s["newly_missed"], s["adopt_suggested"], s["reason"]), (["p1"], False, "newly_missed"))
        s = self.summary({"p1": [miss, miss, miss], "n1": [noisy, [], []]}, {"p1": [hit, hit, hit], "n1": [[], [], []]})
        self.assertEqual((s["newly_caught"], s["reason"], s["adopt_suggested"]), (["p1"], "improved", True))
        s = self.summary({"p1": [hit] * 3, "n1": [[]] * 3}, {"p1": [hit] * 3, "n1": [[]] * 3})
        self.assertEqual(s["reason"], "no_regression")
        s = self.summary({"p1": [hit] * 3, "n1": [[]] * 3}, {"p1": [hit] * 3, "n1": [noisy] * 3})
        self.assertEqual((s["newly_noisy"], s["reason"], s["adopt_suggested"]), (["n1"], "no_gain", False))
        only_pos = evals.summarize([self.POS], {"p1": [hit] * 3}, {"p1": [hit] * 3}, 3, "b", "c")
        self.assertEqual(only_pos["reason"], "insufficient_cases")
        text = evals.comment(only_pos, ["code_review.md"])
        self.assertIn("reviewer eval cases insufficient — change not verified", text)
        self.assertIn("adopt_suggested: false — merge only with 'eval-override: <reason>' in the PR body", text)
        self.assertIn(evals.MARKER, text)
        block = util.fenced_blocks(text, "yaml agentive-reviewer-eval")[0]
        self.assertEqual(ayaml.loads(block), only_pos)


if __name__ == "__main__":
    unittest.main()
