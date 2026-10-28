# agentive-unit: selftest.component
# design: spec.maintenance.harvest.distill, spec.maintenance.janitor.gate, contracts[harvest-queue]
"""queue-read.sh (distill) and gate.sh (janitor)."""
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


if __name__ == "__main__":
    unittest.main()
