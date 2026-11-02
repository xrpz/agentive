# agentive-unit: selftest.component
# design: decisions.accepted[EFFECTIVECHECKS], decisions.accepted[LOCKFILEDEPS], decisions.accepted[REVIEWRECORD], decisions.accepted[PENDINGNOTICE], spec.review_pipeline.stages[RV2], spec.review_pipeline.stages[RV8], docs/impl/decisions-log.md §19
"""End-to-end decisions batch D (2026-10-31): E2E-19 effective required checks and the floor, E2E-21 lockfiles and dependency
files are dependency data, E2E-23 the folded review record is read by every parser, E2E-24 every reason computed and shown."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers as h  # noqa: E402
from test_secpaths import FakeDiff, block, load  # noqa: E402
import test_stages as ts  # noqa: E402

from agentive import ayaml, prstate  # noqa: E402
from agentive.review import config as cfg, independence, post, pr_body, rv1, rv2, rv3, util  # noqa: E402

RV7B_30 = {"score": 30, "reason": "computed", "preset": "agentive:rubric@2", "human_if_score_gt": 28, "config_sha256": "c" * 64,
           "items": [{"id": "size", "hit": True, "weight": 30}]}


def summary(rv8_out, rv4=None, checks=None):
    return {"head_sha": "a" * 40, "rv1": {"checks": checks or [{"name": "lint", "status": "pass"}, {"name": "test", "status": "pass"}]},
            "rv3": {"classes": ["fix"], "method": "rules"}, "rv4": rv4 or {"tier": 0, "reasons": ["tier0"]},
            "rv7b": {"score": None, "reason": "not_tier1"}, "review_round": {"round": 1, "mode": "full"}, "findings": [], "rv8": rv8_out}


class E2E19RequiredChecksTest(unittest.TestCase):
    decide, report = ts.GateTest.decide, ts.GateTest.report

    def test_e2e_19_default_list_drops_a_pending_verb_and_auto_merges(self):
        r = self.decide(pending_verbs=["typecheck"])
        self.assertEqual((r["decision"], r["auto_merge"]), ("pass", True))
        self.assertNotIn("pending_verbs", r["reason_codes"])
        self.assertEqual(r["required_checks"], {"required": ["lint", "test"], "not_required": ["typecheck"], "source": "framework default"})
        self.assertEqual(self.report(r, 0), [])
        lines = post.required_lines(summary(r))
        self.assertEqual(lines, ["- required checks: lint (pass), test (pass) [framework default]",
                                 "- not required: typecheck (no command in this repo; info, not blocking)"])

    def test_e2e_19_floor_no_required_check_with_a_command(self):
        r = self.decide(pending_verbs=["lint", "typecheck", "test"])
        self.assertEqual((r["decision"], r["auto_merge"]), ("human_required", False))
        self.assertIn("no_required_check", r["reason_codes"])
        self.assertIn("no_required_check: human review needed: no required check has a command (lint, typecheck, test are all pending), "
                      "so nothing was verified. Add a command, then run /agentive:update.", r["reasons"])
        self.assertEqual(self.report(r, 0), [])
        # an explicit empty list verifies nothing either
        r = self.decide(required_checks=[], require_explicit=True)
        self.assertIn("no_required_check", r["reason_codes"])
        self.assertIn("require_checks is empty", " ".join(r["reasons"]))

    def test_e2e_19_a_pending_verb_the_user_named_stays_fail_closed(self):
        r = self.decide(pending_verbs=["typecheck"], require_explicit=True)
        self.assertEqual(r["decision"], "human_required")
        self.assertEqual([c for c in r["reason_codes"] if c in ("pending_verbs", "no_required_check")], ["pending_verbs"])
        self.assertEqual(r["required_checks"]["source"], ".agents/framework.yaml")
        self.assertIn("you required it in .agents/framework.yaml", " ".join(r["reasons"]))
        # all named verbs pending: one reason (pending_verbs), not two
        r = self.decide(pending_verbs=["lint", "typecheck", "test"], require_explicit=True)
        self.assertNotIn("no_required_check", r["reason_codes"])

    def test_e2e_19_config_records_whether_the_user_wrote_require_checks(self):
        files = {".agents/framework.yaml": "review:\n  pipeline:\n    stages:\n      RV1:\n        require_checks: [lint, typecheck]\n"}
        got = cfg.load_project_config(files.get)
        self.assertTrue(got.require_explicit)
        self.assertFalse(cfg.load_project_config({}.get).require_explicit)
        self.assertFalse(cfg.load_project_config({".agents/framework.yaml": "review:\n  auto_merge: off\n"}.get).require_explicit)
        manifest = {"verbs": {"lint": {"cmd": "ruff"}, "typecheck": {"status": "pending"}, "test": {"cmd": "pytest"}}}
        self.assertEqual(cfg.pending_required(manifest, got), ["typecheck"])
        self.assertEqual(cfg.pending_required(manifest, cfg.load_project_config({}.get)), [])
        rs = cfg.required_state_of(manifest, cfg.load_project_config({}.get))
        self.assertEqual(cfg.required_report(rs), "required-checks: lint, test. Not required (no command found): typecheck. "
                                                  "Add a typecheck tool and run /agentive:update to require it.")
        self.assertTrue(cfg.required_report(cfg.required_state(["lint"], ["lint"], False)).startswith("required-checks: none."))
        self.assertIn("but you listed it in .agents/framework.yaml", cfg.required_report(cfg.required_state_of(manifest, got)))


class E2E24AllReasonsTest(unittest.TestCase):
    decide, report = ts.GateTest.decide, ts.GateTest.report

    def test_e2e_24_score_reason_is_computed_when_another_reason_stops_auto_merge(self):
        r = self.decide(tier=1, rv4_reasons=["tier1"], rv7b=RV7B_30, pending_verbs=["typecheck"], require_explicit=True)
        self.assertEqual(r["decision"], "human_required")
        self.assertTrue({"pending_verbs", "score_above_threshold"} <= set(r["reason_codes"]))
        self.assertIn("score_above_threshold: score 30 > 28", r["reasons"])
        self.assertEqual(self.report(r, 1, rv7b_=RV7B_30), [])
        # a score at or below the threshold adds nothing; tier 0 and tier 2 carry no score reason
        ok = dict(RV7B_30, score=14)
        r = self.decide(tier=1, rv4_reasons=["tier1"], rv7b=ok, pending_verbs=["typecheck"], require_explicit=True)
        self.assertNotIn("score_above_threshold", r["reason_codes"])
        # a block keeps its decision; the score reason is only added
        r = self.decide(tier=1, rv4_reasons=["tier1"], rv7b=RV7B_30, checks=[{"name": "lint", "status": "fail"}])
        self.assertEqual(r["decision"], "block")
        self.assertIn("score_above_threshold", r["reason_codes"])

    def test_e2e_24_summary_lists_every_reason_once(self):
        r = self.decide(tier=1, rv4_reasons=["tier1"], rv7b=RV7B_30, pending_verbs=["typecheck"], require_explicit=True)
        rv4 = {"tier": 1, "reasons": ["always_human", "always_human: env vars added or removed (1): TOKEN"]}
        text = post.summary_comment(summary(r, rv4=rv4), 0, {})
        visible = text.split("<details>")[0]
        # COMMENTUX: every reason once, as a plain Why line; the codes only in Details
        self.assertIn("**Why**\n", visible)
        self.assertIn("- The code reads 1 environment variable it did not read before, or stopped reading it (names in Details).", visible)
        self.assertIn("- You required typecheck in .agents/framework.yaml, but this repo has no command for it.", visible)
        self.assertIn("- Risk score 30 is above the auto-merge limit 28.", visible)
        self.assertEqual(text.count("Risk score 30 is above"), 1)
        self.assertNotIn("always_human", visible)
        self.assertIn("`score_above_threshold`", text)
        self.assertIn("**Checks:** lint passed · typecheck pending · test passed\n", visible)
        # a passing PR has no Why; the not-required verb is on the Checks line of every summary
        p = self.decide(pending_verbs=["typecheck"])
        text = post.summary_comment(summary(p), 0, {})
        self.assertNotIn("**Why**", text)
        self.assertIn("### \u2705 agentive review: merges automatically", text)
        self.assertIn("**Checks:** lint passed · test passed · typecheck not run (no command in this repo)", text)

    def test_e2e_24_reason_lines_keep_bare_codes_and_free_text(self):
        self.assertEqual(post.reason_lines(["tier2", "trust_boundary", "ai_review_missing: no report", "x: y", "x: y"]),
                         ["tier2", "trust_boundary", "ai_review_missing: no report", "x: y"])
        self.assertEqual(post.reason_lines(["no_required_check", "no_required_check: human review needed: z"]), ["human review needed: z"])


class E2E21LockfileTest(unittest.TestCase):
    R = load(block()).value

    def test_e2e_21_lockfiles_and_dependency_files_are_not_check_config(self):
        manifest = {"verbs": {"lint": {"cmd": "uv run ruff check .", "evidence": {"files": [
            "pyproject.toml", "uv.lock", "ruff.toml", "requirements.txt", "requirements-dev.txt", "Pipfile", "Pipfile.lock",
            "setup.py", "pyproject.toml#tool.ruff"]}},
            "typecheck": {"cmd": "tsc", "evidence": {"files": ["package.json", "pnpm-lock.yaml", "tsconfig.json"]}},
            "test": {"cmd": "go test", "evidence": {"files": ["go.mod", "go.sum"]}}}}
        self.assertEqual(sorted(rv2.evidence_config_files(manifest)), [("pyproject.toml", "tool.ruff"), ("ruff.toml", None), ("tsconfig.json", None)])

    def detect(self, files, checks=None, pr=True, head=None, base=None):
        manifest = {"verbs": {"lint": {"cmd": "ruff", "evidence": {"files": ["pyproject.toml", "uv.lock", "ruff.toml", "requirements.txt"]}}}}
        return rv2.run(FakeDiff(head, base), [("M", p, None) for p in files], {}, self.R, manifest,
                       {"env_vars_changed": [], "agentive_check_tags": {}},
                       pr={"author_association": "OWNER", "is_fork": False, "labels": []} if pr else None, checks=checks)

    def test_e2e_21_a_lockfile_change_goes_through_the_dependency_path(self):
        got, always = self.detect(["uv.lock"])
        self.assertEqual([x for x in got["hits"] if x["reason"] == "check_config"], [])
        self.assertNotIn(("always_human", "gate or agent config change (protected_check_config)"), always)
        # DEPHUMAN (E2E-20) replaced critical_cve_unchecked: a lockfile change goes to a human, whatever dependency review says
        self.assertEqual([c for c, _ in always], ["dependency_change"])
        got, always = self.detect(["uv.lock"], checks=[{"name": "dependency-review", "status": "pass"}])
        self.assertEqual([c for c, _ in always], ["dependency_change"])
        # a dedicated lint config stays protected
        got, always = self.detect(["ruff.toml"], checks=[{"name": "dependency-review", "status": "pass"}])
        self.assertIn("check_config", [x["reason"] for x in got["hits"]])

    def test_e2e_21_requirements_pipfile_and_setup_py_are_dependency_files(self):
        texts = {"requirements.txt": ("flask\n", "flask\nrequests\n"), "requirements-dev.txt": ("", "pytest\n"),
                 "Pipfile": ("[packages]\n", "[packages]\nrequests = \"*\"\n"),
                 "setup.py": ("setup(install_requires=[])\n", "setup(install_requires=['click'])\n"),
                 "sub/Pipfile": ("[dev-packages]\n", "[dev-packages]\npytest = \"*\"\n")}
        for p, (b, hd) in texts.items():
            self.assertTrue(rv1.is_package_file(p), p)
            _, always = self.detect([p], head={p: hd}, base={p: b})
            self.assertIn("dependency_change", [c for c, _ in always], p)
            _, always = self.detect([p], head={p: b + "\n"}, base={p: b})     # no dependency entry changed (DEPHUMAN)
            self.assertNotIn("dependency_change", [c for c, _ in always], p)
        self.assertFalse(rv1.is_package_file("setup.cfg"))
        self.assertEqual(set(rv1._deps_pipfile('[[source]]\nurl = "x"\n[packages]\nrequests = "*"\n"Flask_Login" = ">=1"\n'
                                               '[dev-packages]\npytest = "*"\n[requires]\npython_version = "3.12"\n')),
                         {"pypi:requests", "pypi:flask-login", "pypi:pytest"})
        self.assertEqual(set(rv1._deps_setup_py('setup(name="x", install_requires=["requests>=2", \'click\'],\n'
                                                '      extras_require={"dev": ["pytest"]})\n')),
                         {"pypi:requests", "pypi:click", "pypi:pytest"})
        self.assertEqual(rv3._manifest_class("setup.py"), "deps")
        self.assertEqual(rv3._manifest_class("Pipfile"), "deps")


class E2E23ReviewRecordTest(unittest.TestCase):
    OK, LOCAL, pr, ci_review = ts.PrBodyTest.OK, ts.PrBodyTest.LOCAL, ts.PrBodyTest.pr, ts.PrBodyTest.ci_review
    BLK = {"implementer": {"tool": "claude_code", "model": "m", "session_id": "S1", "started_at": "2026-10-28T00:00:00Z"}, "reviews": []}

    def wrapped(self, blk=None):
        from agentive import prbody
        fence = "```yaml agentive-review\n" + ayaml.dumps({"agentive-review": blk or self.BLK}).rstrip("\n") + "\n```"
        return self.OK + "\n" + prbody.wrap_review_record(fence) + "\n"

    def test_e2e_23_every_parser_reads_the_block_inside_details(self):
        from agentive import prbody
        body = self.wrapped()
        self.assertIn("## agentive review record\n\n<details><summary>", body)
        self.assertIn("\n\n```yaml agentive-review\n", body)
        self.assertIn("```\n\n</details>", body)
        doc, prob = pr_body.block(body)
        self.assertEqual((doc["implementer"]["session_id"], prob), ("S1", None))
        self.assertEqual(len(util.fenced_blocks(body, pr_body.BLOCK_INFO)), 1)
        self.assertIsNone(independence.exemption(self.pr(body=body)))                     # an agent PR, not human_pr_exempt
        self.assertEqual(independence.evaluate(self.pr(body=body), [self.ci_review()])["result"], "ok")
        self.assertEqual(prstate.review_block(body)["implementer"]["model"], "m")
        self.assertEqual(prbody.block_problems(body), [])
        # the minimal Explainer of OK is not drawn by the visual profile (local explainer_style checks, explainer_visual)
        self.assertEqual([p for p in prbody.check_body(body, "optional") if not p.startswith("explainer_style:")], [])
        # an unwrapped block of an older PR still reads
        old = self.OK + "\n```yaml agentive-review\n" + ayaml.dumps({"agentive-review": self.BLK}) + "```\n"
        self.assertEqual(pr_body.block(old)[0]["implementer"]["session_id"], "S1")
        # two blocks still fail
        self.assertTrue(prbody.block_problems(body + "\n```yaml agentive-review\nagentive-review: {}\n```\n"))

    def test_e2e_23_ci_rewrite_keeps_the_wrapper(self):
        body = self.wrapped()
        new = pr_body.with_pipeline(body, {"tier": {"value": 0}}, reviews=[self.ci_review()])
        self.assertIsNotNone(new)
        self.assertIn("## agentive review record\n\n<details><summary>", new)
        self.assertTrue(new.rstrip().endswith("</details>"))
        self.assertEqual(pr_body.block(new)[0]["pipeline"], {"tier": {"value": 0}})
        self.assertEqual(len(util.fenced_blocks(new, pr_body.BLOCK_INFO)), 1)


class E2EBatchDSchemaTest(unittest.TestCase):
    def test_e2e_19_schema_has_required_checks_and_no_required_check(self):
        import json
        s = json.loads(h.read(os.path.join(h.ROOT, "design", "schemas", "review-report.schema.json")))
        self.assertIn("no_required_check", s["$defs"]["gate_reason"]["enum"])
        self.assertIn("required_checks", s["properties"]["rv8"]["properties"])
        self.assertIn("no_required_check", prstate.GATE_REASONS)


if __name__ == "__main__":
    unittest.main()
