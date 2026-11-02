# agentive-unit: selftest.component
# design: spec.work_loop.precommit_review.attestation, decisions.accepted[INDEPCI]
"""The SubagentStart / SubagentStop observation hook (.agents/hooks/subagent-observe.py): one JSON line per event in
.agents/state/subagents.jsonl, nothing printed, never blocking, small file; and the settings wiring."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PAYLOAD = os.path.join(ROOT, "plugins", "claude-code", "agentive", "payload", "repo-layer")
HOOK = os.path.join(PAYLOAD, "dot-agents", "hooks", "subagent-observe.py")
SETTINGS = os.path.join(PAYLOAD, "dot-claude", "settings.json")


class SubagentObserveTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="agentive-subagent-")
        self.addCleanup(shutil.rmtree, self.d, True)
        os.makedirs(os.path.join(self.d, ".agents"))
        with open(os.path.join(self.d, ".agents", "manifest.yaml"), "w", encoding="utf-8") as f:
            f.write("schema_version: 1\n")
        # the hook imports agentive from ../lib relative to its own location: run the installed copy
        self.hook_dir = os.path.join(self.d, ".agents", "hooks")
        shutil.copytree(os.path.join(PAYLOAD, "dot-agents", "hooks"), self.hook_dir)
        shutil.copytree(os.path.join(PAYLOAD, "dot-agents", "lib"), os.path.join(self.d, ".agents", "lib"))
        self.hook = os.path.join(self.hook_dir, "subagent-observe.py")

    def run_hook(self, payload, *args, root=None):
        r = subprocess.run([sys.executable, self.hook] + list(args), input=json.dumps(dict(payload, cwd=root or self.d)),
                           capture_output=True, text=True, timeout=30)
        return r

    def lines(self):
        path = os.path.join(self.d, ".agents", "state", "subagents.jsonl")
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as f:
            return [json.loads(ln) for ln in f.read().splitlines()]

    def test_start_and_stop_lines(self):
        for ev in ("SubagentStart", "SubagentStop"):
            r = self.run_hook({"hook_event_name": ev, "session_id": "S1", "agent_id": "a-1", "agent_type": "correctness-reviewer"})
            self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))
        got = self.lines()
        self.assertEqual([(x["event"], x["session_id"], x["agent_id"], x["agent_type"], x["tool"]) for x in got],
                         [("start", "S1", "a-1", "correctness-reviewer", "claude_code"), ("stop", "S1", "a-1", "correctness-reviewer", "claude_code")])
        self.assertRegex(got[0]["ts"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")

    def test_tool_flag_names_codex(self):
        self.run_hook({"hook_event_name": "SubagentStart", "agent_id": "x", "agent_type": "t"}, "--tool", "codex")
        self.assertEqual(self.lines()[0]["tool"], "codex")

    def test_other_events_and_repos_without_a_manifest_write_nothing(self):
        self.run_hook({"hook_event_name": "PreToolUse", "agent_id": "x"})
        self.assertEqual(self.lines(), [])
        bare = tempfile.mkdtemp(prefix="agentive-subagent-bare-")
        self.addCleanup(shutil.rmtree, bare, True)
        r = self.run_hook({"hook_event_name": "SubagentStart", "agent_id": "x"}, root=bare)
        self.assertEqual(r.returncode, 0)
        self.assertFalse(os.path.exists(os.path.join(bare, ".agents", "state", "subagents.jsonl")))

    def test_garbage_input_never_blocks(self):
        r = subprocess.run([sys.executable, self.hook], input="not json", capture_output=True, text=True, timeout=30, cwd=self.d)
        self.assertEqual((r.returncode, r.stdout), (0, ""))

    def test_the_file_is_trimmed(self):
        state = os.path.join(self.d, ".agents", "state")
        os.makedirs(state)
        with open(os.path.join(state, "subagents.jsonl"), "w", encoding="utf-8") as f:
            for i in range(3000):
                f.write(json.dumps({"event": "start", "agent_id": f"old-{i}", "agent_type": "t", "ts": "2026-10-28T00:00:00Z", "pad": "x" * 60}) + "\n")
        self.run_hook({"hook_event_name": "SubagentStop", "agent_id": "new", "agent_type": "t"})
        got = self.lines()
        self.assertLessEqual(len(got), 200)
        self.assertEqual(got[-1]["agent_id"], "new")

    def test_settings_wire_both_events(self):
        with open(SETTINGS, encoding="utf-8") as f:
            s = json.load(f)
        for ev in ("SubagentStart", "SubagentStop"):
            hooks = [h for grp in s["hooks"][ev] for h in grp["hooks"]]
            self.assertEqual(len(hooks), 1, ev)
            self.assertEqual(hooks[0]["command"], "python3")
            self.assertTrue(any(a.endswith(".agents/hooks/subagent-observe.py") for a in hooks[0]["args"]))
            self.assertLessEqual(hooks[0]["timeout"], 10)
            self.assertNotEqual(hooks[0].get("onFailure"), "block")        # an observation never blocks
        self.assertTrue(os.access(HOOK, os.X_OK))


if __name__ == "__main__":
    unittest.main()
