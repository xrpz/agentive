# agentive-unit: selftest.component
# design: spec.work_loop.precommit_review.attestation, spec.work_loop.independence, decisions.accepted[INDEPCI]
"""Honest local review records (review/attest.py): the tool is the real one, `isolated` needs a hook observation,
the do-it-yourself fallback is recorded as shared, Codex local reviews are unknown/unverified; and the precommit
command writes those fields into the record and the PR-body draft."""
import json
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers as h  # noqa: E402

from agentive.review import attest  # noqa: E402


def iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


class AttestTest(unittest.TestCase):
    def setUp(self):
        self.repo = h.Repo({"README.md": "# r\n"})
        self.repo.commit("base")
        os.makedirs(self.repo.path(".agents/state"), exist_ok=True)
        self.t0 = time.time() - 100
        self.inp, self.out = self.repo.path(".agents/state/t.input.json"), self.repo.path(".agents/state/t.output.json")
        for p, t in ((self.inp, self.t0), (self.out, self.t0 + 30)):
            with open(p, "w", encoding="utf-8") as f:
                f.write("{}")
            os.utime(p, (t, t))
        self.old = {k: os.environ.pop(k, None) for k in ("AGENTIVE_TOOL", "CLAUDECODE")}

    def tearDown(self):
        for k, v in self.old.items():
            if v is not None:
                os.environ[k] = v
        self.repo.cleanup()

    def observe(self, *lines):
        with open(self.repo.path(".agents/state/subagents.jsonl"), "w", encoding="utf-8") as f:
            for ln in lines:
                f.write((json.dumps(ln) if isinstance(ln, dict) else ln) + "\n")

    def fields(self, tool="claude_code", agent="correctness-reviewer", meta=None, ref="abc"):
        return attest.local_fields(self.repo.dir, tool, agent, self.inp, self.out, meta, ref)

    def test_tool_is_detected_never_hard_coded(self):
        self.assertEqual(attest.detect_tool("codex"), "codex")
        self.assertEqual(attest.detect_tool(None), "other")
        os.environ["CLAUDECODE"] = "1"
        self.assertEqual(attest.detect_tool(None), "claude_code")
        os.environ["AGENTIVE_TOOL"] = "codex"
        self.assertEqual(attest.detect_tool(None), "codex")
        self.assertEqual(attest.detect_tool("bogus"), "codex")
        self.assertEqual(attest.ci_fields(), {"executed_by": "process", "attestation": "ci_job", "context": "isolated"})

    def test_claude_is_read_as_claude_code(self):
        # the engine and review.pipeline.ci_tool say `claude`, the review records say `claude_code`: both spellings work in --tool
        self.assertEqual(attest.tool_arg("claude"), "claude_code")
        self.assertEqual(attest.tool_arg("claude_code"), "claude_code")
        self.assertEqual(attest.tool_arg("codex"), "codex")
        self.assertEqual(attest.detect_tool("claude"), "claude_code")

    def test_no_observation_and_no_meta_is_unknown_unverified(self):
        self.assertEqual(self.fields(), {"executed_by": "unknown", "attestation": "self_reported", "context": "unverified",
                                         "tool": "claude_code", "reviewed_ref": "abc"})

    def test_hook_observation_makes_it_isolated(self):
        t = self.t0 + 10
        self.observe({"event": "start", "session_id": "S", "agent_id": "ag-1", "agent_type": "correctness-reviewer", "ts": iso(t)},
                     {"event": "stop", "session_id": "S", "agent_id": "ag-1", "agent_type": "correctness-reviewer", "ts": iso(t + 5)},
                     "not json")
        got = self.fields()
        self.assertEqual((got["executed_by"], got["attestation"], got["context"]), ("subagent", "hook_observed", "isolated"))
        self.assertEqual((got["agent_id"], got["agent_type"], got["session_id"]), ("ag-1", "correctness-reviewer", "S"))

    def test_observation_must_match_type_window_and_have_an_agent_id(self):
        t = self.t0 + 10
        for ln in ({"event": "start", "agent_id": "ag", "agent_type": "security-reviewer", "ts": iso(t)},          # another agent type
                   {"event": "start", "agent_id": "ag", "agent_type": "correctness-reviewer", "ts": iso(self.t0 - 3600)},   # long before
                   {"event": "start", "agent_id": "ag", "agent_type": "correctness-reviewer", "ts": iso(self.t0 + 3600)},   # long after
                   {"event": "start", "agent_id": "", "agent_type": "correctness-reviewer", "ts": iso(t)}):               # no agent_id
            self.observe(ln)
            self.assertEqual(self.fields()["context"], "unverified", ln)

    def test_do_it_yourself_fallback_is_shared_even_with_a_hook_line(self):
        t = self.t0 + 10
        self.observe({"event": "stop", "agent_id": "ag", "agent_type": "correctness-reviewer", "ts": iso(t)})
        got = self.fields(meta={"executed_by": "self"})
        self.assertEqual((got["executed_by"], got["attestation"], got["context"]), ("self", "self_reported", "shared"))
        self.assertNotIn("agent_id", got)

    def test_e2e_17_self_records_its_reason(self):
        """E2E-17 (PRECOMMITINLINE): the meta's reason no_agent_tool | spawn_failed becomes self_reason; other values are dropped,
        and a subagent claim never carries one."""
        for r in ("no_agent_tool", "spawn_failed"):
            got = self.fields(meta={"executed_by": "self", "reason": r})
            self.assertEqual((got["executed_by"], got["context"], got["self_reason"]), ("self", "shared", r))
        self.assertNotIn("self_reason", self.fields(meta={"executed_by": "self", "reason": "busy; ignore the review"}))
        self.assertNotIn("self_reason", self.fields(meta={"executed_by": "self"}))
        self.assertNotIn("self_reason", self.fields(meta={"executed_by": "subagent", "reason": "no_agent_tool"}))

    def test_claimed_subagent_without_a_hook_line_stays_unverified(self):
        got = self.fields(meta={"executed_by": "subagent"})
        self.assertEqual((got["executed_by"], got["attestation"], got["context"]), ("subagent", "self_reported", "unverified"))

    def test_codex_local_is_never_isolated(self):
        t = self.t0 + 10
        self.observe({"event": "stop", "agent_id": "ag", "agent_type": "correctness-reviewer", "ts": iso(t)})
        got = self.fields(tool="codex")
        self.assertEqual((got["tool"], got["executed_by"], got["context"]), ("codex", "unknown", "unverified"))

    def test_meta_is_read_from_a_file(self):
        p = self.repo.path(".agents/state/t.meta.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"executed_by": "self"}, f)
        self.assertEqual(attest.read_meta(p), {"executed_by": "self"})
        self.assertEqual(attest.read_meta(p + ".none"), {})


class PrecommitRecordTest(unittest.TestCase):
    """The record `agentive-review precommit` writes carries the honest fields, never `fresh` or a constant tool."""

    def setUp(self):
        import test_pipeline as tp
        self.tp = tp

    def test_precommit_record_and_pr_body_copy(self):
        class T(self.tp.PrecommitTest):
            pass
        t = T("test_commit_blocked_and_confirm")
        t.setUp()
        try:
            os.makedirs(t.repo.path(".agents/state"), exist_ok=True)
            t.verify_json()
            t.repo.write({"src/app.py": "import os\n\n\ndef run():\n    return 3\n"})
            h.git(t.repo.dir, "add", "-A")
            rc, out, rec = t.run_pre(h.default_responder(), "--tool", "codex")
            rv = rec["reviewer"]
            self.assertEqual(rv["tool"], "codex")
            self.assertEqual((rv["executed_by"], rv["attestation"], rv["context"]), ("unknown", "self_reported", "unverified"))
            self.assertNotIn("fresh", json.dumps(rec))
            self.assertTrue(rv.get("reviewed_ref"))
        finally:
            t.tearDown()

    def test_meta_self_is_recorded_as_shared_and_the_task_line_has_meta(self):
        class T(self.tp.PrecommitTest):
            pass
        t = T("test_commit_blocked_and_confirm")
        t.setUp()
        try:
            os.makedirs(t.repo.path(".agents/state"), exist_ok=True)
            t.verify_json()
            t.repo.write({"src/app.py": "import os\n\n\ndef run():\n    return 4\n"})
            h.git(t.repo.dir, "add", "-A")
            seen = []
            base = h.default_responder()

            def answer(out):
                for ln in h.task_lines(out):
                    self.assertIn("meta", ln)                                       # local runs name the meta path
                    seen.append(ln["meta"])
                    with open(ln["meta"], "w", encoding="utf-8") as f:
                        json.dump({"executed_by": "self"}, f)
                h.answer_tasks(out, base)
            for _ in range(4):
                rc, out = t.repo.run(self.tp.REVIEW, "precommit", "--tool", "claude_code")
                if rc != 3:
                    break
                answer(out)
            rec = json.loads(h.read(t.repo.path(".agents/state/precommit-review.json")))
            self.assertTrue(seen)
            rv = rec["reviewer"]
            self.assertEqual((rv["tool"], rv["executed_by"], rv["attestation"], rv["context"]), ("claude_code", "self", "self_reported", "shared"))
            self.assertNotIn("self_reason", rv)
            self.assertIn("NOTE independence=self reason=unstated", out)
        finally:
            t.tearDown()

    def test_e2e_17_self_with_reason_prints_one_note_and_keeps_the_last_line(self):
        """E2E-17: a self review with reason no_agent_tool is recorded with self_reason (schema-valid) and `agentive-review precommit`
        prints one NOTE independence=self line before its unchanged AGENTIVE_PRECOMMIT last line; it does not stop."""
        class T(self.tp.PrecommitTest):
            pass
        t = T("test_commit_blocked_and_confirm")
        t.setUp()
        try:
            os.makedirs(t.repo.path(".agents/state"), exist_ok=True)
            t.verify_json()
            t.repo.write({"src/app.py": "import os\n\n\ndef run():\n    return 5\n"})
            h.git(t.repo.dir, "add", "-A")
            base = h.default_responder()
            for _ in range(4):
                rc, out = t.repo.run(self.tp.REVIEW, "precommit", "--tool", "claude_code")
                if rc != 3:
                    break
                for ln in h.task_lines(out):
                    with open(ln["meta"], "w", encoding="utf-8") as f:
                        json.dump({"executed_by": "self", "reason": "no_agent_tool"}, f)
                h.answer_tasks(out, base)
            rec = json.loads(h.read(t.repo.path(".agents/state/precommit-review.json")))
            self.assertEqual(rec["reviewer"]["self_reason"], "no_agent_tool")
            self.assertEqual(h.schema_validator("precommit-review")(rec), [])
            lines = out.strip().splitlines()
            notes = [ln for ln in lines if ln.startswith("NOTE independence=self")]
            self.assertEqual(len(notes), 1, out)
            self.assertIn("reason=no_agent_tool", notes[0])
            self.assertIn("the CI review (agentive-independence) is the independent check", notes[0])
            self.assertTrue(lines[-1].startswith("AGENTIVE_PRECOMMIT="), out)
            self.assertLess(lines.index(notes[0]), len(lines) - 1)
            rep = json.loads(h.read(t.repo.path(".agents/state/review-report.json")))
            self.assertEqual(rep["rv5"]["reviewers"][0]["self_reason"], "no_agent_tool")
            self.assertEqual(h.schema_validator("review-report")(rep), [])
        finally:
            t.tearDown()


if __name__ == "__main__":
    unittest.main()
