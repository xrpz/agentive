# agentive-unit: selftest.component
# design: decisions.accepted[MEASUREDWITH], spec.review_pipeline.reviewer_eval.scoring, contracts[reviewer-eval-case]
"""MEASUREDWITH (impl26 B3): a reviewer eval score names the model and CLI version it was measured with, and a case scored with another
pair is listed as needing a re-score (diagnostic only)."""
import importlib.machinery
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers as h  # noqa: E402

from agentive.review import evals  # noqa: E402

SUMMARY_REF = "https://fw.local/schemas/reviewer-eval-case.schema.json#/$defs/summary"
CASE_REF = "https://fw.local/schemas/reviewer-eval-case.schema.json"
OLD = {"tool": "claude", "tool_version": "2.1.316", "model": ["claude-sonnet-5"]}
NEW = {"tool": "claude", "tool_version": "2.1.332", "model": ["claude-sonnet-5-5"]}


def runner():
    path = os.path.join(h.SRC, "dot-agents", "bin", "agentive-reviewer-eval")
    loader = importlib.machinery.SourceFileLoader("agentive_reviewer_eval_bin_mw", path)
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(mod)
    return mod


def case(cid="p1", **kw):
    c = {"id": cid, "pr": "#1", "label": "positive", "origin": {"kind": "revert", "ref": "#2"},
         "expected_findings": [{"file": "a.py", "line_from": 10, "line_to": 12}]}
    c.update(kw)
    return c


class CaseField(unittest.TestCase):
    def test_a_case_may_carry_measured_with(self):
        self.assertEqual(evals.validate_case(case(measured_with=OLD), "p1"), [])
        self.assertEqual(evals.validate_case(case(measured_with={"tool_version": "unknown", "model": "unknown"}), "p1"), [])

    def test_a_bad_measured_with_is_a_problem(self):
        for bad in ({"model": "m"}, {"tool_version": "1.0.0"}, {"tool_version": "", "model": "m"}, {"tool_version": "1", "model": []},
                    {"tool_version": "1", "model": "m", "tool": "gemini"}, {"tool_version": "1", "model": "m", "x": 1}, "claude 2.1"):
            self.assertTrue(evals.validate_case(case(measured_with=bad), "p1"), bad)

    def test_the_schema_agrees(self):
        base = {"id": "p1", "pr": "#1", "label": "positive", "origin": {"kind": "revert", "ref": "#2"},
                "expected_findings": [{"file": "a.py", "line_from": 10, "line_to": 12}]}
        self.assertEqual(h.validate_ref(CASE_REF, dict(base, measured_with=OLD)), [])
        self.assertNotEqual(h.validate_ref(CASE_REF, dict(base, measured_with={"model": "m"})), [])


class Rescore(unittest.TestCase):
    def test_another_model_or_cli_version_asks_for_a_rescore(self):
        got = evals.rescore_required([case("a", measured_with=OLD), case("b", measured_with=NEW),
                                      case("c", measured_with=dict(NEW, model=["claude-sonnet-5"])),
                                      case("d", measured_with=dict(NEW, tool_version="2.1.316"))], NEW)
        self.assertEqual([(x["id"], x["differs"]) for x in got], [("a", ["tool_version", "model"]), ("c", ["model"]), ("d", ["tool_version"])])

    def test_no_claim_or_unknown_is_not_a_change(self):
        self.assertEqual(evals.rescore_required([case("a")], NEW), [])                                   # no measured_with on the case
        self.assertEqual(evals.rescore_required([case("a", measured_with=OLD)], None), [])               # nothing measured now
        unk = {"tool": "codex", "tool_version": "unknown", "model": "unknown"}
        self.assertEqual(evals.rescore_required([case("a", measured_with=OLD)], unk), [])
        self.assertEqual(evals.rescore_required([case("a", measured_with=unk)], NEW), [])

    def test_model_order_does_not_matter(self):
        a = {"tool_version": "1", "model": ["m1", "m2"]}
        b = {"tool_version": "1", "model": ["m2", "m1"]}
        self.assertEqual(evals.rescore_required([case("a", measured_with=a)], b), [])


