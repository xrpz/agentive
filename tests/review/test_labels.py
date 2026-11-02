# agentive-unit: selftest.component
# design: spec.review_pipeline.convergence.label_trigger, spec.review_pipeline.stages[RV8].rerun, docs/IMPLEMENTATION.md §6
"""LABELTRIGGER (2026-10-29): a label run records trigger `labeled`, is not a new review round, `agentive:full-review` discards the
earlier task outputs of the head and is removed after the full review, and an unrelated label changes nothing."""
import importlib.machinery
import importlib.util
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers as h  # noqa: E402
from test_pipeline import PipelineBase, body  # noqa: E402

BIN = os.path.join(h.SRC, "dot-agents", "bin", "agentive-review")
FULL = "agentive:full-review"


def load_bin():
    loader = importlib.machinery.SourceFileLoader("agentive_review_bin", BIN)
    spec = importlib.util.spec_from_loader("agentive_review_bin", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class Env:
    """Set environment variables for a block and restore them."""

    def __init__(self, **kv):
        self.kv, self.old = kv, {}

    def __enter__(self):
        for k, v in self.kv.items():
            self.old[k] = os.environ.get(k)
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def __exit__(self, *a):
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class TriggerTest(unittest.TestCase):
    def test_trigger_handles_labeled_explicitly(self):
        mod = load_bin()
        a = type("A", (), {"event": None})()
        for action, want in (("opened", "opened"), ("synchronize", "synchronize"), ("reopened", "reopened"), ("edited", "edited"),
                             ("labeled", "labeled"), ("unlabeled", "synchronize"), (None, "synchronize")):
            with Env(GITHUB_EVENT_NAME="pull_request", AGENTIVE_EVENT_ACTION=action):
                self.assertEqual(mod._trigger(a), want, action)
        with Env(GITHUB_EVENT_NAME="pull_request_review_comment", AGENTIVE_EVENT_ACTION="created"):
            self.assertEqual(mod._trigger(a), "pull_request_review_comment")

    def test_label_event_reads_the_env_only_for_labeled(self):
        mod = load_bin()
        with Env(AGENTIVE_EVENT_ACTION="labeled", AGENTIVE_EVENT_LABEL=FULL):
            self.assertEqual(mod._label_event(), FULL)
        with Env(AGENTIVE_EVENT_ACTION="synchronize", AGENTIVE_EVENT_LABEL=FULL):
            self.assertIsNone(mod._label_event())

    def test_full_review_label_drops_task_outputs_and_round_but_keeps_the_checks(self):
        from agentive.review import pipeline
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "tasks"))
            for name in ("tasks/rv5-correctness-reviewer.output.json", "round.json", "review-report.json"):
                with open(os.path.join(d, name), "w", encoding="utf-8") as f:
                    f.write("{}")
            pipeline.reset_for_full_review(d)
            self.assertEqual(sorted(os.listdir(d)), ["review-report.json"])
            pipeline.reset_for_full_review(d)                                   # nothing left to drop: no error


class LabelRunTest(PipelineBase):
    def labeled_run(self, head, label, work, labels=None):
        orig = self.repo.run
        env = {"GITHUB_EVENT_NAME": "pull_request", "AGENTIVE_EVENT_ACTION": "labeled", "AGENTIVE_EVENT_LABEL": label}
        self.repo.run = lambda *a, env=None, _o=orig, _e=env: _o(*a, env=dict(_e, **(env or {})))
        try:
            return self.ci(head, h.default_responder(), body(), work=work, labels=labels if labels is not None else [label])
        finally:
            self.repo.run = orig

    def test_a_label_run_is_trigger_labeled_and_not_a_new_round(self):
        self.repo.write({"README.md": "# r\n\nMore words.\n"})
        head = self.repo.commit("docs")
        first, _, _ = self.ci(head, h.default_responder(), body(), work="w1")
        again, agents, _ = self.labeled_run(head, FULL, "w2")
        self.assertEqual(again["rv8"]["explainer"]["trigger"], "labeled")
        self.assertEqual(again["review_round"], first["review_round"])             # same head, same round
        self.assertEqual(again["rv4"]["tier"], first["rv4"]["tier"])

    def test_janitor_label_forces_tier_two_on_the_label_run(self):
        self.repo.write({"README.md": "# r\n\nMore words.\n"})
        head = self.repo.commit("docs")
        first, _, _ = self.ci(head, h.default_responder(), body(), work="w1", labels=[])
        self.assertEqual(first["rv4"]["tier"], 0)
        later, _, _ = self.labeled_run(head, "agentive-janitor:lint", "w2")        # the label arrives after the first run: it converges
        self.assertEqual(later["rv4"]["tier"], 2)
        self.assertFalse(later["rv8"]["auto_merge"])

    def test_full_review_label_is_removed_after_the_run_that_consumed_it(self):
        mod = load_bin()
        self.repo.write({"README.md": "# r\n\nMore words.\n"})
        head = self.repo.commit("docs")
        calls = {"removed": [], "comments": 0}
        gh = mod.gh
        saved = {k: getattr(gh, k) for k in ("remove_label", "upsert_issue_comment", "create_review_comment")}
        gh.remove_label = lambda slug, n, label: calls["removed"].append((slug, n, label))
        gh.upsert_issue_comment = lambda *a, **k: calls.__setitem__("comments", calls["comments"] + 1)
        gh.create_review_comment = lambda *a, **k: None
        cwd = os.getcwd()
        try:
            os.chdir(self.repo.dir)
            for labels, want in (([FULL], [("o/r", 7, FULL)]), (["bug"], []), ([], [])):
                calls["removed"].clear()
                pr = self.pr(head, body(), labels=labels)
                rc = mod.main(["ci", "7", "--pr-json", pr, "--work", self.repo.path(".agents/state/wl"), "--comments-json", self.comments([]),
                               "--repo-slug", "o/r", "--no-tasks", "--post"])
                self.assertEqual(rc, 0)
                self.assertEqual(calls["removed"], want, labels)
        finally:
            os.chdir(cwd)
            for k, v in saved.items():
                setattr(gh, k, v)
        self.assertGreaterEqual(calls["comments"], 3)


if __name__ == "__main__":
    unittest.main()
