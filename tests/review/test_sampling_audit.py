# agentive-unit: selftest.component
# design: decisions.accepted[SAMPAUDIT], spec.review_pipeline.stages[RV8], spec.review_pipeline.auto_merge_policy
"""SAMPAUDIT (user 2026-11-26, revising the removed U1.b): a deterministic, risk-weighted share of the automated approvals goes to
a human first, and every automated approval decision leaves a label and a machine-readable audit block in the summary comment.
Covers the draw, the rates, the gate rule, the config read from the base commit, the audit block, the labels, the kill switch feed."""
import datetime
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers as h  # noqa: E402
from test_labels import load_bin  # noqa: E402
from test_pipeline import PipelineBase, body  # noqa: E402
from test_stages import GateTest  # noqa: E402

from agentive.review import audit, killswitch, post, rv8, sampling  # noqa: E402
from agentive.review import config as cfg  # noqa: E402

APPROVED, SAMPLED, BAD = cfg.APPROVED_LABEL, cfg.SAMPLED_LABEL, cfg.SAMPLE_BAD_LABEL


def pick(rate, want, repo="o/r", lo=1, hi=2000):
    """A PR number whose draw is (want=True) below or (want=False) at or above `rate`."""
    for n in range(lo, hi):
        if (sampling.draw(sampling.seed(repo, n)) < rate) == want:
            return n
    raise AssertionError("no PR number found")


class DrawTest(unittest.TestCase):
    def test_the_draw_is_deterministic_and_in_range(self):
        a = sampling.draw(sampling.seed("o/r", 7))
        self.assertEqual(a, sampling.draw(sampling.seed("o/r", 7)))
        self.assertTrue(0 <= a < 1)
        self.assertNotEqual(a, sampling.draw(sampling.seed("o/r", 8)))
        self.assertNotEqual(a, sampling.draw(sampling.seed("other/repo", 7)))

    def test_rate_zero_never_and_rate_one_always(self):
        for n in range(1, 300):
            s = sampling.seed("o/r", n)
            self.assertFalse(sampling.select(0, {"tier0": 0, "tier1": 0}, s)["selected"])
            self.assertTrue(sampling.select(1, {"tier0": 1, "tier1": 1}, s)["selected"])

    def test_tier_one_is_sampled_more_than_tier_zero_and_the_rates_hold(self):
        rates = dict(cfg.SAMPLING_DEFAULT)
        n0 = n1 = 0
        N = 4000
        for n in range(1, N + 1):
            s = sampling.seed("o/r", n)
            a, b = sampling.select(0, rates, s)["selected"], sampling.select(1, rates, s)["selected"]
            n0 += a
            n1 += b
            self.assertTrue(b or not a, "a PR drawn at tier 0 is also drawn at tier 1")
        self.assertGreater(n1, n0)
        self.assertAlmostEqual(n0 / N, 0.05, delta=0.02)
        self.assertAlmostEqual(n1 / N, 0.2, delta=0.03)
        self.assertEqual(cfg.SAMPLING_DEFAULT, {"tier0": 0.05, "tier1": 0.2})

    def test_the_draw_is_one_number_per_pr_so_a_push_cannot_re_roll_it(self):
        self.assertEqual(sampling.seed("o/r", 7), "o/r:7")      # no head sha, no commit content in the seed


class ConfigTest(unittest.TestCase):
    def load(self, text):
        return cfg.load_sampling(lambda p: text)

    def test_defaults_override_and_invalid_values(self):
        self.assertEqual(self.load(None).value, {"tier0": 0.05, "tier1": 0.2})
        self.assertEqual(self.load("review:\n  auto_merge: low_risk\n").value, {"tier0": 0.05, "tier1": 0.2})
        got = self.load("review:\n  sampling:\n    tier0: 0\n    tier1: 1\n")
        self.assertEqual((got.value, got.problems), ({"tier0": 0.0, "tier1": 1.0}, []))
        got = self.load("review:\n  sampling:\n    tier1: 0.5\n")
        self.assertEqual(got.value, {"tier0": 0.05, "tier1": 0.5})
        # a broken config never lowers the sampling
        for bad in ("review:\n  sampling:\n    tier0: 2\n", "review:\n  sampling:\n    tier0: high\n", "review:\n  sampling:\n    tier0: true\n",
                    "review:\n  sampling: 0\n", "review:\n  sampling:\n    tier2: 1\n", "review: [\n"):
            got = self.load(bad)
            self.assertEqual(got.value, {"tier0": 0.05, "tier1": 0.2}, bad)
            self.assertTrue(got.problems, bad)

    def test_settings_effective_shows_the_rates_and_the_find_command(self):
        from agentive.review import settings
        text = "\n".join(settings.effective(lambda p: "review:\n  sampling:\n    tier0: 0.1\n" if p == ".agents/framework.yaml" else None))
        self.assertIn("review.sampling: tier0 0.1, tier1 0.2   [.agents/framework.yaml]", text)
        self.assertIn(cfg.FIND_SAMPLED_CMD, text)
        self.assertNotIn("gh pr ", text)                      # RESTONLY (E2E-15): gh api only


