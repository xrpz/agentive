# agentive-unit: selftest.component
# design: spec.review_pipeline.stages[RV1], spec.review_pipeline.stages[RV8], docs/IMPLEMENTATION.md §6
"""E2E-64 (end-to-end repo PR #2, 2026-11-02): the first summary comment said `Checks: lint not reported · test not reported`.
Cause (job logs of the gate runs 125357318791 / 125357450110): an `edited` run (the /pr publish edited the body seconds after the
PR was opened) has no outer-verbs job (skipped on edited and labeled), so its ai-review job and its gate got OUTER empty and no
earlier report of the head (the push run had not uploaded its review artifact yet): rv1.checks held no lint and test.
The results of the head are in the push run's artifact `agentive-outer-verbs-<head sha>`; an edited / labeled / reply run now
looks them up by name (waiting, bounded, while another pull_request run of the head is going, never for itself). When there
is still none, the comment says `pending`, not `not reported`."""
import contextlib
import io
import json
import os
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers as h  # noqa: E402,F401

from test_labels import Env, load_bin  # noqa: E402
from test_e2e_60_61 import Clock, HEAD, RUN  # noqa: E402

from agentive.review import github as gh  # noqa: E402
from agentive.review import post  # noqa: E402

OUTER = [{"name": "lint", "status": "pass"}, {"name": "typecheck", "status": "skipped"}, {"name": "test", "status": "pass"}]


class OuterArtifactLookupTest(unittest.TestCase):
    def test_wait_applies_to_edited_labeled_and_reply_runs_only(self):
        self.assertEqual(gh.outer_wait_seconds({"GITHUB_EVENT_NAME": "pull_request", "AGENTIVE_EVENT_ACTION": "edited"}), gh.WAIT_SECONDS)
        self.assertEqual(gh.outer_wait_seconds({"GITHUB_EVENT_NAME": "pull_request", "AGENTIVE_EVENT_ACTION": "labeled"}), gh.WAIT_SECONDS)
        self.assertEqual(gh.outer_wait_seconds({"GITHUB_EVENT_NAME": "pull_request_review_comment"}), gh.WAIT_SECONDS)
        for action in ("opened", "synchronize", "reopened"):
            self.assertEqual(gh.outer_wait_seconds({"GITHUB_EVENT_NAME": "pull_request", "AGENTIVE_EVENT_ACTION": action}), 0, action)
        self.assertEqual(gh.outer_wait_seconds({}), 0)
        self.assertEqual(gh.outer_wait_seconds({"GITHUB_EVENT_NAME": "pull_request", "AGENTIVE_EVENT_ACTION": "edited", "AGENTIVE_REVIEW_WAIT": "0"}), 0)

    def test_a_run_never_waits_for_itself(self):
        runs = {"workflow_runs": [dict(RUN, id=77, status="in_progress")]}
        with mock.patch.object(gh, "api", return_value=runs):
            self.assertTrue(gh.review_run_in_progress("o/r", HEAD))
            self.assertFalse(gh.review_run_in_progress("o/r", HEAD, exclude_run="77"))
            self.assertTrue(gh.review_run_in_progress("o/r", HEAD, exclude_run="78"))

    def test_the_artifact_is_looked_up_by_name_and_waited_for_while_the_push_run_goes(self):
        clock, names = Clock(), []
        answers = iter([False, False, True])

        def download(slug, name, dest):
            names.append(name)
            return next(answers)

        with mock.patch.object(gh, "download_artifact", side_effect=download), \
                mock.patch.object(gh, "review_run_in_progress", return_value=True) as going:
            got = gh.fetch_outer_artifact("o/r", HEAD, "/w", wait=900, exclude_run="5", sleep=clock.sleep, now=clock.now)
        self.assertTrue(got)
        self.assertEqual(names, [f"agentive-outer-verbs-{HEAD}"] * 3)
        self.assertEqual(clock.slept, [gh.WAIT_STEP] * 2)
        self.assertEqual(going.call_args.args[2], "5")

    def test_no_artifact_and_no_other_run_gives_up_at_once(self):
        clock = Clock()
        with mock.patch.object(gh, "download_artifact", return_value=False), mock.patch.object(gh, "review_run_in_progress", return_value=False):
            self.assertFalse(gh.fetch_outer_artifact("o/r", HEAD, "/w", wait=900, sleep=clock.sleep, now=clock.now))
        self.assertEqual(clock.slept, [])