class Summary(unittest.TestCase):
    def _summary(self, cases, mw):
        runs_ok = {c["id"]: [[]] for c in cases}
        return evals.summarize(cases, runs_ok, runs_ok, 1, "b" * 40, "c" * 40, measured_with=mw)

    def test_the_summary_records_both_sides_and_validates(self):
        s = self._summary([case("a", measured_with=OLD)], {"baseline": NEW, "candidate": NEW})
        self.assertEqual(s["measured_with"], {"baseline": NEW, "candidate": NEW})
        self.assertEqual(s["rescore_required"], [{"id": "a", "differs": ["tool_version", "model"]}])
        self.assertEqual(h.validate_ref(SUMMARY_REF, s), [])

    def test_rescore_is_a_diagnostic_only(self):
        plain = self._summary([case("a")], None)
        flagged = self._summary([case("a", measured_with=OLD)], {"baseline": NEW, "candidate": NEW})
        for k in ("adopt_suggested", "reason", "newly_caught", "newly_missed", "newly_noisy"):
            self.assertEqual(plain[k], flagged[k], k)
        self.assertNotIn("measured_with", plain)        # no record without a measurement: an older runner's result stays valid
        self.assertNotIn("rescore_required", plain)

    def test_the_comment_says_what_it_was_measured_with_and_the_rescore(self):
        s = self._summary([case("a", measured_with=OLD), {"id": "n1", "pr": "#3", "label": "negative"}],
                          {"baseline": NEW, "candidate": NEW})
        text = "\n".join(evals.measured_lines(s))
        self.assertIn("Measured with: claude 2.1.332, model claude-sonnet-5-5.", text)
        self.assertIn("Re-score required: `a` (tool_version/model)", text)
        both = "\n".join(evals.measured_lines({"measured_with": {"baseline": OLD, "candidate": NEW}}))
        self.assertIn("before claude 2.1.316, model claude-sonnet-5; after claude 2.1.332, model claude-sonnet-5-5", both)
        self.assertEqual(evals.measured_lines({}), [])


class RunnerHelpers(unittest.TestCase):
    def test_models_come_from_model_usage_of_the_json_result(self):
        m = runner()
        out = json.dumps({"type": "result", "modelUsage": {"claude-sonnet-5-5": {"inputTokens": 1}, "claude-haiku-4-5": {}}})
        self.assertEqual(m.run_models(out), ["claude-haiku-4-5", "claude-sonnet-5-5"])
        for bad in ("", "not json", json.dumps({"result": "x"}), json.dumps([1]), json.dumps({"modelUsage": "x"})):
            self.assertEqual(m.run_models(bad), [], bad)

    def test_tool_version_reads_the_cli_or_says_unknown(self):
        m = runner()
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        ok = os.path.join(d, "fake-ok")
        with open(ok, "w", encoding="utf-8") as f:
            f.write("#!/bin/sh\necho '2.1.332 (Claude Code)'\n")
        os.chmod(ok, 0o755)
        bad = os.path.join(d, "fake-bad")
        with open(bad, "w", encoding="utf-8") as f:
            f.write("#!/bin/sh\nexit 3\n")
        os.chmod(bad, 0o755)
        old = os.environ.get("AGENTIVE_CLAUDE")
        try:
            os.environ["AGENTIVE_CLAUDE"] = ok
            self.assertEqual(m.tool_version("claude"), "2.1.332")
            os.environ["AGENTIVE_CLAUDE"] = bad
            self.assertEqual(m.tool_version("claude"), "unknown")
            os.environ["AGENTIVE_CLAUDE"] = os.path.join(d, "missing")
            self.assertEqual(m.tool_version("claude"), "unknown")
        finally:
            if old is None:
                os.environ.pop("AGENTIVE_CLAUDE", None)
            else:
                os.environ["AGENTIVE_CLAUDE"] = old


if __name__ == "__main__":
    unittest.main()