class GateRuleTest(unittest.TestCase):
    gt = GateTest()

    def decide(self, **kw):
        return self.gt.decide(**kw)

    def seed_for(self, rate, want):
        return sampling.seed("o/r", pick(rate, want))

    def test_a_drawn_tier0_pr_goes_to_a_human_with_a_reason(self):
        r = self.decide(sampling={"tier0": 0.5, "tier1": 0.5}, sample_seed=self.seed_for(0.5, True))
        self.assertEqual((r["decision"], r["auto_merge"], r["check_conclusion"]), ("human_required", False, "success"))
        self.assertIn("sampled_for_review", r["reason_codes"])
        self.assertTrue(r["sampled"]["selected"])
        self.assertEqual((r["sampled"]["tier"], r["sampled"]["rate"]), (0, 0.5))
        self.assertTrue(any(x.startswith("sampled_for_review: drawn ") and SAMPLED in x for x in r["reasons"]))

    def test_an_undrawn_pr_merges_and_records_the_draw(self):
        r = self.decide(sampling={"tier0": 0.5, "tier1": 0.5}, sample_seed=self.seed_for(0.5, False))
        self.assertEqual((r["decision"], r["auto_merge"]), ("pass", True))
        self.assertFalse(r["sampled"]["selected"])
        self.assertNotIn("sampled_for_review", r["reason_codes"])

    def test_rate_zero_and_rate_one(self):
        for n in (1, 2, 3, 4, 5):
            s = sampling.seed("o/r", n)
            self.assertEqual(self.decide(sampling={"tier0": 0, "tier1": 0}, sample_seed=s)["auto_merge"], True)
            r = self.decide(sampling={"tier0": 1, "tier1": 1}, sample_seed=s)
            self.assertEqual((r["decision"], r["auto_merge"]), ("human_required", False))

    def test_no_sampling_input_means_no_sample(self):
        r = self.decide()
        self.assertEqual((r["decision"], r["auto_merge"]), ("pass", True))
        self.assertNotIn("sampled", r)

    def test_tier_weighting_uses_the_rate_of_the_prs_tier(self):
        tier1 = dict(tier=1, rv4_reasons=["tier1"], auto_merge="low_and_medium_risk",
                     rv7b={"score": 10, "human_if_score_gt": 28, "reason": "computed"})
        n = pick(0.2, True)
        s = sampling.seed("o/r", n)
        # a number drawn at rate 0.2 is drawn for tier 1 (rate 0.2) and, when it is also below 0.05, for tier 0
        d = sampling.draw(s)
        rates = {"tier0": 0.05, "tier1": 0.2}
        r1 = self.decide(sampling=rates, sample_seed=s, **tier1)
        self.assertEqual(r1["sampled"]["selected"], True)
        r0 = self.decide(sampling=rates, sample_seed=s)
        self.assertEqual(r0["sampled"]["selected"], d < 0.05)

    def test_only_prs_the_gate_would_merge_are_drawn(self):
        s = sampling.seed("o/r", 1)
        # tier 1 in the default low_risk setting is shadow: nothing automated, so nothing to sample
        shadow = self.decide(tier=1, rv4_reasons=["tier1"], rv7b={"score": 10, "human_if_score_gt": 28, "reason": "computed"},
                             sampling={"tier0": 1, "tier1": 1}, sample_seed=s)
        self.assertTrue(shadow["would_auto_merge"])
        self.assertNotIn("sampled", shadow)
        # a PR that already needs a human or is blocked is not drawn
        human = self.decide(tier=2, rv4_reasons=["tier2"], sampling={"tier0": 1, "tier1": 1}, sample_seed=s)
        self.assertNotIn("sampled", human)
        blocked = self.decide(checks=[{"name": "test", "status": "fail"}], sampling={"tier0": 1, "tier1": 1}, sample_seed=s)
        self.assertEqual(blocked["decision"], "block")
        self.assertNotIn("sampled", blocked)
        off = self.decide(auto_merge="off", sampling={"tier0": 1, "tier1": 1}, sample_seed=s)
        self.assertNotIn("sampled", off)

    def test_the_comment_tells_a_human_why_and_what_to_do(self):
        r = self.decide(sampling={"tier0": 1, "tier1": 1}, sample_seed=sampling.seed("o/r", 3))
        report = {"head_sha": "a" * 40, "rv3": {"classes": ["docs"], "method": "deterministic"}, "rv4": {"tier": 0, "reasons": ["tier0"]},
                  "rv7b": {"score": None, "reason": "not_tier1"}, "review_round": {"round": 1, "mode": "full"}, "findings": [], "rv8": r}
        text = post.summary_comment(report, 0, {})
        head = text.split("<details>")[0]
        self.assertIn("sampled — a human reviews it first", head)
        self.assertIn(SAMPLED, head)
        self.assertIn(BAD, head)
        self.assertNotIn("sampled_for_review", head)          # COMMENTUX: no internal code in the visible lines


