# agentive-unit: selftest.component
# design: spec.repo_layer.files[f-ci], spec.review_pipeline.stages[RV8].rerun, spec.review_pipeline.gate_protection, spec.outer_loop.triggers, spec.review_pipeline.reviewer_eval.ci, spec.framework_repo.self_tests
"""The user-repo workflow templates (src/repo-layer/dot-github/workflows/) and the framework repo's own workflows:
triggers, job names, no schedule, the gate never skipped or path-filtered, actions the design names."""
import os
import re
import sys
import unittest

import yaml  # dev-time only

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "src", "repo-layer", "dot-agents", "lib"))
from agentive import ayaml  # noqa: E402

WF = os.path.join(ROOT, "src", "repo-layer", "dot-github", "workflows")
DESIGN_JOBS = {"outer-verbs", "ai-review", "agentive-independence", "agentive-review-gate", "agentive-lint", "dependency-review", "reviewer-eval"}
BANNED = r"\bun" + r"attended\b|" + "\ubb34\uc778"      # AGENTS.md Words; spelled so this file does not match itself
AGENT_TRIGGERS = {"schedule", "workflow_run", "issues", "issue_comment", "repository_dispatch", "workflow_dispatch", "pull_request_target"}


def load(path):
    with open(path, encoding="utf-8") as f:
        text = f.read()
    return text, ayaml.loads(text)


class UserWorkflowsTest(unittest.TestCase):
    def setUp(self):
        self.files = {f: load(os.path.join(WF, f)) for f in sorted(os.listdir(WF))}

    def test_parse_identically_with_ayaml_and_pyyaml(self):
        for name, (text, doc) in self.files.items():
            self.assertEqual(doc, yaml.safe_load(text), name)       # the engine merges .agents/custom overrides with ayaml
            self.assertIn("agentive-unit: ", text.splitlines()[0])

    def test_jobs_are_the_design_jobs(self):
        jobs = {j for _, d in self.files.values() for j in d["jobs"]}
        self.assertEqual(jobs, DESIGN_JOBS)

    def test_no_trigger_starts_agent_work(self):
        for name, (_, doc) in self.files.items():
            self.assertFalse(set(doc["on"]) & AGENT_TRIGGERS, name)   # G2: PR events only, never a schedule
            for ev in doc["on"].values() if isinstance(doc["on"], dict) else []:
                self.assertFalse(isinstance(ev, dict) and ("paths" in ev or "paths-ignore" in ev), name)

    def test_gate_triggers_and_never_skipped(self):
        _, doc = self.files["agentive-review.yml"]
        on = doc["on"]
        self.assertEqual(on["pull_request"]["types"], ["opened", "synchronize", "reopened", "edited"])
        self.assertEqual(on["pull_request_review_comment"]["types"], ["created", "edited", "deleted"])
        self.assertIn("merge_group", on)
        self.assertNotIn("pull_request_review", on)
        gate = doc["jobs"]["agentive-review-gate"]
        self.assertEqual(gate["if"], "always()")
        self.assertEqual(set(gate["needs"]), {"outer-verbs", "dependency-review", "ai-review", "agentive-independence"})
        self.assertEqual(gate["permissions"]["issues"], "write")         # demotion issue
        self.assertEqual(gate["permissions"]["pull-requests"], "write")  # body, labels, auto-merge
        run = "\n".join(s.get("run", "") for s in gate["steps"])
        self.assertIn('git worktree add --detach "$RUNNER_TEMP/agentive-base" "$BASE_SHA"', run)   # gate script from the base
        self.assertIn("gate \"$PR_NUMBER\"", run)
        self.assertIn("--apply", run)

    def test_ai_review_uses_claude_code_action_and_the_pipeline(self):
        text, doc = self.files["agentive-review.yml"]
        steps = doc["jobs"]["ai-review"]["steps"]
        act = [s for s in steps if str(s.get("uses", "")).startswith("anthropics/claude-code-action")]
        self.assertEqual([s["uses"] for s in act], ["anthropics/claude-code-action@v1"])
        self.assertIn(".agents/bin/agentive-review ci", act[0]["with"]["prompt"])
        self.assertNotIn("--bare", text)
        self.assertNotIn("/code-review:code-review", text)
        dr = [s for s in doc["jobs"]["dependency-review"]["steps"] if "uses" in s][0]
        self.assertEqual((dr["uses"].split("@")[0], dr["with"]["fail-on-severity"]), ("actions/dependency-review-action", "critical"))

    def test_reviewer_eval_is_change_triggered_and_non_blocking(self):
        text, doc = self.files["reviewer-eval.yml"]
        self.assertEqual(set(doc["on"]), {"pull_request"})
        job = doc["jobs"]["reviewer-eval"]
        self.assertTrue(job["continue-on-error"])
        self.assertEqual(doc["permissions"]["pull-requests"], "write")
        self.assertIn("agentive-reviewer-eval detect", text)
        self.assertIn("--n 3", text)
        self.assertIn("--not-run", text)

    def test_terminology(self):
        for name, (text, _) in self.files.items():
            self.assertIsNone(re.search(BANNED, text, re.I), name)


class FrameworkWorkflowsTest(unittest.TestCase):
    def test_framework_ci_never_scheduled(self):
        d = os.path.join(ROOT, ".github", "workflows")
        for f in sorted(os.listdir(d)):
            _, doc = load(os.path.join(d, f))
            self.assertNotIn("schedule", doc["on"], f)


class TerminologyTest(unittest.TestCase):
    def test_owner_d_files(self):
        roots = [os.path.join(ROOT, "src", "repo-layer", "dot-agents", p) for p in ("bin", "agents", os.path.join("lib", "agentive", "review"), os.path.join("lib", "agentive", "lint"))]
        roots += [WF, os.path.join(ROOT, "tests", "review"), os.path.join(ROOT, "tests", "sample_repo"), os.path.join(ROOT, "docs", "impl")]
        for r in roots:
            for base, _, files in os.walk(r):
                for f in files:
                    if f.endswith((".pyc",)):
                        continue
                    p = os.path.join(base, f)
                    if r.endswith("impl") and f != "D.md":
                        continue
                    with open(p, encoding="utf-8", errors="replace") as fh:
                        self.assertIsNone(re.search(BANNED, fh.read(), re.I), p)


if __name__ == "__main__":
    unittest.main()
