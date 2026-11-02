# agentive-unit: selftest.component
# design: decisions.accepted[HEADLESSHINT], spec.context_management.headless.bash_hint, spec.context_management.headless.sandbox_cache, spec.guard.push_without_refspec, docs/impl/decisions-log.md §23
"""E2E-32 (HEADLESSHINT) and E2E-36 (decision e2e_32_36, 2026-11-01) on the installed hooks and the verb wrapper.

E2E-32: the PreToolUse context-gate hook adds a hint (additionalContext) to a Bash call the headless allowlist would refuse and
never changes the verdict; the verb moves an unwritable scratch cache into .agents/state/cache/<tool>. E2E-36: with no human
present a `git push` without a refspec is denied with NEXT `git push -u origin HEAD`."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_hooks import ENV, PAYLOAD, ROOT, Repo  # noqa: E402

from agentive import headless, verb  # noqa: E402

GATE = "pre-tool-use-context-gate.py"
GUARD = "pre-tool-use-guard.py"
REFUSED = {                                    # command -> the hint code (the forms the E2E runs used)
    "cat >> src/x.py <<'EOF'\ndef f():\n    return 1\nEOF": "redirect",
    "sh .agents/skills/chain-intent/scripts/stage-check.sh s; echo EXIT=$?": "exit_status",
    "sed -i s/a/b/ src/x.py": "sed_in_place",
    "cd src && ls": "cd",
    "python3 -c 'print(1)'": "not_allowlisted",
    "echo hi > .agents/state/p.txt": "redirect",
}
ALLOWED = [".agents/bin/verb verify 2>&1 | tail -15", "git commit -q -F - <<'EOF'\nfix: a > b\nEOF", ".agents/bin/verb lint || true",
           "git status --short && cat go.mod && ls", ".agents/bin/agentive-lint lock && echo ok", "git push -u origin HEAD"]


class E2E32HintHookTest(Repo):
    def gate(self, command, mode="dontAsk", tool="claude", env=None):
        inp = {"tool_name": "Bash", "tool_input": {"command": command}}
        if mode:
            inp["permission_mode"] = mode
        rc, out, err = self.hook(GATE, inp, tool=tool, env=env)
        self.assertEqual(rc, 0, err)
        return out

    def test_e2e_32_hint_adds_context_and_never_changes_the_verdict(self):
        for cmd, code in REFUSED.items():
            out = self.gate(cmd)
            self.assertIsInstance(out, dict, cmd)
            hso = out["hookSpecificOutput"]
            self.assertEqual(hso["hookEventName"], "PreToolUse")
            self.assertNotIn("permissionDecision", hso, cmd)                     # the hint never decides
            self.assertNotIn("permissionDecisionReason", hso, cmd)
            ctx = hso["additionalContext"]
            self.assertTrue(ctx.startswith("AGENTIVE_HEADLESS_BASH="), ctx)
            self.assertIn(code, ctx.split(":")[0], cmd)
            self.assertIn("only this one command was refused: Bash, /verify, /precommit-review and git commit still work", ctx)
            self.assertIn("NEXT=" + headless.CODE_NEXT[code], ctx)
        # the guard hook gives these commands no decision either (no new deny rule)
        for cmd in REFUSED:
            rc, out, _ = self.hook(GUARD, {"tool_name": "Bash", "tool_input": {"command": cmd}, "permission_mode": "dontAsk"})
            self.assertEqual((rc, out), (0, ""), cmd)

    def test_e2e_32_no_hint_for_allowlisted_commands_or_with_a_human(self):
        for cmd in ALLOWED:
            self.assertEqual(self.gate(cmd), "", cmd)
        for cmd in REFUSED:
            self.assertEqual(self.gate(cmd, mode="default"), "", cmd)               # interactive: nothing
            self.assertEqual(self.gate(cmd, tool="codex"), "", cmd)                 # Claude Code only
        out = self.gate("cd src && ls", mode="acceptEdits", env=dict(ENV, AGENTIVE_HEADLESS="1"))
        self.assertIn("AGENTIVE_HEADLESS_BASH=cd", out["hookSpecificOutput"]["additionalContext"])

    def test_e2e_32_a_context_gate_deny_keeps_its_verdict_and_gets_the_hint(self):
        out = self.gate("cat big.txt")
        hso = out["hookSpecificOutput"]
        self.assertEqual(hso["permissionDecision"], "deny")                       # unchanged context gate verdict
        self.assertNotIn("additionalContext", hso)                                 # cat is read-only: no hint
        rc, out, _ = self.hook(GATE, {"tool_name": "Read", "tool_input": {"file_path": self.path("big.txt")}, "permission_mode": "dontAsk"})
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertNotIn("additionalContext", out["hookSpecificOutput"])

    def test_e2e_32_allowlist_is_the_standard_commands(self):
        import re
        import yaml
        with open(os.path.join(ROOT, "design", "framework.yaml"), encoding="utf-8") as f:
            std = yaml.load(f, Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader))["spec"]["context_management"]["headless"]["standard_cmd"]
        self.assertEqual(list(headless.ALLOWED), re.search(r'--allowedTools "([^"]*)"', std).group(1).split(","))
        self.assertIn("--settings '{\"sandbox\":{\"autoAllowBashIfSandboxed\":false}}'", std)
        self.assertNotIn("Bash(python3", std)                                      # the allowlist is not widened

    def test_e2e_32_problem_codes(self):
        self.assertEqual(headless.problems(".agents/bin/verb lint > out 2>&1"), [("redirect", ".agents/bin/verb lint > out")])
        self.assertEqual(headless.problems("x 2>/dev/null; true"), [("not_allowlisted", "x 2>/dev/null")])
        self.assertEqual(headless.problems("git log --oneline -5 | head"), [])
        self.assertEqual(headless.problems("git commit -m 'exit $? in a message'"), [])     # single quotes keep $? literal
        self.assertEqual([c for c, _ in headless.problems("bash -c 'cd x && sed -i s/a/b/ f'")], ["sed_in_place", "cd"])
        self.assertIsNone(headless.hint("cd x", no_human=False))
        self.assertIsNone(headless.hint("cd x", no_human=True, tool="codex"))


class E2E32CacheTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="agentive-cache-")
        self.addCleanup(shutil.rmtree, self.root, True)

    def test_e2e_32_unwritable_scratch_cache_moves_into_the_project(self):
        home_cache = os.path.join(os.path.expanduser("~"), ".cache", "uv")
        with mock.patch.object(verb, "_writable", lambda p: p != home_cache and not p.endswith(".npm")):
            env, notes = verb.cache_env(self.root, "uv run ruff check . && npm test", {"HOME": os.path.expanduser("~")})
        self.assertEqual(env["UV_CACHE_DIR"], os.path.join(self.root, ".agents/state/cache/uv"))
        self.assertEqual(env["npm_config_cache"], os.path.join(self.root, ".agents/state/cache/npm"))
        self.assertNotIn("RUFF_CACHE_DIR", env)                                     # ruff's cache is in the project
        self.assertTrue(os.path.isdir(os.path.join(self.root, ".agents/state/cache/uv")))
        self.assertEqual(len(notes), 2)
        self.assertTrue(all(n.startswith("NOTE cache: ") for n in notes), notes)
        # a value the user set is kept; a writable default changes nothing
        with mock.patch.object(verb, "_writable", lambda p: False):
            env, notes = verb.cache_env(self.root, "uv run pytest", {"UV_CACHE_DIR": "/mine"})
        self.assertEqual(env["UV_CACHE_DIR"], "/mine")
        self.assertIn("-o cache_dir=.agents/state/cache/pytest", env["PYTEST_ADDOPTS"])
        env, notes = verb.cache_env(self.root, "uv run pytest", {})
        self.assertEqual(notes, [])

    def test_e2e_32_blocked_write_in_a_dependency_store_names_the_allow_write_entry(self):
        nxt = verb.blocked_write_next("cargo test", "error: failed to open ~/.cargo/registry: Read-only file system (os error 30)")
        self.assertIn("add ~/.cargo to sandbox.filesystem.allowWrite", nxt)
        self.assertIn("--settings JSON of the headless standard command", nxt)
        self.assertIsNone(verb.blocked_write_next("cargo test", "test result: FAILED"))

    def test_e2e_37_ordinary_failure_with_the_words_keeps_the_normal_next(self):
        """E2E-37: test output that says `Operation not permitted` (a test that chmods a file) is not a refused cache write."""
        logs = ["E   PermissionError: [Errno 1] Operation not permitted: '/tmp/pytest-of-x/test_chmod0/f'\nFAILED tests/test_a.py::test_chmod",
                "tests/test_fs.py: os.chown failed: Operation not permitted\n1 failed",
                "cp: cannot create regular file '/usr/bin/x': Read-only file system"]
        for cmd in ("uv run pytest", "cargo test", "npm test"):
            for data in logs:
                self.assertIsNone(verb.blocked_write_next(cmd, data), (cmd, data))
        # the words on a line, a cache path on another line: still not a refused write
        self.assertIsNone(verb.blocked_write_next("uv run pytest", "Operation not permitted (test)\nwarn: ~/.cache/uv is fine"))
        # a command with no known tool never gets the sandbox NEXT
        self.assertIsNone(verb.blocked_write_next("make test", "error: ~/.cargo/registry: Read-only file system (os error 30)"))

    def test_e2e_37_real_cache_denials_get_the_sandbox_next(self):
        cases = [("uv run pytest", "error: failed to open file `/home/u/.cache/uv/sdists-v9/.git`: Read-only file system (os error 30)"),
                 ("uv run ruff check .", "warning: Failed to create cache directory `/w/.ruff_cache`: Operation not permitted (os error 1)"),
                 ("uv run pytest", "PytestCacheWarning: could not create cache path /w/.pytest_cache/v/cache/nodeids: [Errno 30] Read-only file system"),
                 ("npm test", "npm ERR! Error: EROFS: read-only file system, mkdir '/home/u/.npm/_cacache'"),
                 ("cargo build", "error: failed to open `/home/u/.cargo/registry/index`: Operation not permitted (os error 1)"),
                 ("go test ./...", "go: creating work dir: mkdir /home/u/go/pkg/mod/cache: read-only file system"),
                 ("./gradlew test", "Could not create service of type X: /home/u/.gradle/caches: Read-only file system")]
        for cmd, data in cases:
            nxt = verb.blocked_write_next(cmd, "tests ran\n" + data + "\n1 failed")
            self.assertIsNotNone(nxt, (cmd, data))
            self.assertIn("sandbox.filesystem.allowWrite", nxt)
        # a cache directory the user set is a store path too
        nxt = verb.blocked_write_next("uv run pytest", "error: write /mnt/c/uvc/x: Read-only file system",
                                      env={"UV_CACHE_DIR": "/mnt/c/uvc"})
        self.assertIsNotNone(nxt)

    def test_e2e_37_run_verb_keeps_grep_next_on_an_ordinary_failure_and_sandbox_next_on_a_denial(self):
        def run(script):
            manifest = {"verbs": {"test": {"cmd": script, "status": "ready"}}}
            with mock.patch.object(verb, "cache_env", lambda root, cmd, env: (env, [])):
                return verb.run_verb(self.root, manifest, "test", quiet=True)
        r = run("echo 'chmod: changing permissions of /tmp/x: Operation not permitted'; echo uv; exit 1")
        self.assertFalse(r["ok"])
        self.assertIn("NEXT=grep -n -m20", r["fail_line"], r["fail_line"])
        self.assertNotIn("allowWrite", r["fail_line"])
        r = run("echo 'error: failed to open /home/u/.cache/uv/x: Read-only file system (os error 30)'; echo uv run; exit 1")
        self.assertIn("allowWrite", r["fail_line"], r["fail_line"])


    def test_e2e_32_writable_probe_is_a_real_write(self):
        self.assertTrue(verb._writable(os.path.join(self.root, "not", "there", "yet")))
        self.assertEqual([n for n in os.listdir(self.root) if n.startswith(".agentive-write-test")], [])


@unittest.skipUnless(shutil.which("claude") and os.environ.get("AGENTIVE_LIVE_PROBE") == "1",
                     "live probe: needs the claude CLI and AGENTIVE_LIVE_PROBE=1 (it calls the model)")
class E2E32LiveProbeTest(Repo):
    def test_e2e_32_probe_hint_reaches_the_refused_call(self):
        """One headless run (`claude -p`, dontAsk) asked to run a refused command: the refused call's result carries the hint."""
        cmd = ["claude", "-p", "Run exactly this Bash command once: cd .agents && ls. Then stop.", "--permission-mode", "dontAsk",
               "--allowedTools", ",".join(headless.ALLOWED), "--output-format", "stream-json", "--verbose", "--max-budget-usd", "0.3"]
        r = subprocess.run(cmd, cwd=self.repo, capture_output=True, text=True, timeout=300,
                           env=dict(ENV, AGENTIVE_HEADLESS="1", CLAUDE_BASH_MAINTAIN_PROJECT_WORKING_DIR="1"))
        self.assertIn("AGENTIVE_HEADLESS_BASH=cd", r.stdout, r.stdout[-2000:])