class EditedRunChecksTest(unittest.TestCase):
    """ci_context of an edited run: no --outer-results file, no earlier report -> the checks come from the push run's artifact."""

    def context(self, fetch, outer_file=None, prior=None, env=None):
        mod = load_bin()
        pr = {"number": 2, "title": "t", "body": "b", "author": "dev", "author_type": "User", "labels": [], "base_sha": "b" * 40,
              "head_sha": HEAD, "base_ref": "main", "state": "open", "merged": False}
        with tempfile.TemporaryDirectory() as tmp:
            work = os.path.join(tmp, "work")
            os.makedirs(work)
            checks = os.path.join(tmp, "checks.json")
            with open(checks, "w") as f:
                json.dump([{"name": "agentive-independence", "status": "pass"}], f)
            if prior:
                with open(os.path.join(work, "review-report.json"), "w") as f:
                    json.dump({"rv1": {"checks": prior}}, f)
            a = types.SimpleNamespace(repo_slug="o/r", pr_json=None, pr=2, comments_json=None, work=work, checks=checks, fetch_prior=True,
                                      outer_results=outer_file or os.path.join(tmp, "missing", "outer-verbs.json"), cmd="gate", event=None)
            seen = {}
            with Env(GITHUB_EVENT_NAME="pull_request", AGENTIVE_EVENT_ACTION="edited", GITHUB_RUN_ID="9", AGENTIVE_REVIEW_WAIT=None, **(env or {})), \
                    mock.patch.object(mod.gh, "pr_context", return_value=pr), mock.patch.object(mod.gh, "review_comments", return_value=[]), \
                    mock.patch.object(mod.gh, "fetch_review_artifact", return_value=False), \
                    mock.patch.object(mod.gh, "fetch_outer_artifact", side_effect=lambda *x, **k: fetch(work, seen, *x)), \
                    mock.patch.object(mod, "ensure_commit"), mock.patch.object(mod.gitio, "merge_base", return_value="m" * 40), \
                    mock.patch.object(mod.pipeline, "Ctx", side_effect=lambda *x, **k: types.SimpleNamespace(**k)), \
                    contextlib.redirect_stdout(io.StringIO()):
                ctx, _, _ = mod.ci_context(a, "/nonexistent", allow_tasks=False)
            return ctx, seen

    @staticmethod
    def artifact(work, seen, slug, head, dest, wait=0, exclude=None, *rest):
        seen.update(head=head, wait=wait, exclude=exclude)
        os.makedirs(dest, exist_ok=True)
        with open(os.path.join(dest, "outer-verbs.json"), "w") as f:
            json.dump(OUTER, f)
        return True

    def test_lint_and_test_come_from_the_push_runs_artifact(self):
        ctx, seen = self.context(lambda work, seen, *x: self.artifact(work, seen, *x))
        got = {c["name"]: c["status"] for c in ctx.checks}
        self.assertEqual(got, {"agentive-independence": "pass", "lint": "pass", "typecheck": "skipped", "test": "pass"})
        self.assertEqual(seen["head"], HEAD)
        self.assertEqual(seen["wait"], gh.WAIT_SECONDS)             # an edited run waits
        self.assertEqual(seen["exclude"], "9")                       # but never for its own run
        self.assertEqual([c["name"] for c in ctx.outer_results], ["lint", "typecheck", "test"])

    def test_no_artifact_leaves_the_checks_out_and_does_not_fail(self):
        ctx, _ = self.context(lambda *x: False)
        self.assertEqual([c["name"] for c in ctx.checks], ["agentive-independence"])

    def test_an_earlier_report_of_the_head_is_enough(self):
        calls = []
        ctx, _ = self.context(lambda *x: calls.append(x) or False, prior=[{"name": "lint", "status": "pass"}, {"name": "test", "status": "fail"}])
        self.assertEqual(calls, [])
        self.assertEqual({c["name"]: c["status"] for c in ctx.checks}["test"], "fail")

    def test_an_outer_results_file_of_this_run_is_used_as_before(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "outer-verbs.json")
            with open(path, "w") as f:
                json.dump(OUTER, f)
            calls = []
            ctx, _ = self.context(lambda *x: calls.append(x) or False, outer_file=path)
        self.assertEqual(calls, [])


class PendingWordTest(unittest.TestCase):
    def report(self, checks):
        return {"rv1": {"checks": checks}, "rv8": {"required_checks": {"required": ["lint", "test"], "not_required": [], "source": "framework default"}}}

    def test_a_check_without_a_result_is_pending_not_not_reported(self):
        rep = self.report([])
        self.assertEqual(post.checks_line(rep), "lint pending · test pending")
        self.assertIn("- required checks: lint (pending), test (pending) [framework default]", post.required_lines(rep))

    def test_reported_checks_keep_their_words(self):
        rep = self.report([{"name": "lint", "status": "pass"}, {"name": "test", "status": "fail"}])
        self.assertEqual(post.checks_line(rep), "lint passed · test failed")
        self.assertIn("- required checks: lint (pass), test (fail) [framework default]", post.required_lines(rep))


if __name__ == "__main__":
    unittest.main()