class PipelineSamplingTest(PipelineBase):
    """The rate is read from the base commit through the full pipeline."""

    def docs_pr(self, base_config=None):
        if base_config is not None:
            self.repo.write({".agents/framework.yaml": base_config})
            h.git(self.repo.dir, "checkout", "-q", "main")
            self.base = self.repo.commit("config")
            h.git(self.repo.dir, "checkout", "-q", "-b", "feat2")
        self.repo.write({"README.md": "# r\n\nMore words.\n"})
        return self.repo.commit("docs")

    def test_default_rates_do_not_sample_a_pr_that_draws_high(self):
        n = pick(0.05, False, repo="")
        self.assertEqual(n, pick(0.05, False, repo="", lo=n, hi=n + 1))
        head = self.docs_pr()
        rep, _, _ = self.ci(head, h.default_responder(), body())          # PR 7, no repository id: draw 0.608
        self.assertEqual((rep["rv8"]["decision"], rep["rv8"]["auto_merge"]), ("pass", True))
        self.assertEqual(rep["rv8"]["sampled"]["selected"], False)
        self.assertEqual(rep["rv8"]["sampled"]["rate"], 0.05)

    def test_the_base_commit_config_sets_the_rate(self):
        head = self.docs_pr("review:\n  sampling:\n    tier0: 1\n")
        rep, _, _ = self.ci(head, h.default_responder(), body())
        r = rep["rv8"]
        self.assertEqual((r["decision"], r["auto_merge"], r["sampled"]["selected"]), ("human_required", False, True))
        self.assertIn("sampled_for_review", r["reason_codes"])
        self.assertEqual(rep["rv4"]["tier"], 0)

    def test_a_pr_that_lowers_the_rate_in_its_own_head_is_still_drawn_by_the_base_rate(self):
        h.git(self.repo.dir, "checkout", "-q", "main")
        self.repo.write({".agents/framework.yaml": "review:\n  sampling:\n    tier0: 1\n"})
        self.base = self.repo.commit("config")
        h.git(self.repo.dir, "checkout", "-q", "-b", "feat3")
        self.repo.write({".agents/framework.yaml": "review:\n  sampling:\n    tier0: 0\n", "README.md": "# r\n\nMore words.\n"})
        head = self.repo.commit("lower the rate")
        rep, _, _ = self.ci(head, h.default_responder(), body())
        self.assertEqual(rep["rv4"]["tier"], 2)                      # a review config change is tier 2 (U2 review_config): a human decides
        self.assertFalse(rep["rv8"]["auto_merge"])
        self.assertNotIn("sampled", rep["rv8"])                      # never drawn: the head's rate 0 cannot let it merge


