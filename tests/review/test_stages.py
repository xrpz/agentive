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


def cfg_key(e):
    from agentive.review import secpaths
    return secpaths.key(e)


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
        text = h.rules_region_text() if os.path.exists(RULES_REGION) else None
        if text is None:
            self.skipTest("C's code_review.md#rules region not present yet")
        loaded = cfg.load_rules(lambda p: text)
        self.assertEqual(loaded.problems, [])
        doc = ayaml.loads(util.fenced_blocks(text, "yaml agentive-review-rules")[0])
        self.assertEqual(h.schema_validator("review-rules")(doc), [])
        globs = {e["glob"] for e in loaded.value["trust_boundary"]["never_ai_approve"] if "glob" in e}
        self.assertTrue({e["glob"] for e in cfg.DEFAULT_RULES["trust_boundary"]["never_ai_approve"] if "glob" in e} <= globs)
        # the shipped block already holds the floor (tokens included): it equals the framework floor
        self.assertEqual({cfg_key(e) for e in loaded.value["trust_boundary"]["never_ai_approve"]}, cfg.floor_keys())
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

    def test_ci_tool_key_and_resolution(self):
        """CITOOL: review.pipeline.ci_tool is claude | codex; absent = manifest framework.init_tool; else claude."""
        self.assertEqual(cfg.validate_config({"ci_tool": "codex"}), [])
        self.assertEqual(cfg.validate_config({"ci_tool": "claude"}), [])
        self.assertTrue(cfg.validate_config({"ci_tool": "both"}))
        self.assertEqual(h.schema_validator("review-config")({"ci_tool": "codex"}), [])
        self.assertTrue(h.schema_validator("review-config")({"ci_tool": "both"}))
        fw = lambda t: {"framework": {"plugin": "agentive", "version": "0.1.0", "init_tool": t}}   # noqa: E731
        self.assertEqual(cfg.ci_tool({}, {}), "claude")                                            # manifest written before CITOOL
        self.assertEqual(cfg.ci_tool(None, fw("codex")), "codex")
        self.assertEqual(cfg.ci_tool({"ci_tool": "claude"}, fw("codex")), "claude")                # the key wins
        self.assertEqual(cfg.ci_tool({"ci_tool": "codex"}, fw("claude")), "codex")
        self.assertEqual(cfg.ci_tool({"ci_tool": "gemini"}, fw("codex")), "codex")                 # invalid value: the init tool
        self.assertEqual(cfg.ci_tool({}, fw("gemini")), "claude")
        self.assertEqual(cfg.load_project_config(lambda p: "review:\n  pipeline:\n    ci_tool: codex\n" if p == ".agents/framework.yaml" else None).value["ci_tool"], "codex")
        bad = cfg.load_project_config(lambda p: "review:\n  pipeline:\n    ci_tool: both\n" if p == ".agents/framework.yaml" else None)
        self.assertTrue(bad.problems)
        self.assertNotIn("ci_tool", bad.value)

    def test_e2e_12_ci_auth_key_and_credentials(self):
        """E2E-12 / CIAUTH (user 2026-10-30): claude takes the API key, the subscription OAuth token, or both (default)."""
        for v in ("both", "api_key", "oauth_token"):
            self.assertEqual(cfg.validate_config({"ci_auth": v}), [])
            self.assertEqual(h.schema_validator("review-config")({"ci_auth": v}), [])
        self.assertTrue(cfg.validate_config({"ci_auth": "token"}))
        self.assertTrue(h.schema_validator("review-config")({"ci_auth": "token"}))
        self.assertEqual(cfg.ci_auth({}, "claude"), "both")
        self.assertEqual(cfg.ci_auth({"ci_auth": "oauth_token"}, "claude"), "oauth_token")
        self.assertEqual(cfg.ci_auth({"ci_auth": "token"}, "claude"), "both")                       # invalid: the default
        self.assertEqual(cfg.ci_auth({"ci_auth": "oauth_token"}, "codex"), "api_key")              # codex: its API key only
        names = lambda tool, auth: [(k, c["secret"], c["input"]) for k, c in cfg.ci_credentials(tool, auth)]   # noqa: E731
        self.assertEqual(names("claude", "both"), [("api_key", "ANTHROPIC_API_KEY", "anthropic_api_key"),
                                                   ("oauth_token", "CLAUDE_CODE_OAUTH_TOKEN", "claude_code_oauth_token")])
        self.assertEqual(names("claude", "oauth_token"), [("oauth_token", "CLAUDE_CODE_OAUTH_TOKEN", "claude_code_oauth_token")])
        self.assertEqual(names("codex", "api_key"), [("api_key", "OPENAI_API_KEY", "openai-api-key")])
        from agentive.review import settings
        files = {".agents/framework.yaml": "review:\n  pipeline:\n    ci_auth: oauth_token\n"}
        out = "\n".join(settings.effective(lambda p: files.get(p)))
        self.assertIn("review.pipeline.ci_auth: oauth_token   [.agents/framework.yaml]", out)
        self.assertIn("pass the repository secret(s) CLAUDE_CODE_OAUTH_TOKEN", out)
        out = "\n".join(settings.effective(lambda p: None))
        self.assertIn("review.pipeline.ci_auth: both   [framework default]", out)
        self.assertIn("ANTHROPIC_API_KEY and CLAUDE_CODE_OAUTH_TOKEN; OAuth token first: ANTHROPIC_API_KEY is passed only when CLAUDE_CODE_OAUTH_TOKEN is empty", out)

    def test_rubric_override_merge_and_invalid(self):
        preset = ayaml.dumps(cfg.DEFAULT_RUBRIC)
        over = "extends: agentive:rubric@2\nitems:\n  - {id: recent_churn, enabled: false}\n  - {id: size, weight: 10}\n"
        eff = cfg.effective_rubric(lambda p: {".agents/rubric.yaml": preset, ".agents/custom/.agents/rubric.yaml": over}.get(p))
        self.assertTrue(eff.valid)
        by = {i["id"]: i for i in eff.value["items"]}
        self.assertFalse(by["recent_churn"]["enabled"])
        self.assertEqual(by["size"]["weight"], 10)
        self.assertEqual(h.schema_validator("rubric")(ayaml.loads(over)), [])
        bad = cfg.effective_rubric(lambda p: {".agents/rubric.yaml": preset, ".agents/custom/.agents/rubric.yaml": "extends: agentive:rubric@2\n"}.get(p))
        self.assertFalse(bad.valid)                          # extends the preset in force and changes nothing
        bad = cfg.effective_rubric(lambda p: {".agents/rubric.yaml": preset, ".agents/custom/.agents/rubric.yaml": "extends: agentive:rubric@9\nhuman_if_score_gt: 20\n"}.get(p))
        self.assertFalse(bad.valid)                          # not a framework preset
        c_rubric = os.path.join(h.SRC, "dot-agents", "rubric.yaml")
        if os.path.exists(c_rubric):
            got = cfg.effective_rubric(lambda p: h.read(c_rubric) if p == ".agents/rubric.yaml" else None)
            self.assertTrue(got.valid, got.problems)
            self.assertEqual(got.value, dict(cfg.DEFAULT_RUBRIC, items=[dict(i, enabled=True) for i in cfg.DEFAULT_RUBRIC["items"]]))
            self.assertEqual([(i["id"], i["weight"]) for i in got.value["items"]],
                             [("spread", 24), ("size", 30), ("incident_history", 30), ("change_type", 14), ("recent_churn", 14)])
            self.assertEqual(got.value["human_if_score_gt"], 28)

    def test_e2e_13_rubric1_stays_pinnable(self):
        """E2E-13: an override whose extends names agentive:rubric@1 is merged onto @1 even when .agents/rubric.yaml holds @2
        (alone it pins @1; with items it changes @1)."""
        preset = ayaml.dumps(cfg.RUBRIC_PRESETS["agentive:rubric@2"])
        for over, size_w in (("extends: \"agentive:rubric@1\"\n", 24), ("extends: agentive:rubric@1\nitems:\n  - {id: size, weight: 10}\n", 10)):
            self.assertEqual(h.schema_validator("rubric")(ayaml.loads(over)), [])
            eff = cfg.effective_rubric(lambda p: {".agents/rubric.yaml": preset, ".agents/custom/.agents/rubric.yaml": over}.get(p))
            self.assertTrue(eff.valid, eff.problems)
            self.assertEqual(eff.value["preset"], "agentive:rubric@1")
            by = {i["id"]: i for i in eff.value["items"]}
            self.assertEqual((by["spread"]["input"], by["recent_churn"]["input"], by["size"]["weight"]),
                             ("modules_touched_or_shared_path", "recent_churn_30d", size_w))
        old = cfg.effective_rubric(lambda p: {".agents/rubric.yaml": ayaml.dumps(cfg.RUBRIC_PRESETS["agentive:rubric@1"])}.get(p))
        self.assertEqual(old.value["preset"], "agentive:rubric@1")         # an install that was not updated keeps @1


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
    def score(self, preset="agentive:rubric@1", **sig):
        lines = sig.pop("lines", 10)
        classes = sig.pop("classes", ["refactor"])
        s = {"modules_touched": ["a"], "shared_path_touched": False, "incident_history_90d": False, "recent_churn_30d": False,
             "production_modules_touched": ["a"], "production_shared_path_touched": False, "production_churn_commits_90d": 0}
        s.update(sig)
        eff = cfg.effective_rubric(lambda p: ayaml.dumps(cfg.RUBRIC_PRESETS[preset]) if p == ".agents/rubric.yaml" else None)
        return rv7b.run(1, "on", cfg.Loaded(eff.value), s, classes, lines)

    def test_e2e_13_rubric2_scores(self):
        """E2E-13 (rubric@2): the E2E PRs and the Fable cases. A feature with its test scores 0; tests and docs are not
        spread; a single-module 501..1000 line change goes to a human (30); dependency + hot file is 28 (auto)."""
        sc = lambda **k: self.score(preset="agentive:rubric@2", **k)["score"]       # noqa: E731
        # E2E PR #2 (item_count + its test): rubric@1 counted tests/ as a second module and the feature type
        self.assertEqual(self.score(modules_touched=["src", "tests"], classes=["feature"], recent_churn_30d=True)["score"], 52)
        self.assertEqual(sc(modules_touched=["src", "tests"], production_modules_touched=["src"], classes=["feature"], recent_churn_30d=True), 0)
        self.assertEqual(sc(classes=["fix"]), 0)                                                  # no penalty by type
        self.assertEqual(sc(classes=["deps"]), 14)
        self.assertEqual(sc(production_modules_touched=["src/a", "src/b"]), 24)
        self.assertEqual(sc(production_modules_touched=["src/a", "src/b"], production_churn_commits_90d=5), 38)
        self.assertEqual(sc(shared_path_touched=True), 0)                                          # tests/common/ is not production
        self.assertEqual(sc(production_shared_path_touched=True), 24)
        self.assertEqual(sc(lines=500), 0)
        self.assertEqual(sc(lines=501), 30)                                                        # >500 alone -> human
        self.assertEqual(sc(incident_history_90d=True), 30)
        self.assertEqual(sc(production_churn_commits_90d=4), 0)
        self.assertEqual(sc(production_churn_commits_90d=5, classes=["deps"]), 28)                # 14+14 can auto-merge
        r = self.score(preset="agentive:rubric@2")
        self.assertEqual((r["preset"], r["human_if_score_gt"]), ("agentive:rubric@2", 28))

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
                "rv7b": {"score": None, "reason": "not_tier1"}, "auto_merge": "low_risk", "config_valid": True,
                "explainer": None, "chain": {"result": "not_chain"}, "review_round": {"round": 1, "mode": "full"},
                "pending_verbs": [], "signals": {}, "llm_problems": [], "head_sha": "a" * 40, "trigger": "synchronize",
                "kill_switch": None, "source": "dev"}
        base.update(kw)
        out = rv8.decide(base)
        out.pop("_kill_switch_result", None)
        return out

    def test_pending_required_verb_names_the_two_exits(self):
        """PENDINGNOTICE (V2-23): human_required with the verb named, the RV8 rule kept, and both ways out in the reason."""
        r = self.decide(pending_verbs=["typecheck", "test"], required_checks=["lint", "typecheck", "test"], require_explicit=True)
        self.assertEqual((r["decision"], r["auto_merge"]), ("human_required", False))
        self.assertIn("pending_verbs", r["reason_codes"])
        detail = next(x for x in r["reasons"] if x.startswith("pending_verbs: "))
        self.assertIn("typecheck, test", detail)
        self.assertIn("required checks typecheck, test have no command (you required them in .agents/framework.yaml)", detail)
        self.assertIn("Give the command", detail)
        # E2E-02: one verb takes "has", several take "have" (gate reason, report sentence, Stop tail)
        one = next(x for x in self.decide(pending_verbs=["typecheck"], required_checks=["typecheck"], require_explicit=True)["reasons"]
                   if x.startswith("pending_verbs: "))
        self.assertIn("required check typecheck has no command (you required it in .agents/framework.yaml), so nothing verifies it.", one)
        from agentive import common
        from agentive.review import config as cfgmod
        self.assertEqual(cfgmod.unavailable_text(["typecheck"]), "typecheck has no command -> give the command or run /agentive:update")
        self.assertTrue(cfgmod.unavailable_text(["lint", "typecheck"]).startswith("lint, typecheck have no command"))
        self.assertEqual(common.no_command(["lint", "test"], ","), "lint,test have no command")
        self.assertIn("remove them from review.pipeline.stages.RV1.require_checks in .agents/framework.yaml", detail)
        # the exit that removes the verb from the required checks lets the PR through again
        self.assertEqual(self.decide(pending_verbs=["typecheck"], required_checks=["lint", "test"], require_explicit=True)["decision"], "pass")

    def test_gate_comment_carries_the_pending_sentence(self):
        from agentive.review import post
        r = self.decide(pending_verbs=["typecheck"], required_checks=["lint", "typecheck", "test"], require_explicit=True)
        report = {"head_sha": "a" * 40, "rv3": {"classes": ["fix"], "method": "rules"}, "rv4": {"tier": 0, "reasons": ["tier0"]},
                  "rv7b": {"score": None, "reason": "not_tier1"}, "review_round": {"round": 1, "mode": "full"}, "findings": [], "rv8": r}
        text = post.summary_comment(report, 0, {})
        # COMMENTUX: one plain Why line; the way out is the What to do line; the setting is in Details
        self.assertIn("- You required typecheck in .agents/framework.yaml, but this repo has no command for it.", text)
        self.assertIn("review.pipeline.stages.RV1.require_checks", text.split("<details>")[0])
        self.assertIn("- Auto-merge setting `low_risk` (framework default)", text)

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
        self.assertEqual(self.decide(auto_merge="off")["decision"], "human_required")
        self.assertEqual(self.decide(auto_merge="off")["reason_codes"], ["auto_merge_off"])
        # DEFAULTS: the default is low_risk (U1=C); the key absent from the input reads as the framework default
        self.assertEqual(self.decide()["auto_merge_setting"], "low_risk")
        # dry_run merges nothing, not even tier 0, and says what it would have done
        r = self.decide(auto_merge="dry_run")
        self.assertEqual((r["decision"], r["auto_merge"], r.get("would_auto_merge"), r["reason_codes"]), ("human_required", False, True, ["would_auto_merge"]))
        self.assertEqual(self.report(r, 0), [])
        # low_risk merges tier 0 and labels tier 1; low_and_medium_risk merges both
        self.assertTrue(self.decide(auto_merge="low_and_medium_risk")["auto_merge"])

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
        self.assertEqual(self.decide(pending_verbs=["test"], require_explicit=True)["decision"], "human_required")
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
        self.assertEqual((r["decision"], r.get("would_auto_merge"), r["auto_merge_setting"]), ("human_required", True, "low_risk"))
        self.assertEqual(self.report(r, 1, rv7b_=rv7b_ok), [])
        r = self.decide(auto_merge="low_and_medium_risk", **t1)
        self.assertEqual((r["decision"], r["auto_merge"], r["auto_merge_setting"]), ("pass", True, "low_and_medium_risk"))
        self.assertEqual(self.report(r, 1, rv7b_=rv7b_ok), [])
        high = dict(rv7b_ok, score=38)
        self.assertIn("score_above_threshold", self.decide(tier=1, rv4_reasons=["tier1"], rv7b=high, auto_merge="low_and_medium_risk")["reason_codes"])
        r = self.decide(auto_merge="low_and_medium_risk", kill_switch=lambda t: {"count": 2, "demoted": True}, **t1)
        self.assertEqual((r["decision"], r["demoted"], r["kill_switch"]["count"]), ("human_required", True, 2))
        self.assertEqual(self.report(r, 1, rv7b_=rv7b_ok), [])
        self.assertEqual(self.decide(auto_merge="off", **t1)["reason_codes"], ["auto_merge_off"])


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

    def test_e2e_13_production_signals_and_first_parent_churn(self):
        """E2E-13 (rubric@2): production modules and shared paths leave tests and docs out; churn counts the changes landed
        on the base (first-parent records within 90 days per changed production file, merge commits included since
        2026-10-31 [user]): a branch merged with a merge commit counts 1 through its merge, its own commits add nothing;
        a merged revert PR is still an incident through its merge message."""
        from agentive.review import gitio, rv1
        r = h.Repo({"src/a.py": "x=0\n", "src/b.py": "y=0\n", "tests/common/conftest.py": "c=0\n", "docs/x.md": "d\n"})
        D = lambda d: f"2026-{d}T00:00:00Z"      # noqa: E731
        try:
            r.commit("init", date=D("06-01"))
            for i in range(4):                                     # 4 direct commits on main within 90 days
                r.write({"src/a.py": f"x={i + 1}\n"})
                r.commit(f"edit {i}", date=D(f"09-0{i + 1}"))
            h.git(r.dir, "checkout", "-q", "-b", "side")
            for i in range(3):                                     # 3 commits on a branch, merged with a merge commit
                r.write({"src/a.py": f"x=side{i}\n", "tests/common/conftest.py": f"c={i}\n"})
                r.commit(f"side {i}", date=D(f"09-1{i}"))
            h.git(r.dir, "checkout", "-q", "main")
            h.git(r.dir, "merge", "-q", "--no-ff", "side", "-m", "Merge pull request #7 from o/revert-6-x", "-m", 'Revert "x"',
                  env={"GIT_AUTHOR_DATE": D("09-20"), "GIT_COMMITTER_DATE": D("09-20")})
            base = h.git(r.dir, "rev-parse", "HEAD")
            h.git(r.dir, "checkout", "-q", "-b", "f")
            r.write({"src/a.py": "x=f\n", "tests/common/conftest.py": "c=f\n", "docs/x.md": "e\n", "tests/test_a.py": "t\n"})
            head = r.commit("f", date=D("10-01"))
            d = gitio.Diff(r.dir, base, head)
            s = rv1.signals(d, d.name_status(), d.hunks(), {}, ["**/shared/**", "**/common/**"], rv1.TEST_GLOBS, base, [])
            self.assertEqual(s["modules_touched"], ["docs", "src", "tests"])
            self.assertEqual(s["production_modules_touched"], ["src"])
            self.assertEqual((s["shared_path_touched"], s["production_shared_path_touched"]), (True, False))
            self.assertEqual(s["production_churn_commits_90d"], 5)          # 4 direct commits + the merged PR; side commits not counted
            self.assertTrue(s["incident_history_90d"])                      # the merge message carries the revert PR title
            r.write({"src/a.py": "x=g\n"})
            more = r.commit("one more on f", date=D("10-02"))
            h.git(r.dir, "checkout", "-q", "main")
            r.write({"src/a.py": "x=5\n"})
            base2 = r.commit("edit 5", date=D("09-25"))
            h.git(r.dir, "checkout", "-q", "f")
            d = gitio.Diff(r.dir, base2, more)
            s = rv1.signals(d, d.name_status(), d.hunks(), {}, [], rv1.TEST_GLOBS, base2, [])
            self.assertEqual(s["production_churn_commits_90d"], 6)          # one more direct commit
        finally:
            r.cleanup()


