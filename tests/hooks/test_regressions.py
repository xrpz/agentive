# agentive-unit: selftest.component
# design: spec.guard, spec.init_apply.session_start, spec.handoff.reader, spec.init_apply.reprove_on_change, spec.enforcement.hooks.pre_tool_use_guard
"""Regression tests for the 2026-10-29 implementation verification findings (one test per finding id) in the hook layer:
V1-02 / V1-06 (agent-visible NEXT on SessionStart notices), V1-07 (DIRTY notice), V1-08 (no __pycache__ in the repo),
V1-09 (the `:ref` push refspec), V1-17 (missing or broken guard.yaml), SKILLPATHS (a .. path under an allowlist prefix)."""
import json
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_hooks import ENV, GIT_ENV, Repo, rtext  # noqa: E402

from agentive import guard  # noqa: E402


class SessionStartNoticeTest(Repo):
    def startup(self, env=None, tool="claude"):
        rc, out, err = self.hook("session-start-notice.py", {"source": "startup"}, tool=tool, env=env)
        self.assertEqual(rc, 0, err)
        return out["hookSpecificOutput"]["additionalContext"].splitlines()

    def write_state(self, name, obj):
        p = self.path(f".agents/state/{name}")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            json.dump(obj, f)
        self.addCleanup(lambda: os.path.exists(p) and os.unlink(p))

    def test_v1_06_handoff_line_carries_the_agent_next(self):
        from agentive import handoff
        path = handoff.write(self.repo, "no_progress", "tests keep failing", "fix the parser")
        self.addCleanup(lambda: os.path.exists(os.path.join(self.repo, path)) and os.unlink(os.path.join(self.repo, path)))
        line = next(x for x in self.startup() if x.startswith("HANDOFF="))
        self.assertIn("; NEXT=ask_user:resume|discard", line)
        self.assertIn("DO_NOT=start_other_work_before_the_answer", line)
        line = next(x for x in self.startup(env=dict(ENV, AGENTIVE_HEADLESS="1")) if x.startswith("HANDOFF="))
        self.assertTrue(line.endswith("; NEXT=none"), line)
        rc, out, _ = self.hook("session-start-notice.py", {"source": "clear"})
        self.assertNotIn("NEXT=", out["hookSpecificOutput"]["additionalContext"])           # clear: the bare HANDOFF line only

    def test_v1_02_pending_confirmation_line_ends_with_next(self):
        self.write_state("pending-confirmation.json", {"command": "/agentive:init", "framework_version": "0.1.0", "files": [
            {"path": ".claude/settings.json", "category": "claude_settings", "reason": "untrusted_folder",
             "next": "open the repo in an interactive Claude Code session once and trust the folder"}]})
        line = next(x for x in self.startup() if x.startswith("PENDING_CONFIRMATION="))
        self.assertIn("; NEXT=tell_user:open the repo in an interactive Claude Code session once and trust the folder", line)
        line = next(x for x in self.startup(env=dict(ENV, CI="true")) if x.startswith("PENDING_CONFIRMATION="))
        self.assertTrue(line.endswith("; NEXT=none"), line)

    def test_v1_07_dirty_slots_reach_the_agent(self):
        self.write_state("dirty.json", {"slots": [{"slot": "verbs.test", "file": "go.mod", "at": "2026-10-29T00:00:00Z"}]})
        line = next(x for x in self.startup() if x.startswith("DIRTY="))
        self.assertEqual(line, "DIRTY=verbs.test (stack evidence changed); NEXT=ask_user_to_run:/agentive:reprove; DO_NOT=edit_the_manifest_by_hand")
        line = next(x for x in self.startup(tool="codex") if x.startswith("DIRTY="))
        self.assertIn("ask_user_to_run:$agentive-update", line)