class E2E36PushGateTest(Repo):
    def guard(self, command, env=None, mode=None):
        inp = {"tool_name": "Bash", "tool_input": {"command": command}}
        if mode:
            inp["permission_mode"] = mode
        rc, out, err = self.hook(GUARD, inp, env=env)
        self.assertEqual(rc, 0, err)
        return out["hookSpecificOutput"]["permissionDecisionReason"] if isinstance(out, dict) else None

    def test_e2e_36_refspec_less_push_is_denied_in_headless_runs(self):
        headless_env = dict(ENV, AGENTIVE_HEADLESS="1")
        for cmd in ("git push", "git push origin", "git push -u origin", "git -C . push --set-upstream origin"):
            reason = self.guard(cmd, env=headless_env)
            self.assertIsNotNone(reason, cmd)
            self.assertTrue(reason.startswith("BLOCKED push-no-refspec: "), reason)
            self.assertTrue(reason.endswith("NEXT: git push -u origin HEAD"), reason)
            self.assertIsNotNone(self.guard(cmd, mode="dontAsk"), cmd)              # the dontAsk signal counts too
        self.assertIsNone(self.guard("git push -u origin HEAD", env=headless_env))     # the /pr form passes
        self.assertIsNone(self.guard("git push origin HEAD:refs/heads/work", env=headless_env))
        self.assertIn("push-tag", self.guard("git push --tags", env=headless_env))      # other rules keep their own ids

    def test_e2e_36_interactive_sessions_are_unchanged(self):
        self.assertIsNone(self.guard("git push"))                                      # work branch, a human is present
        self.assertIsNone(self.guard("git push origin", mode="default"))


if __name__ == "__main__":
    unittest.main()
