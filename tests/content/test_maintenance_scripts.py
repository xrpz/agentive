# agentive-unit: selftest.component
# design: spec.maintenance.harvest.distill, spec.maintenance.janitor.gate, spec.maintenance.janitor.label, contracts[harvest-queue]
"""queue-read.sh (distill), gate.sh and label-pr.sh (janitor)."""
import json
import os
import time
import unittest

from helpers import Sandbox, read, schema_errors, write


def entry(sid, transcript, **sig):
    return {"session_id": sid, "transcript_path": transcript, "reason": "prompt_input_exit", "at": "2026-10-28T00:00:00Z",
            "signals": sig or {"stop_blocks": 3}}


class QueueRead(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        old = write(self.sb.tmp, "t-old.jsonl", "{}\n")
        os.utime(old, (time.time() - 120, time.time() - 120))
        fresh = write(self.sb.tmp, "t-new.jsonl", "{}\n")
        self.entries = [entry("s-old", old), entry("s-new", fresh, verify_fail_streak=2),
                        entry("s-gone", os.path.join(self.sb.tmp, "missing.jsonl"),
                              repeated_command=[{"normalized": "npm run build", "count": 3}])]
        for e in self.entries:
            self.assertEqual(schema_errors("harvest-queue", e), [])
        lines = [json.dumps(e) for e in self.entries] + ["not json", json.dumps({"session_id": "x"})]
        self.q = write(self.sb.repo, ".agents/state/harvest-queue.jsonl", "\n".join(lines) + "\n")

    def tearDown(self):
        self.sb.close()

    def run_q(self, *args):
        return self.sb.run(["sh", self.sb.script("distill", "queue-read.sh"), *args])

    def test_lists_ready_entries(self):
        p = self.run_q()
        self.assertEqual(p.returncode, 0, p.stdout)
        out = p.stdout
        self.assertIn('"session_id": "s-old"', out)
        self.assertIn('"session_id": "s-gone"', out)
        self.assertIn("WAIT line=2 session=s-new", out)
        self.assertIn("INVALID line=4 not JSON", out)
        self.assertIn("INVALID line=5 missing", out)
        self.assertIn("ready=2 waiting=1 invalid=2", out.splitlines()[-1])

    def test_done_removes_only_named_entries(self):
        p = self.run_q("--done", "s-old", "s-gone")
        self.assertIn("QUEUE removed=2 left=3", p.stdout)
        left = read(self.q).splitlines()
        self.assertEqual(len(left), 3)
        self.assertEqual(json.loads(left[0])["session_id"], "s-new")
        self.assertIn("not json", left)

    def test_empty_queue(self):
        os.remove(self.q)
        p = self.run_q()
        self.assertIn("ready=0 waiting=0 invalid=0; NEXT=none", p.stdout)


class JanitorGate(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()

    def tearDown(self):
        self.sb.close()

    def gate(self, routes, kind="lint-fix"):
        return self.sb.run(["sh", self.sb.script("janitor", "gate.sh"), kind], env=self.sb.fake_gh(routes))

    def test_go_when_no_open_pr(self):
        p = self.gate([(r"issues", {"stdout": ""})])
        self.assertEqual((p.returncode, p.stdout.strip()), (0, "GO lint-fix"))
        call = self.sb.gh_calls()[0]["args"]
        self.assertIn("labels=agentive-janitor:lint-fix", call)
        self.assertIn("state=open", call)
        self.assertIn("-X", call)

    def test_skip_when_one_is_open(self):
        p = self.gate([(r"issues", {"stdout": "42\n"})])
        self.assertEqual((p.returncode, p.stdout.strip().splitlines()[-1]), (1, "SKIP lint-fix: #42 open"))

    def test_errors(self):
        p = self.gate([(r"issues", {"stdout": "", "rc": 1})])
        self.assertEqual(p.returncode, 2)
        self.assertIn("NEXT=", p.stdout)
        p = self.gate([], kind="Bad Kind")
        self.assertEqual(p.returncode, 2)


class JanitorLabel(unittest.TestCase):
    """SKILLGUIDE: label-pr.sh adds agentive-janitor:<kind> through REST and reads it back; one retry; clear failure line."""

    def setUp(self):
        self.sb = Sandbox()

    def tearDown(self):
        self.sb.close()

    def label(self, routes, kind="lint-fix", pr="7"):
        return self.sb.run(["sh", self.sb.script("janitor", "label-pr.sh"), kind, pr], env=self.sb.fake_gh(routes))

    def posts(self):
        return [c["args"] for c in self.sb.gh_calls() if "POST" in c["args"]]

    def test_adds_and_confirms_the_label(self):
        p = self.label([(r"-X POST .*issues/7/labels", {"stdout": ""}),
                        (r"-X GET .*issues/7/labels", {"stdout": "bug\nagentive-janitor:lint-fix\n"})])
        self.assertEqual((p.returncode, p.stdout.strip()), (0, "LABELED agentive-janitor:lint-fix pr=7"))
        self.assertEqual(len(self.posts()), 1)
        self.assertIn("labels[]=agentive-janitor:lint-fix", self.posts()[0])
        self.assertTrue(all(c["args"][:1] != ["pr"] for c in self.sb.gh_calls()))

    def test_repeats_the_post_once_when_the_label_is_missing(self):
        p = self.label([(r"-X POST .*issues/7/labels", {"stdout": ""}),
                        (r"-X GET .*issues/7/labels", {"seq": [{"stdout": "bug\n"}, {"stdout": "agentive-janitor:lint-fix\n"}]})])
        self.assertEqual((p.returncode, p.stdout.strip()), (0, "LABELED agentive-janitor:lint-fix pr=7"))
        self.assertEqual(len(self.posts()), 2)

    def test_a_label_that_stays_missing_fails_with_a_next_line(self):
        p = self.label([(r"-X POST .*issues/7/labels", {"stdout": ""}), (r"-X GET .*issues/7/labels", {"stdout": "bug\n"})])
        self.assertEqual(p.returncode, 2)
        last = p.stdout.strip().splitlines()[-1]
        self.assertTrue(last.startswith("AGENTIVE_FAIL tool=janitor-label reason=label_missing; NEXT="), last)
        self.assertEqual(len(self.posts()), 2)

    def test_a_similar_label_does_not_count(self):
        p = self.label([(r"-X POST .*issues/7/labels", {"stdout": ""}),
                        (r"-X GET .*issues/7/labels", {"stdout": "agentive-janitor:lint-fix-2\nxagentive-janitor:lint-fix\n"})])
        self.assertEqual(p.returncode, 2)

    def test_bad_arguments(self):
        for kind, pr in (("Bad Kind", "7"), ("lint-fix", ""), ("lint-fix", "x7")):
            p = self.label([], kind=kind, pr=pr)
            self.assertEqual(p.returncode, 2, (kind, pr))
            self.assertIn("NEXT=", p.stdout)
        self.assertEqual(self.sb.gh_calls(), [])


if __name__ == "__main__":
    unittest.main()