class AuditTest(unittest.TestCase):
    def report(self, **rv8_kw):
        r8 = {"decision": "pass", "auto_merge": True, "auto_merge_setting": "low_risk", "head_sha": "a" * 40, "reasons": [],
              "reason_codes": ["x"], "check_conclusion": "success"}
        r8.update(rv8_kw)
        return {"rv1": {"checks": [{"name": "test", "status": "pass"}, {"name": "lint", "status": "pass"}]},
                "rv2": {"hits": [], "confirm_scope_hits": []}, "rv3": {"classes": ["docs"], "method": "deterministic"},
                "rv4": {"tier": 0, "reasons": ["tier0"]},
                "rv7b": {"score": 14, "reason": "computed", "preset": "agentive:rubric@2", "human_if_score_gt": 28, "config_sha256": "c" * 64,
                         "items": [{"id": "spread", "hit": True, "weight": 14}, {"id": "size", "hit": False, "weight": 30}]},
                "findings": [{"severity": "important", "verification": {"verdict": "refuted"}, "file": "a.py", "line": 1, "summary": "s"},
                             {"severity": "nit", "file": "a.py", "line": 2, "summary": "n"}], "rv8": r8}

    def test_the_record_holds_the_signals_and_the_reasons(self):
        reviews = [{"role": "reviewer", "reviewer_id": "correctness-reviewer", "verdict": "pass", "tool": "claude_code"}]
        rec = audit.record(self.report(), reviews, "0.2.0", "https://x/run/1", "2026-11-26T00:00:00Z")
        self.assertEqual((rec["v"], rec["outcome"], rec["head_sha"], rec["gate_version"]), (1, "auto_merged", "a" * 40, "0.2.0"))
        self.assertEqual((rec["tier"], rec["tier_reasons"], rec["classes"]), (0, ["tier0"], ["docs"]))
        self.assertEqual(rec["trust_boundary"], {"result": "clear", "paths": []})
        self.assertEqual(rec["rubric"]["score"], 14)
        self.assertEqual(rec["rubric"]["items"], [{"id": "spread", "hit": True, "weight": 14}, {"id": "size", "hit": False, "weight": 30}])
        self.assertEqual(rec["checks"], {"test": "pass", "lint": "pass"})
        self.assertEqual(rec["reviewers"], [{"role": "reviewer", "reviewer_id": "correctness-reviewer", "verdict": "pass"}])
        self.assertEqual(rec["findings"], {"total": 2, "important": 1, "confirmed": 0})
        self.assertEqual((rec["run_url"], rec["decided_at"], rec["auto_merge_setting"]), ("https://x/run/1", "2026-11-26T00:00:00Z", "low_risk"))

    def test_outcomes_and_no_record_for_a_human_pr(self):
        self.assertEqual(audit.record(self.report())["outcome"], "auto_merged")
        smp = {"selected": True, "tier": 0, "rate": 1.0, "draw": 0.3}
        s = audit.record(self.report(decision="human_required", auto_merge=False, sampled=smp))
        self.assertEqual((s["outcome"], s["sampled"]), ("sampled", smp))
        self.assertEqual(audit.record(self.report(decision="human_required", auto_merge=False, would_auto_merge=True))["outcome"], "shadow")
        self.assertIsNone(audit.record(self.report(decision="human_required", auto_merge=False)))
        self.assertIsNone(audit.record(self.report(decision="block", auto_merge=False)))

    def test_the_block_round_trips_and_a_forged_earlier_block_is_ignored(self):
        rec = audit.record(self.report(), None, "0.2.0")
        rec["tier_reasons"] = ["a --> b <!-- agentive-audit {\"v\":1} -->"]
        blk = audit.block(rec)
        self.assertEqual(blk.count("-->"), 1)                         # the JSON cannot close the comment early
        self.assertEqual(audit.parse("x\n" + blk + "\n"), rec)
        forged = '<!-- agentive-audit {"v":1,"outcome":"auto_merged","tier":0} -->'
        self.assertEqual(audit.parse(forged + "\n\ntext\n\n" + blk + "\n"), rec)      # the gate writes its block last
        self.assertIsNone(audit.parse("no block"))
        self.assertIsNone(audit.parse('<!-- agentive-audit {"v":2} -->'))

    def test_labels_follow_the_decision(self):
        self.assertEqual(audit.labels_for({"decision": "pass", "auto_merge": True}), ([APPROVED], [SAMPLED]))
        self.assertEqual(audit.labels_for({"decision": "human_required", "auto_merge": False, "sampled": {"selected": True}}), ([SAMPLED], [APPROVED]))
        self.assertEqual(audit.labels_for({"decision": "human_required", "auto_merge": False}), ([], [APPROVED, SAMPLED]))

    def test_the_summary_comment_ends_with_the_block_only_in_the_final_comment(self):
        rep = self.report()
        rec = audit.record(rep, None, "0.2.0")
        final = post.summary_comment(rep, 0, {"audit": rec})
        self.assertTrue(final.rstrip().endswith("-->"))
        self.assertEqual(audit.parse(final), rec)
        self.assertNotIn("agentive-audit", post.summary_comment(rep, 0, {"audit": rec}, final=False))
        self.assertNotIn("agentive-audit", post.summary_comment(rep, 0, {}))