SHIPPED_RUBRIC = os.path.join(h.SRC, "dot-agents", "rubric.yaml")


class ChurnLandingTest(unittest.TestCase):
    """E2E-13 revised 2026-10-31 [user "1번으로 확정해"]: rubric@2 recent_churn counts the changes landed on the base branch:
    first-parent history WITH merge commits, so one merged PR, one squash commit or one direct commit counts 1 (a
    rebase-merged PR counts one per commit). Histories are built like the GitHub merge methods: merge commit (--no-ff with
    the 'Merge pull request #N from o/<branch>' subject and the PR title as body), squash (one commit 'title (#N)') and
    rebase-merge (the PR commits put onto the base one by one)."""

    def setUp(self):
        self.r = h.Repo({"src/a.py": "a=0\n", "src/b.py": "b=0\n", "tests/test_a.py": "t=0\n"})
        self.day, self.pr = 0, 0
        self.r.commit("init", date="2026-01-01T00:00:00Z")   # outside the 90-day window of every base below (about 2026-08)

    def tearDown(self):
        self.r.cleanup()

    def date(self):
        d = datetime.datetime(2026, 7, 1, tzinfo=datetime.timezone.utc) + datetime.timedelta(days=self.day)
        self.day += 1
        return d.strftime("%Y-%m-%dT%H:%M:%SZ")

    def land(self, style, commits=3, path="src/a.py", title="Change a"):
        """One PR of `commits` commits (each also edits its test) landed on main with the GitHub method `style`."""
        r = self.r
        self.pr += 1
        br = f"pr{self.pr}"
        h.git(r.dir, "checkout", "-q", "-b", br, "main")
        for i in range(commits):
            r.write({path: f"{br}-{i}\n", "tests/test_a.py": f"{br}-{i}\n"})
            r.commit(f"{title}: slice {i}", date=self.date())
        h.git(r.dir, "checkout", "-q", "main")
        when = self.date()
        env = {"GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when}
        if style == "merge":
            h.git(r.dir, "merge", "-q", "--no-ff", br, "-m", f"Merge pull request #{self.pr} from o/{br}", "-m", title, env=env)
        elif style == "squash":
            h.git(r.dir, "merge", "-q", "--squash", br)
            r.commit(f"{title} (#{self.pr})", date=when)
        elif style == "rebase":
            h.git(r.dir, "merge", "-q", "--ff-only", br)
        else:
            raise ValueError(style)
        return h.git(r.dir, "rev-parse", "HEAD")

    def direct(self, msg="direct edit", path="src/a.py"):
        self.r.write({path: f"direct-{self.day}\n"})
        return self.r.commit(msg, date=self.date())

    def signals(self):
        """RV1 signals of a PR on top of main that edits src/a.py and its test."""
        from agentive.review import gitio, rv1
        r = self.r
        base = h.git(r.dir, "rev-parse", "main")
        h.git(r.dir, "checkout", "-q", "-b", f"f{self.day}", "main")
        r.write({"src/a.py": "a=f\n", "tests/test_a.py": "t=f\n"})
        head = r.commit("f", date=self.date())
        h.git(r.dir, "checkout", "-q", "main")
        d = gitio.Diff(r.dir, base, head)
        return rv1.signals(d, d.name_status(), d.hunks(), {}, [], rv1.TEST_GLOBS, base, [])

    def churn(self):
        return self.signals()["production_churn_commits_90d"]

    def rubric_hit(self, signals):
        """recent_churn of the shipped .agents/rubric.yaml (its hit text carries the threshold rv7b reads)."""
        with open(SHIPPED_RUBRIC, encoding="utf-8") as f:
            text = f.read()
        eff = cfg.effective_rubric(lambda p: text if p == ".agents/rubric.yaml" else None)
        res = rv7b.run(1, "on", cfg.Loaded(eff.value), signals, ["refactor"], 10)
        return [i for i in res["items"] if i["id"] == "recent_churn"][0]["hit"], res["score"]

    def test_e2e_13_churn_git_merge_lists_pr_files(self):
        """Pins the git behavior the count relies on (no added flag): with --first-parent --name-only a merge commit is
        listed and names the files its PR changed against the first parent; a merge whose PR did not touch a path is not
        listed for that path; the PR's own commits are not on the first-parent history."""
        from agentive.review import gitio
        r = self.r
        m1 = self.land("merge", commits=3, path="src/a.py", title="Change a")
        m2 = self.land("merge", commits=2, path="src/b.py", title="Change b")
        now = gitio.commit_time(r.dir, m2)
        recs = gitio.log_first_parent(r.dir, m2, ["src/a.py", "src/b.py"], now - 90 * 86400, now)
        self.assertEqual([(sha, len(par), files) for sha, par, _, _, _, files in recs],
                         [(m2, 2, ["src/b.py"]), (m1, 2, ["src/a.py"])])
        self.assertEqual([sha for sha, *_ in gitio.log_first_parent(r.dir, m2, ["src/a.py"], now - 90 * 86400, now)], [m1])
        self.assertEqual(recs[1][3:5], ("Merge pull request #1 from o/pr1", "Change a\n"))

    def test_e2e_13_churn_merge_commit_repo(self):
        self.land("merge", commits=4)
        self.assertEqual(self.churn(), 1)               # one merged PR = 1 (its 4 commits add nothing)
        for _ in range(4):
            self.land("merge", commits=2)
        s = self.signals()
        self.assertEqual(s["production_churn_commits_90d"], 5)
        self.assertEqual(self.rubric_hit(s), (True, 14))  # >= 5 hits; 14 alone can auto-merge
        self.assertFalse(s["incident_history_90d"])     # 'Merge pull request #N from o/<branch>' alone is no incident

    def test_e2e_13_churn_squash_repo(self):
        self.land("squash", commits=4)
        self.assertEqual(self.churn(), 1)               # one squash commit = 1
        for _ in range(3):
            self.land("squash", commits=2)
        s = self.signals()
        self.assertEqual(s["production_churn_commits_90d"], 4)
        self.assertEqual(self.rubric_hit(s), (False, 0))

    def test_e2e_13_churn_rebase_merge_repo(self):
        """Accepted cost: git alone cannot group rebase-merged commits into their PR, so each counts 1."""
        self.land("rebase", commits=3)
        self.assertEqual(self.churn(), 3)

    def test_e2e_13_churn_mixed_history(self):
        """The default mixed history: the gate squashes tier 0/1 PRs, a human merges a tier 2 PR with the merge button,
        plus a direct commit and a merged PR on another file. Each landing on src/a.py counts 1."""
        self.land("squash", commits=4)
        self.land("squash", commits=3)
        self.land("merge", commits=6)
        self.land("merge", commits=2, path="src/b.py", title="Change b")
        self.direct()
        s = self.signals()
        self.assertEqual(s["production_churn_commits_90d"], 4)
        self.assertFalse(s["incident_history_90d"])
        self.land("merge", commits=1)
        self.assertEqual(self.churn(), 5)

    def test_e2e_13_churn_incident_history_unchanged(self):
        """[설계 판단] incident_history reads the same first-parent records as before the revision: a squash or direct
        commit by its own message, a merge commit by its message (subject and the PR title in its body); the generic merge
        subject is no incident and a hotfix commit inside a merged branch is seen only through the merge message."""
        self.land("merge", commits=2, title="Add feature")
        h.git(self.r.dir, "checkout", "-q", "-b", "inner", "main")
        self.r.write({"src/a.py": "inner\n"})
        self.r.commit("hotfix: inner commit", date=self.date())
        h.git(self.r.dir, "checkout", "-q", "main")
        when = self.date()
        h.git(self.r.dir, "merge", "-q", "--no-ff", "inner", "-m", "Merge pull request #9 from o/inner", "-m", "Tidy a",
              env={"GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when})
        s = self.signals()
        self.assertEqual((s["incident_history_90d"], s["production_churn_commits_90d"]), (False, 2))
        self.land("squash", commits=2, title="Hotfix crash on empty input")
        self.assertTrue(self.signals()["incident_history_90d"])        # squash commit subject 'Hotfix ... (#N)'
        r2 = ChurnLandingTest("test_e2e_13_churn_incident_history_unchanged")
        r2.setUp()
        try:
            r2.direct('Revert "Change a"\n\nThis reverts commit ' + "b" * 40 + ".")
            self.assertTrue(r2.signals()["incident_history_90d"])      # direct revert commit
            r2.land("merge", commits=1, title='Revert "Change a"')
            s2 = r2.signals()
            self.assertEqual((s2["incident_history_90d"], s2["production_churn_commits_90d"]), (True, 2))
        finally:
            r2.tearDown()


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

    def test_evidence_filter(self):
        out = {"findings": [{"file": "a.py", "line": 3, "trigger": "x", "summary": "s", "bug_class": "Null deref"},
                            {"file": "a.py", "summary": "no line"}, {"file": "b.py", "line": 1, "summary": "no trigger"}]}
        got, _, prob = fm.clean_reviewer_output(out, "correctness-reviewer")
        self.assertEqual(len(got), 1)
        self.assertIn("dropped 2", prob)
        self.assertEqual((got[0]["bug_class"], got[0]["pattern"]), ("other", "Null deref"))   # FINDINGID: free text is only a pattern

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

    LOCAL = {"role": "reviewer", "tool": "codex", "model": "m", "session_id": "S2", "context": "unverified",
             "executed_by": "unknown", "attestation": "self_reported", "source": "precommit-review", "verdict": "pass"}

    def pr(self, blk=None, body=None, **kw):
        if body is None:
            body = self.OK + "\n```yaml agentive-review\n" + ayaml.dumps({"agentive-review": blk}) + "```\n"
        return dict({"number": 5, "author": "dev", "author_type": "User", "head_sha": "a" * 40, "body": body}, **kw)

    def ci_review(self, sha="a" * 40, **kw):
        return dict({"role": "reviewer", "tool": "claude_code", "source": "ci", "executed_by": "process", "attestation": "ci_job",
                     "context": "isolated", "run": {"run_id": "9", "run_attempt": "1", "sha": sha}}, **kw)

    def test_independence(self):
        blk = {"implementer": {"tool": "claude_code", "model": "m", "session_id": "S1", "started_at": "2026-10-28T00:00:00Z"},
               "reviews": [dict(self.LOCAL)]}
        self.assertEqual(h.schema_validator("pr-body-agentive-review")({"agentive-review": blk}), [])
        # a review the CI job produced for this head passes; the local record is only shown
        got = independence.evaluate(self.pr(blk), [self.ci_review()])
        self.assertEqual((got["result"], got["problems"]), ("ok", []))
        self.assertTrue(got["notes"] and "attested locally" in got["notes"][0])
        # the PR body is written by the implementer's side: a local record never satisfies the CI rule, whatever it says
        got = independence.evaluate(self.pr(blk), [])
        self.assertEqual(got["result"], "fail")
        self.assertIn("not counted", got["problems"][0])
        # sha mismatch (a force-push after the review), a missing run id, and a non-ci source are not accepted
        self.assertEqual(independence.evaluate(self.pr(blk), [self.ci_review(sha="b" * 40)])["result"], "fail")
        bad_run = self.ci_review(); bad_run["run"] = {"sha": "a" * 40}
        self.assertEqual(independence.evaluate(self.pr(blk), [bad_run])["result"], "fail")
        self.assertEqual(independence.evaluate(self.pr(blk), [self.ci_review(source="precommit-review")])["result"], "fail")
        # a body entry claiming source ci with a matching run is still not counted
        forged = dict(self.LOCAL, source="ci", run={"run_id": "1", "sha": "a" * 40})
        blk2 = dict(blk, reviews=[forged])
        self.assertEqual(independence.evaluate(self.pr(blk2), [])["result"], "fail")
        # the always-true session-id comparison is gone: equal ids no longer fail
        blk3 = dict(blk, reviews=[dict(self.LOCAL, session_id="S1")])
        self.assertEqual(independence.evaluate(self.pr(blk3), [self.ci_review()])["result"], "ok")
        # format check on the local record
        blk4 = dict(blk, reviews=[dict(self.LOCAL, context="fresh")])
        got = independence.evaluate(self.pr(blk4), [self.ci_review()])
        self.assertEqual(got["result"], "fail")
        self.assertIn("context must be one of", got["problems"][0])
        # headings
        no_head = self.pr(blk, body="## Verification\n\n```yaml agentive-review\n" + ayaml.dumps({"agentive-review": blk}) + "```\n")
        self.assertEqual(independence.evaluate(no_head, [self.ci_review()])["result"], "fail")

    def test_independence_exempt(self):
        blk = {"implementer": {"tool": "claude_code", "model": "m", "session_id": "S1", "started_at": "2026-10-28T00:00:00Z"}, "reviews": []}
        # bot PR: by author type or a [bot] login, whatever the body carries
        for kw in ({"author_type": "Bot"}, {"author": "dependabot[bot]"}, {"author": "renovate[bot]", "author_type": "User"}):
            got = independence.evaluate(self.pr(blk, **kw), [])
            self.assertEqual((got["result"], got["code"]), ("exempt", independence.BOT_PR_EXEMPT), kw)
            self.assertEqual(independence.exemption(self.pr(blk, **kw))["code"], "bot_pr_exempt")
        # human PR without an agentive-review block: exempt from the heading check, routed to a human
        human = self.pr(body="## Summary\n\nplain\n")
        got = independence.evaluate(human, [])
        self.assertEqual((got["result"], got["code"]), ("exempt", independence.HUMAN_PR_EXEMPT))
        # an agent PR is never exempt
        self.assertIsNone(independence.exemption(self.pr(blk)))
        self.assertIsNone(independence.exemption(None))

    def test_accepted_ci_reviews(self):
        sha = "a" * 40
        good = self.ci_review()
        self.assertEqual(independence.accepted_ci_reviews([good, self.ci_review(sha="c" * 40), "junk", None], sha), [good])
        self.assertEqual(independence.accepted_ci_reviews([good], None), [])
        self.assertEqual(independence.accepted_ci_reviews(None, sha), [])

    def test_with_pipeline_keeps_the_rest_of_the_body(self):
        blk = {"implementer": {"tool": "claude_code", "model": "m", "session_id": "S1", "started_at": "2026-10-28T00:00:00Z"}, "reviews": []}
        body = self.OK + "\n```yaml agentive-review\n" + ayaml.dumps({"agentive-review": blk}) + "```\n\ntrailer\n"
        pipe = {"classification": {"classes": ["docs"], "method": "deterministic"}, "tier": {"value": 0, "reasons": ["tier0"]},
                "verified_findings": [], "risk_score": {"score": None, "reason": "not_tier1"},
                "gate": {"decision": "pass", "check": "agentive-review-gate", "auto_merge": True, "head_sha": "a" * 40}}
        new = pr_body.with_pipeline(body, pipe, 1, [{"role": "reviewer", "tool": "claude_code", "model": "inherit", "session_id": "S3",
                                                     "context": "isolated", "executed_by": "process", "attestation": "ci_job", "source": "ci", "verdict": "pass",
                                                     "reviewer_id": "correctness-reviewer"}])
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
        self.assertIn("agentive reviewer eval: not scored — review this reviewer change by hand", text)       # COMMENTUX
        self.assertIn("adopt_suggested: false — merge only with `eval-override: <reason>` in the PR body", text)
        self.assertIn(evals.MARKER, text)
        block = util.fenced_blocks(text, "yaml agentive-reviewer-eval")[0]
        self.assertEqual(ayaml.loads(block), only_pos)

    def test_e2e_16_no_baseline_needs_no_eval_override(self):
        """E2E-16 (NOBASELINE): the first install has no reviewer to compare with: reason no_baseline, adopt_suggested true, no
        score table, the comment says first install and names the next step, and never asks for eval-override."""
        s = evals.summarize([], {}, {}, 3, "b" * 40, "c" * 40, no_baseline=True)
        self.assertEqual((s["reason"], s["adopt_suggested"]), ("no_baseline", True))
        self.assertEqual(h.validate_ref("https://fw.local/schemas/reviewer-eval-case.schema.json#/$defs/summary", s), [])
        text = evals.comment(s, [".agents/bin/agentive-review (.agents/bin/agentive-review)"])
        self.assertIn("### \u2139\ufe0f agentive reviewer eval: not compared — first install", text)            # COMMENTUX
        self.assertIn("**Result:** Nothing to compare: the base branch has no agentive reviewer yet", text)
        self.assertIn("no `eval-override` line is needed", text)
        self.assertIn("**To get a score next time:** add one positive and one negative case under evals/reviewer/", text)
        self.assertNotIn("merge only with", text)
        self.assertNotIn("| known bugs caught", text)
        self.assertEqual(ayaml.loads(util.fenced_blocks(text, "yaml agentive-reviewer-eval")[0]), s)

    def test_e2e_16_order_and_the_kept_override(self):
        """not_run is named before no_baseline; with a baseline and no case the override line stays (EVALOVERRIDE) and the
        insufficient line says what to add."""
        s = evals.summarize([], {}, {}, 3, "b", "c", not_run=True, no_baseline=True)
        self.assertEqual(s["reason"], "not_run")
        s = evals.summarize([], {}, {}, 3, "b", "c")
        self.assertEqual((s["reason"], s["adopt_suggested"]), ("insufficient_cases", False))
        text = evals.comment(s, ["code_review.md"])
        self.assertIn("**Result:** Not scored: `evals/reviewer/` has no case yet, so nothing was compared.", text)   # COMMENTUX
        self.assertIn("**To get a score next time:** add one positive and one negative case under evals/reviewer/ "
                      "(evals/reviewer/README.md).", text)
        self.assertIn("adopt_suggested: false — merge only with `eval-override: <reason>` in the PR body", text)


if __name__ == "__main__":
    unittest.main()
