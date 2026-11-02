# agentive-unit: selftest.component
# design: spec.review_pipeline.stages[RV8].rerun, spec.review_pipeline.severity_blocking.reply_line.fixed, docs/IMPLEMENTATION.md §6
"""E2E-61 (end-to-end repo PR #8, 2026-11-02): a reply to a finding starts a pull_request_review_comment run of agentive-review
while the push run (pull_request) of the same head is still reviewing. The reply run has no ai-review job, so its
agentive-independence job looked the review artifact up before the push run uploaded it ("Artifact not found") and failed.
The lookup now waits while a pull_request run of the workflow for the same head has not completed. E2E-60 is tested in
test_pipeline.py and test_findings.py."""
import contextlib
import io
import os
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers as h  # noqa: E402,F401

from test_labels import Env, load_bin  # noqa: E402

from agentive.review import github as gh  # noqa: E402

HEAD = "dd70c724d33bc0e2347dc141a2420acc15e43567"
RUN = {"id": 1, "name": "agentive-review", "path": ".github/workflows/agentive-review.yml", "event": "pull_request"}


class Clock:
    def __init__(self):
        self.t, self.slept = 0.0, []

    def now(self):
        return self.t

    def sleep(self, n):
        self.slept.append(n)
        self.t += n


class WaitForTheReviewArtifactTest(unittest.TestCase):
    def runs(self, *statuses, path=RUN["path"]):
        return {"workflow_runs": [dict(RUN, id=i, status=s, path=path) for i, s in enumerate(statuses, 1)]}

    def test_a_run_of_the_review_workflow_that_is_not_done_counts(self):
        for status in ("queued", "in_progress", "waiting", "pending", "requested"):
            with mock.patch.object(gh, "api", return_value=self.runs(status)):
                self.assertTrue(gh.review_run_in_progress("o/r", HEAD), status)
        with mock.patch.object(gh, "api", return_value=self.runs("completed", "completed")):
            self.assertFalse(gh.review_run_in_progress("o/r", HEAD))
        # another workflow of the same head (agentive-lint, reviewer-eval) never uploads the review artifact
        with mock.patch.object(gh, "api", return_value=self.runs("in_progress", path=".github/workflows/agentive-lint.yml")):
            self.assertFalse(gh.review_run_in_progress("o/r", HEAD))
        with mock.patch.object(gh, "api", return_value=self.runs("in_progress", path=gh.REVIEW_WORKFLOW + "@refs/heads/x")):
            self.assertTrue(gh.review_run_in_progress("o/r", HEAD))
        with mock.patch.object(gh, "api", side_effect=gh.GhError("boom")):
            self.assertFalse(gh.review_run_in_progress("o/r", HEAD))               # nothing known: nothing to wait for

    def test_waits_for_the_push_run_to_upload_then_extracts(self):
        clock, calls = Clock(), []
        answers = iter([False, False, False, True])               # not there, not there, not there, uploaded

        def download(slug, name, dest):
            calls.append((slug, name, dest))
            return next(answers)
        with mock.patch.object(gh, "download_artifact", side_effect=download), \
                mock.patch.object(gh, "review_run_in_progress", return_value=True):
            got = gh.fetch_review_artifact("o/r", HEAD, "/w", wait=900, sleep=clock.sleep, now=clock.now)
        self.assertTrue(got)
        self.assertEqual(calls[0], ("o/r", "agentive-review-" + HEAD, "/w"))
        self.assertEqual(len(calls), 4)
        self.assertEqual(clock.slept, [gh.WAIT_STEP] * 3)

    def test_gives_up_when_the_push_run_is_done_or_the_wait_is_over(self):
        clock = Clock()
        with mock.patch.object(gh, "download_artifact", return_value=False), mock.patch.object(gh, "review_run_in_progress", return_value=False):
            self.assertFalse(gh.fetch_review_artifact("o/r", HEAD, "/w", wait=900, sleep=clock.sleep, now=clock.now))
        self.assertEqual(clock.slept, [])                                          # no run is going: it never comes, do not wait
        with mock.patch.object(gh, "download_artifact", return_value=False), mock.patch.object(gh, "review_run_in_progress", return_value=True):
            self.assertFalse(gh.fetch_review_artifact("o/r", HEAD, "/w", wait=60, sleep=clock.sleep, now=clock.now))
        self.assertEqual(sum(clock.slept), 60)                                     # bounded
        clock = Clock()
        with mock.patch.object(gh, "download_artifact", return_value=False) as dl, mock.patch.object(gh, "review_run_in_progress", return_value=True):
            self.assertFalse(gh.fetch_review_artifact("o/r", HEAD, "/w", wait=0, sleep=clock.sleep, now=clock.now))
        self.assertEqual((dl.call_count, clock.slept), (1, []))                    # wait 0: one look, as before

    def test_only_a_reply_run_waits(self):
        self.assertEqual(gh.wait_seconds({"GITHUB_EVENT_NAME": "pull_request_review_comment"}), gh.WAIT_SECONDS)
        for ev in ("pull_request", "merge_group", None):
            self.assertEqual(gh.wait_seconds({"GITHUB_EVENT_NAME": ev} if ev else {}), 0, ev)
        self.assertEqual(gh.wait_seconds({"GITHUB_EVENT_NAME": "pull_request_review_comment", "AGENTIVE_REVIEW_WAIT": "30"}), 30)
        self.assertEqual(gh.wait_seconds({"GITHUB_EVENT_NAME": "pull_request_review_comment", "AGENTIVE_REVIEW_WAIT": "0"}), 0)
        self.assertEqual(gh.wait_seconds({"GITHUB_EVENT_NAME": "pull_request_review_comment", "AGENTIVE_REVIEW_WAIT": "x"}), gh.WAIT_SECONDS)

    def test_the_independence_command_waits_only_in_a_reply_run(self):
        mod = load_bin()
        pr = {"number": 6, "title": "t", "body": "plain", "author": "dev", "author_type": "User", "author_association": "OWNER", "is_fork": False,
              "labels": [], "base_sha": "b" * 40, "head_sha": HEAD, "base_ref": "main"}
        for event, want in (("pull_request_review_comment", gh.WAIT_SECONDS), ("pull_request", 0)):
            with tempfile.TemporaryDirectory() as work, Env(GITHUB_REPOSITORY="o/r", GITHUB_EVENT_NAME=event, AGENTIVE_REVIEW_WAIT=None), \
                    mock.patch.object(mod.gh, "pr_context", return_value=pr), \
                    mock.patch.object(mod.gh, "fetch_review_artifact", return_value=False) as fetch, contextlib.redirect_stdout(io.StringIO()):
                mod.cmd_independence(types.SimpleNamespace(repo_slug=None, pr_json=None, pr=6, work=work))
            self.assertEqual(fetch.call_args.args[1:3], (HEAD, work), event)
            self.assertEqual(fetch.call_args.args[3], want, event)


if __name__ == "__main__":
    unittest.main()