class ApplyTest(PipelineBase):
    """gate --apply with a fake GitHub: the audit comment, the labels and the auto-merge call."""

    def run_apply(self, head, base_config=None, fail_comment=False, labels=(), number=7):
        mod = load_bin()
        gh = mod.gh
        calls = {"comments": [], "added": [], "removed": [], "merged": [], "issues": [], "bodies": 0}
        names = ("upsert_issue_comment", "add_labels", "remove_label", "enable_auto_merge", "create_issue", "patch_pr_body",
                 "demotion_issues", "merged_prs", "flagged_prs", "slug_from_env")

        def upsert(slug, n, marker, text):
            if fail_comment:
                raise gh.GhError("boom")
            calls["comments"].append(text)
        saved = {k: getattr(gh, k) for k in names}
        gh.upsert_issue_comment = upsert
        gh.add_labels = lambda slug, n, labels: calls["added"].extend(labels)
        gh.remove_label = lambda slug, n, label: calls["removed"].append(label)
        gh.enable_auto_merge = lambda slug, n: calls["merged"].append(n)
        gh.create_issue = lambda *a, **k: calls["issues"].append(a)
        gh.patch_pr_body = lambda *a, **k: calls.__setitem__("bodies", calls["bodies"] + 1)
        gh.demotion_issues = lambda *a, **k: []
        gh.merged_prs = lambda *a, **k: []
        gh.flagged_prs = lambda *a, **k: []
        gh.slug_from_env = lambda repo: "o/r"
        cwd = os.getcwd()
        try:
            os.chdir(self.repo.dir)
            pr = self.pr(head, body(), labels=list(labels))
            rc = mod.main(["gate", str(number), "--pr-json", pr, "--work", self.repo.path(".agents/state/w"),
                           "--comments-json", self.comments([]), "--repo-slug", "o/r", "--apply"])
        finally:
            os.chdir(cwd)
            for k, v in saved.items():
                setattr(gh, k, v)
        return rc, calls

    def prepare(self, config=None):
        if config is not None:
            h.git(self.repo.dir, "checkout", "-q", "main")
            self.repo.write({".agents/framework.yaml": config})
            self.base = self.repo.commit("config")
            h.git(self.repo.dir, "checkout", "-q", "-b", "feat2")
        self.repo.write({"README.md": "# r\n\nMore words.\n"})
        head = self.repo.commit("docs")
        rep, _, _ = self.ci(head, h.default_responder(), body(), gate=False)
        return head, rep

    def test_an_auto_merge_leaves_the_label_the_audit_block_and_the_merge_call(self):
        head, _ = self.prepare()
        rc, calls = self.run_apply(head)
        self.assertEqual(rc, 0)
        self.assertEqual(calls["merged"], [7])
        self.assertEqual(calls["added"], [APPROVED])
        rec = audit.parse(calls["comments"][-1])
        self.assertEqual((rec["outcome"], rec["tier"], rec["head_sha"], rec["classes"]), ("auto_merged", 0, head, ["docs"]))
        self.assertIn("trust_boundary", rec)
        self.assertIn("gate_version", rec)
        self.assertEqual(rec["sampled"]["selected"], False)

    def test_a_sampled_pr_gets_the_sample_label_no_merge_call_and_a_record(self):
        head, _ = self.prepare("review:\n  sampling:\n    tier0: 1\n")
        rc, calls = self.run_apply(head)
        self.assertEqual(rc, 0)
        self.assertEqual(calls["merged"], [])
        self.assertEqual(calls["added"], [SAMPLED])
        rec = audit.parse(calls["comments"][-1])
        self.assertEqual((rec["outcome"], rec["sampled"]["selected"]), ("sampled", True))
        self.assertIn("sampled_for_review", rec["reasons"])

    def test_stale_labels_are_removed_when_the_decision_changes(self):
        head, _ = self.prepare("review:\n  sampling:\n    tier0: 1\n")
        rc, calls = self.run_apply(head, labels=[APPROVED, SAMPLED])
        self.assertEqual((calls["added"], calls["removed"]), ([SAMPLED], [APPROVED]))

    def test_a_pr_that_is_no_longer_sampled_swaps_the_label(self):
        head, _ = self.prepare()          # the default rates: PR 7 draws high, so the gate approves it
        rc, calls = self.run_apply(head, labels=[SAMPLED])
        self.assertEqual((calls["added"], calls["removed"], calls["merged"]), ([APPROVED], [SAMPLED], [7]))

    def test_no_audit_record_no_automated_merge(self):
        head, _ = self.prepare()
        rc, calls = self.run_apply(head, fail_comment=True)
        self.assertEqual(rc, 0)
        self.assertEqual(calls["merged"], [])

    def test_a_pr_for_a_human_gets_neither_label_nor_block(self):
        self.repo.write({"src/app.py": "import os\n\n\ndef run(x):\n    return 10 / x\n"})
        head = self.repo.commit("code")
        self.ci(head, h.default_responder(), body(), gate=False)
        rc, calls = self.run_apply(head)
        self.assertEqual((calls["merged"], calls["added"]), ([], [cfg.AUTO_MERGE_LABEL]))        # tier 1 shadow: its own label only
        self.assertEqual(audit.parse(calls["comments"][-1])["outcome"], "shadow")


