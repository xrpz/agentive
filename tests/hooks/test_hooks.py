# agentive-unit: selftest.component
# design: spec.guard, spec.context_management.context_gate, spec.verb_wrapper, spec.stop_hook, spec.handoff, spec.init_apply.session_start, spec.init_apply.reprove_on_change, spec.maintenance.harvest, spec.no_human_run
"""Installed hook scripts and .agents/bin/verb, run as Claude Code and Codex would run them (JSON on stdin),
inside a repo initialized by the engine. State files are validated against their schemas."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PAYLOAD = os.path.join(ROOT, "plugins", "claude-code", "agentive", "payload")
sys.path.insert(0, os.path.join(PAYLOAD, "repo-layer", "dot-agents", "lib"))

from jsonschema import Draft202012Validator  # noqa: E402
from referencing import Registry, Resource  # noqa: E402

from agentive import ayaml, guard, handoff, nohuman  # noqa: E402

_REG, _S = Registry(), {}
for _n in sorted(os.listdir(os.path.join(ROOT, "design", "schemas"))):
    with open(os.path.join(ROOT, "design", "schemas", _n), encoding="utf-8") as _f:
        _s = json.load(_f)
    _S[_n[:-len(".schema.json")]] = _s
    _REG = _REG.with_resource(_s["$id"], Resource.from_contents(_s))
ENV = {k: v for k, v in os.environ.items() if k not in ("CI", "GITHUB_ACTIONS", "AGENTIVE_HEADLESS")}
GIT_ENV = dict(ENV, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")


def rtext(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def jload(path):
    return json.loads(rtext(path))


def schema_errors(name, inst):
    return [f"{'/'.join(map(str, e.absolute_path))}: {e.message}" for e in Draft202012Validator(_S[name], registry=_REG).iter_errors(inst)]


OK = {"tier": "inner", "evidence": {"rank": 1, "source": "test", "files": ["go.mod"]}}


class Repo(unittest.TestCase):
    """One initialized repo shared by the tests of a class (setUpClass); tests reset the state they use."""

    @classmethod
    def setUpClass(cls):
        cls.repo = tempfile.mkdtemp(prefix="agentive-hooks-")
        r = cls.repo
        subprocess.run(["git", "-C", r, "init", "-q", "-b", "main"], check=True, env=GIT_ENV)
        with open(os.path.join(r, "go.mod"), "w") as f:
            f.write("module x\n")
        with open(os.path.join(r, "big.txt"), "w") as f:
            f.write("x" * 70000)
        values = {"tools": {}, "verbs": {
            "setup": dict(OK, cmd="true", tier="outer"), "build": dict(OK, cmd="true", tier="outer"),
            "lint": dict(OK, cmd="test ! -e lint.flag"), "test": dict(OK, cmd="test ! -e fail.flag", fail_count="^FAIL"),
        }}
        cls.values = r + "-values.json"
        with open(cls.values, "w") as f:
            json.dump(values, f)
        out = subprocess.run([sys.executable, os.path.join(PAYLOAD, "engine", "apply.py"), "init", "--repo", r, "--confirm-all",
                              "--no-managed-review-copy", "--values", cls.values], capture_output=True, text=True, env=ENV)
        assert out.returncode == 0, out.stdout + out.stderr
        subprocess.run(["git", "-C", r, "add", "-A"], check=True, env=GIT_ENV)
        subprocess.run(["git", "-C", r, "commit", "-qm", "init"], check=True, env=GIT_ENV)
        subprocess.run(["git", "-C", r, "checkout", "-qb", "work"], check=True, env=GIT_ENV)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.repo, ignore_errors=True)
        os.unlink(cls.values)

    def hook(self, name, inp, tool="claude", env=None):
        cmd = [sys.executable, os.path.join(self.repo, ".agents", "hooks", name)] + (["--tool", "codex"] if tool == "codex" else [])
        base = dict(inp)
        base.setdefault("cwd", self.repo)
        base.setdefault("session_id", "s1")
        r = subprocess.run(cmd, input=json.dumps(base), capture_output=True, text=True, env=env or ENV, cwd=self.repo)
        out = json.loads(r.stdout) if r.stdout.strip().startswith("{") else r.stdout
        return r.returncode, out, r.stderr

    def path(self, rel):
        return os.path.join(self.repo, rel)

    def touch(self, rel, on=True):
        p = self.path(rel)
        if on:
            open(p, "w").close()
        elif os.path.exists(p):
            os.unlink(p)


class GuardTest(Repo):
    def decision(self, command, env=None, tool="claude"):
        rc, out, err = self.hook("pre-tool-use-guard.py", {"tool_name": "Bash", "tool_input": {"command": command}}, tool=tool, env=env)
        self.assertEqual(rc, 0, err)
        if isinstance(out, dict):
            hso = out["hookSpecificOutput"]
            return hso["permissionDecision"], hso["permissionDecisionReason"]
        return None, None

    def test_deny_rules(self):
        cases = {
            "git push origin main": "push-default-branch", "git push origin HEAD:main": "push-default-branch",
            "git push --force origin work": "push-force", "git -C . push -f origin work": "push-force",
            "git push origin +work": "push-force", "bash -c 'git push --force origin work'": "push-force",
            "git push --tags": "push-tag", "git push origin --follow-tags": "push-tag",
            "npm publish": "publish-dry-run", "docker push img": "publish", "gh release create v1": "publish",
            "cargo publish": "publish-dry-run", "terraform destroy": "infra-destroy", "kubectl apply -f x.yaml": "infra-change",
            "rm -rf $DIR/x": "rm-unresolvable", "rm -rf .": "rm-unresolvable", "rm -rf $(pwd)/x": "rm-unresolvable",
            "git commit --no-verify -m x": "no-verify", "git commit -nm x": "no-verify", "gh pr merge 3": "gh-pr-merge",
            "psql -c 'DELETE FROM users'": "sql-delete-no-where", "psql -c 'DROP DATABASE prod'": "infra-destroy-sql",
            "FOO=1 git push origin main": "push-default-branch", "ls && git push origin main": "push-default-branch",
        }
        for cmd, rid in cases.items():
            d, reason = self.decision(cmd)
            self.assertEqual(d, "deny", cmd)
            self.assertTrue(reason.startswith(f"BLOCKED {rid}: "), (cmd, reason))
            self.assertIn(". NEXT: ", reason)

    def test_allowed(self):
        for cmd in ("git push origin work", "git push -u origin HEAD:work", "git push --force-with-lease origin work",
                    "npm publish --dry-run", "cargo publish -n", "gh release list", "DIR=build; rm -rf $DIR/x", "rm -rf build",
                    "git commit -m 'fix -n handling'", "psql -c 'DELETE FROM users WHERE id = 1'", "terraform plan",
                    "git status", "./gradlew publishToMavenLocal"):
            self.assertEqual(self.decision(cmd), (None, None), cmd)

    def test_ask_interactive_vs_no_human(self):
        self.assertEqual(self.decision("git reset --hard HEAD~1"), (None, None))
        d, reason = self.decision("git reset --hard HEAD~1", env=dict(ENV, AGENTIVE_HEADLESS="1"))
        self.assertEqual(d, "deny")
        self.assertIn("BLOCKED reset-hard", reason)
        self.assertIn("handed off in .agents/state/handoff/", reason)
        hd = os.path.join(self.repo, ".agents", "state", "handoff")
        files = [f for f in os.listdir(hd) if f.endswith(".md")]
        doc = handoff.read(os.path.join(hd, files[0]))
        self.assertEqual(doc["reason"], "ask_class_denied")
        self.assertEqual(schema_errors("handoff", doc), [])
        # Codex: permission_mode dontAsk in the hook input is the no-human signal
        rc, out, _ = self.hook("pre-tool-use-guard.py", {"tool_name": "Bash", "tool_input": {"command": "git branch -D old"},
                                                          "permission_mode": "dontAsk"}, tool="codex")
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_tag_ref_push(self):
        subprocess.run(["git", "-C", self.repo, "tag", "v9"], check=True, env=GIT_ENV)
        self.assertEqual(self.decision("git push origin v9")[0], "deny")

    def test_rendered_permissions_and_codex_rules(self):
        rules = guard.load_rules(self.repo)
        deny, ask = guard.claude_permissions(rules)
        self.assertIn("Bash(terraform destroy)", deny)
        self.assertIn("Bash(git clean -f*)", ask)
        self.assertFalse([p for p in deny if "npm publish" in p])     # unless -> hook only
        text = rtext(self.path(".codex/rules/agentive.rules"))
        self.assertIn('pattern = ["gh", "pr", "merge"]', text)
        self.assertIn('decision = "prompt"', text)
        self.assertEqual(schema_errors("guard-rules", ayaml.load(self.path(".agents/guard.yaml"))), [])

    def test_nohuman_signals(self):
        self.assertFalse(nohuman.no_human({"CLAUDE_CODE_REMOTE": "true"}))
        self.assertFalse(nohuman.no_human({}, {"permission_mode": "bypassPermissions"}))
        self.assertTrue(nohuman.no_human({"AGENTIVE_HEADLESS": "1"}))
        self.assertTrue(nohuman.no_human({"GITHUB_ACTIONS": "true"}))
        self.assertTrue(nohuman.no_human({}, {"permission_mode": "dontAsk"}))


class GateRewriteTest(Repo):
    def pre(self, name, tool_name, tool_input):
        rc, out, err = self.hook(name, {"tool_name": tool_name, "tool_input": tool_input})
        self.assertEqual(rc, 0, err)
        return out["hookSpecificOutput"] if isinstance(out, dict) else None

    def test_context_gate(self):
        big = self.path("big.txt")
        self.assertEqual(self.pre("pre-tool-use-context-gate.py", "Read", {"file_path": big})["permissionDecision"], "deny")
        self.assertIsNone(self.pre("pre-tool-use-context-gate.py", "Read", {"file_path": big, "limit": 100}))
        self.assertIsNone(self.pre("pre-tool-use-context-gate.py", "Read", {"file_path": self.path("go.mod")}))
        self.assertEqual(self.pre("pre-tool-use-context-gate.py", "Read", {"file_path": self.path("go.mod"), "allow_large": True})["permissionDecision"], "deny")
        self.assertEqual(self.pre("pre-tool-use-context-gate.py", "Bash", {"command": "cat big.txt"})["permissionDecision"], "deny")
        self.assertIsNone(self.pre("pre-tool-use-context-gate.py", "Bash", {"command": "cat big.txt | head -5"}))
        self.assertIsNone(self.pre("pre-tool-use-context-gate.py", "Bash", {"command": "head -c 100 big.txt"}))

    def test_test_rewrite(self):
        out = self.pre("pre-tool-use-test-rewrite.py", "Bash", {"command": "test ! -e fail.flag", "description": "d"})
        self.assertEqual(out["permissionDecision"], "allow")
        self.assertEqual(out["updatedInput"], {"command": ".agents/bin/verb test", "description": "d"})
        out = self.pre("pre-tool-use-test-rewrite.py", "Bash", {"command": "go test ./..."})
        self.assertEqual(out["updatedInput"]["command"], ".agents/bin/verb test")
        self.assertIsNone(self.pre("pre-tool-use-test-rewrite.py", "Bash", {"command": "ls -la"}))
        self.assertIsNone(self.pre("pre-tool-use-test-rewrite.py", "Bash", {"command": "go test ./... && rm x"}))


class VerbStopTest(Repo):
    def verb(self, *args):
        r = subprocess.run([self.path(".agents/bin/verb")] + list(args), capture_output=True, text=True, cwd=self.repo, env=ENV)
        return r.returncode, r.stdout

    def setUp(self):
        for f in ("fail.flag", "lint.flag"):
            self.touch(f, False)
        for f in ("stop.json", "verify.json", "dirty.json"):
            p = self.path(".agents/state/" + f)
            if os.path.exists(p):
                os.unlink(p)
        shutil.rmtree(self.path(".agents/state/handoff"), ignore_errors=True)

    def test_verb_wrapper_lines_and_verify_json(self):
        rc, out = self.verb("typecheck")
        self.assertEqual((rc, out.strip()), (0, "PENDING verb=typecheck reason=no_evidence"))
        self.touch("fail.flag")
        rc, out = self.verb("verify")
        self.assertEqual(rc, 1)
        last = [ln for ln in out.splitlines() if ln.startswith("AGENTIVE_FAIL")]
        self.assertTrue(last and last[0].startswith("AGENTIVE_FAIL verb=test exit=1 log=.agents/state/logs/test-"), out)
        self.assertIn("; NEXT=grep -n -m20 -E", last[0])
        v = jload(self.path(".agents/state/verify.json"))
        self.assertEqual(schema_errors("verify-result", v), [])
        self.assertEqual(v["total_failures"], 1)
        self.assertIn("typecheck", v["pending"])
        self.assertEqual(oct(os.stat(self.path(v["verbs"]["test"]["log"])).st_mode & 0o777), "0o600")

    def stop(self, tool="claude"):
        return self.hook("stop-verify.py", {"stop_hook_active": False}, tool=tool)

    def test_stop_block_no_progress_then_done(self):
        self.touch("fail.flag")
        rc, out, err = self.stop()
        self.assertEqual(rc, 2)
        self.assertIn("AGENTIVE_FAIL verb=test", err)
        rc, out, err = self.stop()
        self.assertEqual(rc, 0)
        self.assertIn("NO-PROGRESS", out["hookSpecificOutput"]["additionalContext"])
        st = jload(self.path(".agents/state/stop.json"))
        self.assertTrue(st["handoff_requested"])
        self.assertEqual(schema_errors("stop-state", st), [])
        hd = self.path(".agents/state/handoff")
        doc = handoff.read(os.path.join(hd, os.listdir(hd)[0]))
        self.assertEqual((doc["reason"], doc["branch"]), ("no_progress", "work"))
        self.assertEqual(schema_errors("handoff", doc), [])
        rc, out, err = self.stop()          # the stop after the one continuation: exit 0, no rerun
        self.assertEqual((rc, out), (0, ""))
        self.touch("fail.flag", False)
        rc, out, err = self.stop()
        self.assertEqual((rc, out), (0, {"systemMessage": "DONE"}))
        self.assertEqual(jload(self.path(".agents/state/stop.json"))["consecutive_blocks"], 0)

    def test_stop_cap_and_codex_form(self):
        m = ayaml.load(self.path(".agents/manifest.yaml"))
        orig = rtext(self.path(".agents/manifest.yaml"))
        try:
            m["stop_conditions"] = {"max_blocks": 1, "no_progress_rounds": 3}
            with open(self.path(".agents/manifest.yaml"), "w") as f:
                f.write(ayaml.dumps(m))
            self.touch("fail.flag")
            self.assertEqual(self.stop("codex")[0], 2)
            rc, out, err = self.stop("codex")
            self.assertEqual((rc, out), (0, ""))      # CAP: Codex gets no stdout (plain text is invalid for Stop)
            self.assertEqual(jload(self.path(".agents/state/stop.json"))["last_reason"], "CAP")
        finally:
            with open(self.path(".agents/manifest.yaml"), "w") as f:
                f.write(orig)

    def test_file_changed_marks_dirty(self):
        rc, out, err = self.hook("file-changed-dirty.py", {"file_path": self.path("go.mod")})
        self.assertEqual(rc, 0, err)
        d = jload(self.path(".agents/state/dirty.json"))
        self.assertEqual(schema_errors("dirty-state", d), [])
        slots = {e["slot"] for e in d["slots"]}
        self.assertIn("verbs.test", slots)
        self.assertIn("verbs.typecheck", slots)      # a pending verb marks the stack slots while it is pending
        self.touch("fail.flag", False)
        rc, out, err = self.stop()
        self.assertIn("DIRTY=", out["systemMessage"])
        self.assertIn("NEXT=/agentive:reprove", out["systemMessage"])


class SessionTest(Repo):
    def test_session_start_ready_and_compact(self):
        rc, out, err = self.hook("session-start-notice.py", {"source": "startup"})
        self.assertEqual(rc, 0, err)
        ctx = out["hookSpecificOutput"]["additionalContext"]
        self.assertRegex(ctx.splitlines()[0], r"^AGENTIVE_STATE=ready; VERSION=\d+\.\d+\.\d+; COMPAT=(ok|unknown|fail:\S+); SUPPLY=ok; PENDING=typecheck,run$")
        rc, out, err = self.hook("session-start-notice.py", {"source": "compact"}, tool="codex")
        self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "SessionStart")
        self.assertNotIn("watchPaths", out["hookSpecificOutput"])

    def test_session_start_supply_mismatch_and_pending_confirmation(self):
        skill = self.path(".agents/skills/verify/SKILL.md")
        orig = rtext(skill)
        pc = self.path(".agents/state/pending-confirmation.json")
        try:
            with open(skill, "a") as f:
                f.write("\nedited\n")
            with open(pc, "w") as f:
                json.dump({"created_at": "2026-10-28T00:00:00Z", "command": "/agentive:update",
                           "files": [{"path": ".claude/settings.json", "category": "claude_settings", "rendered_sha256": "a" * 64}]}, f)
            rc, out, err = self.hook("session-start-notice.py", {"source": "resume"})
            ctx = out["hookSpecificOutput"]["additionalContext"]
            self.assertIn("SUPPLY=mismatch:verify", ctx)
            self.assertIn("PENDING_CONFIRMATION=1 files", ctx)
            self.assertIn("agentive:", out["systemMessage"])
        finally:
            with open(skill, "w") as f:
                f.write(orig)
            os.unlink(pc)

    def test_session_end_harvest(self):
        os.makedirs(self.path(".agents/state"), exist_ok=True)
        with open(self.path(".agents/state/stop.json"), "w") as f:
            json.dump({"session_id": "s9", "consecutive_blocks": 0, "last_fail_count": None, "total_blocks": 4}, f)
        tp = self.path(".agents/state/t.jsonl")
        with open(tp, "w") as f:
            for _ in range(2):
                f.write(json.dumps({"message": {"content": [{"type": "tool_use", "name": "Bash", "input": {"command": "gh pr checks 3"}}]}}) + "\n")
            f.write(json.dumps({"message": {"content": [{"type": "tool_use", "name": "Bash", "input": {"command": "git status"}}]}}) + "\n")
            f.write(json.dumps({"message": {"content": [{"type": "tool_use", "name": "Bash", "input": {"command": "git status"}}]}}) + "\n")
        rc, out, err = self.hook("session-end-harvest.py", {"session_id": "s9", "transcript_path": tp, "reason": "other"})
        self.assertEqual(rc, 0, err)
        lines = rtext(self.path(".agents/state/harvest-queue.jsonl")).splitlines()
        line = json.loads(lines[-1])
        self.assertEqual(schema_errors("harvest-queue", line), [])
        self.assertEqual(line["signals"]["stop_blocks"], 4)
        self.assertEqual([c["normalized"] for c in line["signals"]["repeated_command"]], ["gh pr checks 3"])

    def test_uninitialized_line(self):
        d = tempfile.mkdtemp()
        try:
            rc, out, err = self.hook("session-start-notice.py", {"cwd": d, "source": "startup"})
            self.assertEqual(out.strip(), "AGENTIVE_STATE=uninitialized; NEXT=ask_user_to_run:/agentive:init; DO_NOT=reproduce init steps")
            rc, out, err = self.hook("session-start-notice.py", {"cwd": d, "source": "startup"}, env=dict(ENV, CI="true"))
            self.assertEqual(out.strip(), "AGENTIVE_STATE=uninitialized; NEXT=none")
        finally:
            shutil.rmtree(d)


class HandoffUnitTest(unittest.TestCase):
    def test_branch_key_and_redact(self):
        k = handoff.branch_key("Feat/Login UI", "abc")
        self.assertRegex(k, r"^feat-login-ui-[a-f0-9]{8}$")
        self.assertEqual(handoff.branch_key(None, "abcdef1234"), "detached-abcdef1")
        self.assertNotIn("ghp_", handoff.redact("token ghp_" + "a" * 30))
        self.assertIn("[REDACTED]", handoff.redact("password=hunter2"))


if __name__ == "__main__":
    unittest.main()