class GuardRegressionTest(Repo):
    def decision(self, command):
        rc, out, err = self.hook("pre-tool-use-guard.py", {"tool_name": "Bash", "tool_input": {"command": command}})
        self.assertEqual(rc, 0, err)
        if isinstance(out, dict):
            hso = out["hookSpecificOutput"]
            return hso["permissionDecision"], hso["permissionDecisionReason"]
        return None, None

    def test_v1_09_refspec_delete_is_denied_by_the_hook_and_no_rule_mixes_star_and_colon_star(self):
        for cmd in ("git push origin :old-branch", "git push origin +:old-branch", "git -C . push upstream :refs/heads/x"):
            d, reason = self.decision(cmd)
            self.assertEqual(d, "deny", cmd)
            self.assertTrue(reason.startswith("BLOCKED push-delete-refspec:"), reason)
        self.assertEqual(self.decision("git push origin work:work"), (None, None))
        self.assertEqual(self.decision("git push origin --delete old"), (None, None))       # ask class: permissions.ask prompts
        settings = json.loads(rtext(self.path(".claude/settings.json")))
        for rule in sum((settings["permissions"].get(k, []) for k in ("allow", "deny", "ask")), []):
            body = rule[rule.find("(") + 1:-1] if "(" in rule else ""
            self.assertFalse(body.endswith(":*") and "*" in body[:-2], f"{rule}: a trailing :* after another * is the legacy prefix form")
        self.assertIn("push-delete-refspec", [r["id"] for r in guard.not_expressible(guard.load_rules(self.repo))])

    def test_skillpaths_dot_dot_under_an_allowlist_prefix_is_denied(self):
        """SKILLPATHS: Bash(sh .agents/skills/*) matched `sh .agents/skills/../../evil.sh` (Claude Code 2.1.312), so the
        guard denies a .. path under the agentive prefixes: rendered into Claude permissions.deny and matched by the
        PreToolUse hook for Claude Code and Codex; the normal repo-root calls stay allowed."""
        bad = ["sh .agents/skills/../../evil.sh", "sh .agents/skills/pr/../../../x.sh draft", "bash .agents/skills/x/../../e.sh",
               ".agents/bin/../../evil", "X=1 sh .agents/skills/a/scripts/../../../e.sh", "bash -c 'sh .agents/skills/../x.sh'"]
        for cmd in bad:
            d, reason = self.decision(cmd)
            self.assertEqual(d, "deny", cmd)
            self.assertTrue(reason.startswith("BLOCKED agentive-path-traversal:"), reason)
            self.assertIn("NEXT:", reason)
            self.assertIn("through a `..` path segment. NEXT:", reason)      # E2E-05: the literal .. is kept
            self.assertNotIn("through . NEXT", reason)
            rc, out, _ = self.hook("pre-tool-use-guard.py", {"tool_name": "Bash", "tool_input": {"command": cmd}}, tool="codex")
            self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny", cmd)
        for cmd in ("sh .agents/skills/pr/scripts/pr-body.sh draft --tool claude_code --model x", ".agents/bin/verb test",
                    "sh .agents/skills/chain-spec/scripts/handoff.sh 'Delete old rows...?' 'decide'", ".agents/bin/agentive-lint rules --write"):
            self.assertEqual(self.decision(cmd), (None, None), cmd)
        settings = json.loads(rtext(self.path(".claude/settings.json")))
        for rule in ("Bash(sh .agents/skills/*../*)", "Bash(.agents/bin/*../*)"):
            self.assertIn(rule, settings["permissions"]["deny"])

    def test_block_message_keeps_a_literal_dot_dot(self):
        """E2E-05: only one sentence-ending period is dropped; a reason that ends in `..` keeps it."""
        rule = {"id": "x", "reason": "it leaves the prefix through ..", "next": "n"}
        self.assertEqual(guard.block_message(rule), "BLOCKED x: it leaves the prefix through ... NEXT: n")
        self.assertEqual(guard.block_message(dict(rule, reason="ends here.")), "BLOCKED x: ends here. NEXT: n")
        self.assertEqual(guard.block_message(dict(rule, reason="no period")), "BLOCKED x: no period. NEXT: n")

    def test_v1_17_missing_or_broken_guard_yaml_blocks_with_next(self):
        p = self.path(".agents/guard.yaml")
        orig = rtext(p)

        def restore():
            with open(p, "w") as f:
                f.write(orig)
        self.addCleanup(restore)
        for content in (None, "rules: [unclosed\n", ""):
            if content is None:
                os.unlink(p)
            else:
                with open(p, "w") as f:
                    f.write(content)
            d, reason = self.decision("ls")
            self.assertEqual(d, "deny", content)
            self.assertTrue(reason.startswith("BLOCKED guard-config: .agents/guard.yaml is"), reason)
            self.assertIn("NEXT: run `git checkout -- .agents/guard.yaml`", reason)
            self.assertEqual(self.decision("git checkout -- .agents/guard.yaml"), (None, None))    # the way back stays open
            restore()


class NoBytecodeTest(Repo):
    def test_v1_08_hooks_and_bin_leave_no_pycache_in_the_repo(self):
        import shutil
        for d, _dirs, _files in os.walk(self.path(".agents")):
            if os.path.basename(d) == "__pycache__":
                shutil.rmtree(d, ignore_errors=True)
        env = {k: v for k, v in ENV.items() if k != "PYTHONDONTWRITEBYTECODE"}
        self.hook("session-start-notice.py", {"source": "startup"}, env=env)
        self.hook("pre-tool-use-guard.py", {"tool_name": "Bash", "tool_input": {"command": "ls"}}, env=env)
        subprocess.run([self.path(".agents/bin/verb"), "lint"], cwd=self.repo, capture_output=True, env=env)
        subprocess.run([sys.executable, self.path(".agents/bin/agentive-lint"), "size"], cwd=self.repo, capture_output=True, env=env)
        found = [d for d, _dirs, _files in os.walk(self.path(".agents")) if os.path.basename(d) == "__pycache__"]
        self.assertEqual(found, [])
        st = subprocess.run(["git", "-C", self.repo, "status", "--porcelain", "--untracked-files=all"], capture_output=True, text=True, env=GIT_ENV).stdout
        self.assertNotIn("__pycache__", st)


if __name__ == "__main__":
    unittest.main()