class KillSwitchFeedTest(unittest.TestCase):
    NOW = datetime.datetime(2026, 11, 26, tzinfo=datetime.timezone.utc)

    def body(self, tier, sampled=True):
        return ("x\n\n```yaml agentive-review\nagentive-review:\n  pipeline:\n    tier: {value: %d}\n    gate: {sampled: %s}\n```\n"
                % (tier, "true" if sampled else "false"))

    def flagged(self, n, author="dev", tier=0, days=1, sampled=True):
        t = (self.NOW - datetime.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        return {"number": n, "author": author, "updated_at": t, "body": self.body(tier, sampled)}

    def test_a_sampled_pr_found_bad_counts_like_a_revert(self):
        one = killswitch.evaluate(0, "dev", self.NOW, [], [], [], [self.flagged(5)])
        self.assertEqual((one["count"], one["prs"], one["demoted"]), (1, [5], False))
        two = killswitch.evaluate(0, "dev", self.NOW, [], [], [], [self.flagged(5), self.flagged(6)])
        self.assertEqual((two["count"], two["demoted"], two["open_issue"]), (2, True, True))

    def test_only_the_same_tier_source_window_and_a_sampled_pr_count(self):
        flagged = [self.flagged(1, tier=1), self.flagged(2, author="other"), self.flagged(3, days=9), self.flagged(4, sampled=False)]
        self.assertEqual(killswitch.evaluate(0, "dev", self.NOW, [], [], [], flagged)["count"], 0)

    def test_a_pr_that_is_also_reverted_counts_once(self):
        sha = "b" * 40
        merged = [{"number": 5, "author": "dev", "merged_at": "2026-11-24T00:00:00Z", "merge_commit_sha": sha,
                   "body": "x\n\n```yaml agentive-review\nagentive-review:\n  pipeline:\n    tier: {value: 0}\n    gate: {auto_merge: true}\n```\n"}]
        ts = int((self.NOW - datetime.timedelta(days=1)).timestamp())
        got = killswitch.evaluate(0, "dev", self.NOW, merged, [("c" * 40, ts, f"This reverts commit {sha}.")], [], [self.flagged(5)])
        self.assertEqual((got["count"], got["prs"]), (1, [5]))

    def test_the_body_flag_is_written_by_the_gate(self):
        mod = load_bin()
        rep = {"rv2": {"hits": []}, "rv3": {"classes": ["docs"], "method": "x"}, "rv4": {"tier": 0, "reasons": []}, "findings": [],
               "rv7b": {"score": None, "reason": "not_tier1"},
               "rv8": {"decision": "human_required", "auto_merge": False, "head_sha": "a" * 40, "sampled": {"selected": True}}}
        self.assertTrue(mod.body_pipeline(rep)["gate"]["sampled"])
        rep["rv8"]["sampled"] = {"selected": False}
        self.assertFalse(mod.body_pipeline(rep)["gate"]["sampled"])


if __name__ == "__main__":
    unittest.main()
