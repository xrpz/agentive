# agentive-unit: selftest.component
# design: decisions.accepted[TESTGUARD], spec.stop_hook, contracts[stop-state]
"""TESTGUARD in the Stop hook: the uncommitted and unpushed diff that weakens tests blocks the stop once per distinct set of facts;
a clean diff prints nothing; Codex gets a decision block; a run with no human is told to leave the reason in the PR body."""
import json
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_hooks import ENV, GIT_ENV, StopBase, jload, schema_errors  # noqa: E402

HEADLESS = dict(ENV, AGENTIVE_HEADLESS="1")
TEST_PY = "def test_total():\n    assert total([1, 2]) == 3\n    assert total([]) == 0\n\n\ndef test_other():\n    assert other() == 1\n"


class TestguardStopTest(StopBase):
    """A branch whose base (local main) already holds tests/test_a.py, so the working tree edits are the diff."""

    def git(self, *args, check=True):
        return subprocess.run(["git", "-C", self.repo] + list(args), check=check, capture_output=True, text=True, env=GIT_ENV)

    def write(self, rel, text):
        p = self.path(rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)

    def setUp(self):
        super().setUp()
        main = self.git("rev-parse", "main").stdout.strip()

        def back():
            self.git("checkout", "-q", "-f", "work", check=False)
            self.git("branch", "-q", "-D", "tg-work", check=False)
            self.git("update-ref", "refs/heads/main", main)
            self.git("clean", "-q", "-fd", "tests", check=False)
        self.addCleanup(back)
        self.git("checkout", "-q", "-b", "tg-work", "main")
        self.write("tests/test_a.py", TEST_PY)
        self.git("add", "tests/test_a.py")
        self.git("commit", "-q", "-m", "add tests")
        self.git("update-ref", "refs/heads/main", "HEAD")     # the base the hook compares with

    def stop(self, tool="claude", env=None, active=False):
        return self.hook("stop-verify.py", {"stop_hook_active": active}, tool=tool, env=env)

    def state(self):
        return jload(self.path(".agents/state/stop.json"))

    def weaken(self):
        self.write("tests/test_a.py", "import pytest\n\n\n@pytest.mark.skip\ndef test_total():\n    assert total([1, 2]) == 3\n\n\n"
                                      "def test_other():\n    assert other() == 1\n")

    def test_a_clean_diff_prints_nothing_about_tests(self):
        self.write("tests/test_a.py", TEST_PY + "\n\ndef test_third():\n    assert third() == 3\n")
        rc, out, err = self.stop()
        self.assertEqual(rc, 0, err)
        self.assertNotIn("TEST_WEAKENED", json.dumps(out) + err)
        self.assertNotIn("testguard_blocked", self.state())

    def test_a_weakened_diff_blocks_once_with_the_facts(self):
        self.weaken()
        rc, out, err = self.stop()
        self.assertEqual(rc, 2, (out, err))
        self.assertTrue(err.startswith("TEST_WEAKENED: the diff against the merge base weakens tests (2 fact(s)"), err)
        self.assertIn("tests/test_a.py:4 skip_marker_added: @pytest.mark.skip", err)
        self.assertIn("assertions_dropped: test test_total holds 1 assertion(s), the base version had 2", err)
        self.assertIn("NEXT=restore the tests; if a change is intended, state in your final answer why each one is intended", err)
        st = self.state()
        self.assertEqual(len(st["testguard_blocked"]), 1)
        self.assertEqual((st["consecutive_blocks"], st["total_blocks"]), (0, 0))        # the verify counters are not touched
        self.assertEqual(schema_errors("stop-state", st), [])
        rc, out, err = self.stop()                                                      # same facts: the answer was given, no second block
        self.assertEqual(rc, 0, err)
        self.assertNotIn("TEST_WEAKENED", json.dumps(out) + err)

    def test_stop_hook_active_does_not_switch_the_check_off(self):
        # a continuation after a failing verify is when a test gets weakened; the fingerprint is the loop guard
        self.weaken()
        rc, _, err = self.stop(active=True)
        self.assertEqual(rc, 2, err)
        self.assertEqual(self.stop(active=True)[0], 0)

    def test_new_facts_block_again_and_the_session_cap_holds(self):
        self.weaken()
        self.assertEqual(self.stop()[0], 2)
        self.write("tests/test_a.py", TEST_PY.replace("def test_other", "def helper_other"))
        rc, _, err = self.stop()
        self.assertEqual(rc, 2, err)
        self.assertIn("removed_test", err)
        self.write("tests/conftest.py", "collect_ignore = ['test_old.py']\n")
        self.assertEqual(self.stop()[0], 2)
        self.write("tests/test_a.py", "def test_total():\n    pass\n")
        rc, out, err = self.stop()                                                      # a fourth distinct set: cap of 3 reached
        self.assertEqual(rc, 0, err)
        self.assertEqual(len(self.state()["testguard_blocked"]), 3)

    def test_codex_gets_a_decision_block(self):
        self.weaken()
        rc, out, err = self.stop(tool="codex")
        self.assertEqual((rc, out["decision"]), (0, "block"))
        self.assertIn("TEST_WEAKENED:", out["reason"])

    def test_no_human_present_leaves_the_reason_for_the_pr(self):
        self.weaken()
        rc, _, err = self.stop(env=HEADLESS)
        self.assertEqual(rc, 2, err)
        self.assertIn("no human is present: if a change is intended, write why each one is intended in the PR body (/pr)", err)

    def test_deleting_the_test_file_and_unpushed_commits_count(self):
        os.unlink(self.path("tests/test_a.py"))
        rc, _, err = self.stop()
        self.assertEqual(rc, 2, err)
        self.assertIn("tests/test_a.py:1 deleted_test_file:", err)
        self.git("checkout", "-q", "--", "tests/test_a.py")
        self.weaken()
        self.git("commit", "-qam", "weaken")                                            # committed, not pushed: still the diff
        self.assertEqual(self.stop()[0], 2)

    def test_outside_git_and_a_failing_detector_never_fail_the_hook(self):
        sys.path.insert(0, os.path.join(self.repo, ".agents", "lib"))
        try:
            from agentive import testguard
        finally:
            sys.path.pop(0)
        self.assertEqual(testguard.detect_local("/nonexistent-dir-for-testguard", lambda p: True), [])


if __name__ == "__main__":
    unittest.main()
