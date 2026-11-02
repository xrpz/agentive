# agentive-unit: selftest.component
# design: spec.work_loop.ci_independence_check, spec.review_pipeline.stages[RV8], decisions.accepted[WFCHANGE], docs/IMPLEMENTATION.md §6
"""E2E-63 (end-to-end repo PR #1, 2026-11-02): a human merged the first install PR 40 seconds after it opened, while the workflow run
was still going. The Claude Code action had skipped its run (workflow validation: the install PR adds agentive-review.yml), which the
agentive-independence and gate jobs recognise by comparing the workflow blob with the default branch. After the merge the default
branch holds the same file, so the comparison found no skip: independence failed ("no review produced by the CI job") and the gate
blocked (failing_required_check: agentive-independence). Both jobs now end cleanly, with one line naming the reason, when the PR is
already merged or closed [design judgment]. An open PR is unchanged."""
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

HEAD = "75337f1baa00afad2e4dbc2f1bd6f161a51ae5ee"


def pr(**kw):
    base = {"number": 1, "title": "Install agentive", "body": "plain", "author": "xrpz", "author_type": "User", "author_association": "OWNER",
            "is_fork": False, "labels": [], "base_sha": "b" * 40, "head_sha": HEAD, "base_ref": "main", "state": "open", "merged": False}
    base.update(kw)
    return base


class FinishedPrTest(unittest.TestCase):
    def test_pr_finished(self):
        mod = load_bin()
        self.assertEqual(mod.pr_finished(pr(state="closed", merged=True)), "merged")
        self.assertEqual(mod.pr_finished(pr(state="closed", merged=False)), "closed")
        self.assertIsNone(mod.pr_finished(pr()))
        self.assertIsNone(mod.pr_finished({"number": 1}))          # a --pr-json without state: unchanged
        self.assertIsNone(mod.pr_finished(None))

    def run_independence(self, **kw):
        mod = load_bin()
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as work, Env(GITHUB_REPOSITORY="o/r", GITHUB_EVENT_NAME="pull_request", AGENTIVE_REVIEW_WAIT=None), \
                mock.patch.object(mod.gh, "pr_context", return_value=pr(**kw)), \
                mock.patch.object(mod.gh, "fetch_review_artifact", return_value=False) as fetch, contextlib.redirect_stdout(out):
            rc = mod.cmd_independence(types.SimpleNamespace(repo_slug=None, pr_json=None, pr=1, work=work))
        return rc, out.getvalue(), fetch

    def test_independence_of_a_merged_pr_ends_cleanly_with_one_reason_line(self):
        rc, out, fetch = self.run_independence(state="closed", merged=True)
        self.assertEqual(rc, 0)
        self.assertEqual(len(out.strip().splitlines()), 1, out)
        self.assertIn("AGENTIVE_INDEPENDENCE=skipped reason=pr_merged", out)
        self.assertIn("NEXT=none", out)
        fetch.assert_not_called()                                   # no artifact lookup, no wait

    def test_independence_of_a_closed_pr_ends_cleanly(self):
        rc, out, _ = self.run_independence(state="closed", merged=False)
        self.assertEqual(rc, 0)
        self.assertIn("reason=pr_closed", out)

    def test_independence_of_an_open_pr_still_fails_without_a_ci_review(self):
        """An agent PR (agentive-review block in the body) with no CI review is still a failure while the PR is open."""
        body = "```yaml agentive-review\nagentive-review:\n  version: 1\n```\n"
        mod = load_bin()
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as work, Env(GITHUB_REPOSITORY="o/r", GITHUB_EVENT_NAME="pull_request", AGENTIVE_REVIEW_WAIT=None), \
                mock.patch.object(mod.gh, "pr_context", return_value=pr(body=body)), \
                mock.patch.object(mod.gh, "fetch_review_artifact", return_value=False), \
                mock.patch.object(mod, "_skip", return_value=None), contextlib.redirect_stdout(out):
            rc = mod.cmd_independence(types.SimpleNamespace(repo_slug=None, pr_json=None, pr=1, work=work))
        self.assertNotIn("skipped reason=pr_", out.getvalue())
        self.assertNotEqual(rc, 0, out.getvalue())

    def test_gate_of_a_merged_pr_ends_cleanly_without_side_effects(self):
        mod = load_bin()
        out = io.StringIO()
        ctx = types.SimpleNamespace(work_dir="/nonexistent", checks=[])
        with mock.patch.object(mod, "ci_context", return_value=(ctx, "o/r", pr(state="closed", merged=True))), \
                mock.patch.object(mod.pipeline, "run", side_effect=AssertionError("the pipeline must not run")), \
                contextlib.redirect_stdout(out):
            rc = mod.cmd_gate(types.SimpleNamespace(apply=True))
        self.assertEqual(rc, 0)
        self.assertEqual(len(out.getvalue().strip().splitlines()), 1)
        self.assertIn("AGENTIVE_GATE=skipped reason=pr_merged", out.getvalue())


if __name__ == "__main__":
    unittest.main()
