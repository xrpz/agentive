# agentive-unit: selftest.component
# design: spec.guard, spec.context_management.context_gate, spec.verb_wrapper, spec.stop_hook, spec.handoff, spec.init_apply.session_start, spec.init_apply.reprove_on_change, spec.maintenance.harvest, spec.no_human_run
"""Installed hook scripts and .agents/bin/verb, run as Claude Code and Codex would run them (JSON on stdin),
inside a repo initialized by the engine. State files are validated against their schemas."""
import atexit
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

from agentive import ayaml, guard, handoff, hookio, lintscope, nohuman  # noqa: E402

_REG, _S = Registry(), {}
for _n in sorted(os.listdir(os.path.join(ROOT, "design", "schemas"))):
    with open(os.path.join(ROOT, "design", "schemas", _n), encoding="utf-8") as _f:
        _s = json.load(_f)
    _S[_n[:-len(".schema.json")]] = _s
    _REG = _REG.with_resource(_s["$id"], Resource.from_contents(_s))
_IDENT = ("EMAIL", "GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL")
_GITHOME = tempfile.mkdtemp(prefix="agentive-nogit-")          # an empty, writable home for git's own config lookups
atexit.register(shutil.rmtree, _GITHOME, True)


def no_identity_env(base):
    """[설계 판단] The environment of a CI runner or a fresh user repo: no system/global/XDG git config, no identity variables.
    The tests run in it, so a product git call that needs the developer's user.name/user.email fails here as on a runner
    (GitHub ubuntu-latest, 6e7903b: `git commit-tree` refused). GIT_CONFIG_GLOBAL needs git >= 2.32; XDG_CONFIG_HOME and
    GIT_CONFIG_NOSYSTEM cover older git except ~/.gitconfig, which only a HOME change would hide (not done: other tools use it)."""
    env = {k: v for k, v in base.items() if k not in _IDENT}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, XDG_CONFIG_HOME=_GITHOME)
    return env


_clean = no_identity_env(os.environ)
for _k in _IDENT:
    os.environ.pop(_k, None)
os.environ.update(_clean)
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

    def assert_done(self, out):
        """DONE, plus the PENDINGNOTICE tail while a required verb (here typecheck) has no command."""
        msg = out["systemMessage"]
        self.assertTrue(msg == "DONE" or msg.startswith("DONE; PENDING="), msg)

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
        out = subprocess.run([sys.executable, os.path.join(PAYLOAD, "engine", "apply.py"), "init", "--repo", r,
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

    def edit_manifest(self, fn):
        """Change the agentive manifest for one test; restored by the cleanup."""
        orig = rtext(self.path(".agents/manifest.yaml"))

        def restore():
            with open(self.path(".agents/manifest.yaml"), "w") as f:
                f.write(orig)
        self.addCleanup(restore)
        m = ayaml.load(self.path(".agents/manifest.yaml"))
        fn(m)
        with open(self.path(".agents/manifest.yaml"), "w") as f:
            f.write(ayaml.dumps(m))

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


class ChainPlanGateTest(Repo):
    """E2E-18 (PLANMODEGATE): on Claude Code the Bash call that runs the chain-plan stage-check passes only in plan mode."""
    CHECK = "sh .agents/skills/chain-plan/scripts/stage-check.sh rate"

    def decision(self, command, mode, tool="claude", env=None):
        inp = {"tool_name": "Bash", "tool_input": {"command": command}}
        if mode is not None:
            inp["permission_mode"] = mode
        rc, out, err = self.hook("pre-tool-use-guard.py", inp, tool=tool, env=env)
        self.assertEqual(rc, 0, err)
        if isinstance(out, dict):
            hso = out["hookSpecificOutput"]
            return hso["permissionDecision"], hso["permissionDecisionReason"]
        return None, None

    def test_e2e_18_outside_plan_mode_is_denied_with_the_chain_stop_line(self):
        for mode in ("default", "acceptEdits", "auto", "bypassPermissions"):
            d, reason = self.decision(self.CHECK, mode)
            self.assertEqual(d, "deny", mode)
            self.assertTrue(reason.startswith("AGENTIVE_CHAIN=stop:plan:rate NEXT=enter plan mode first (EnterPlanMode tool, or ask the "
                                              "human to press Shift+Tab), then rerun sh .agents/skills/chain-plan/scripts/stage-check.sh rate"), reason)
            self.assertIn("in a headless run (claude -p) or a Routines run, stop here: plan approval needs a human", reason)
            self.assertIn(f"permission_mode={mode}", reason)

    def test_e2e_18_no_human_signal_says_a_human_is_needed(self):
        d, reason = self.decision(self.CHECK, "dontAsk")
        self.assertEqual(d, "deny")
        self.assertTrue(reason.startswith("AGENTIVE_CHAIN=stop:plan:rate NEXT=plan approval needs a human: run /chain-plan rate in an "
                                          "interactive session"), reason)
        d, reason = self.decision(self.CHECK, "acceptEdits", env=dict(ENV, AGENTIVE_HEADLESS="1"))
        self.assertIn("NEXT=plan approval needs a human", reason)

    def test_e2e_18_plan_mode_missing_field_codex_and_other_commands_pass(self):
        self.assertEqual(self.decision(self.CHECK, "plan"), (None, None))
        self.assertEqual(self.decision(self.CHECK, None), (None, None))                 # an event or tool without the field
        self.assertEqual(self.decision(self.CHECK, "default", tool="codex"), (None, None))  # Codex keeps the env rule
        for cmd in ("sh .agents/skills/chain-spec/scripts/stage-check.sh rate", "sh .agents/skills/chain-intent/scripts/stage-check.sh rate",
                    "cat .agents/skills/chain-plan/scripts/stage-check.sh", "git status"):
            self.assertEqual(self.decision(cmd, "default"), (None, None), cmd)

    def test_e2e_18_wrapped_and_prefixed_forms_are_the_same_call(self):
        for cmd in ("AGENTIVE_HEADLESS=0 sh .agents/skills/chain-plan/scripts/stage-check.sh rate", "bash -c 'sh .agents/skills/chain-plan/scripts/stage-check.sh rate'",
                    "sh ./.agents/skills/chain-plan/scripts/stage-check.sh rate", f"sh {self.repo}/.agents/skills/chain-plan/scripts/stage-check.sh rate",
                    "git fetch -q origin && sh -e .agents/skills/chain-plan/scripts/stage-check.sh rate"):
            d, reason = self.decision(cmd, "acceptEdits")
            self.assertEqual(d, "deny", cmd)
            self.assertTrue(reason.startswith("AGENTIVE_CHAIN=stop:plan:rate NEXT="), (cmd, reason))
        d, reason = self.decision("sh .agents/skills/chain-plan/scripts/stage-check.sh", "default")
        self.assertTrue(reason.startswith("AGENTIVE_CHAIN=stop:plan:none NEXT="), reason)


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


class StopBase(Repo):
    """Shared by the Stop tests: the verb wrapper, a clean state dir per test, the Stop hook call."""

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

    def stop(self, tool="claude"):
        return self.hook("stop-verify.py", {"stop_hook_active": False}, tool=tool)


class ContentTreeTest(unittest.TestCase):
    """VERIFYTREE (E2E-08): hookio.content_tree on a plain repository (racy git, ignored files, unmerged index)."""

    def setUp(self):
        self.repo = tempfile.mkdtemp(prefix="agentive-tree-")
        self.addCleanup(shutil.rmtree, self.repo, True)
        self.git("init", "-q", "-b", "main")
        self.write(".gitignore", "build/\n.agents/state/\n")
        self.write("a.txt", "aaaa\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "base")

    def git(self, *args, check=True):
        r = subprocess.run(["git", "-C", self.repo] + list(args), capture_output=True, text=True, env=GIT_ENV)
        if check:
            self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def write(self, rel, text):
        p = os.path.join(self.repo, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write(text)

    def test_e2e_08_same_size_edit_changes_the_tree(self):
        """racy git: an edit of the same size whose mtime equals the index's is still seen (the index copy keeps its mtime)."""
        head_tree = self.git("rev-parse", "HEAD^{tree}")
        self.assertEqual(hookio.content_tree(self.repo), head_tree)
        idx = os.stat(os.path.join(self.repo, ".git", "index"))
        self.write("a.txt", "bbbb\n")
        os.utime(os.path.join(self.repo, "a.txt"), ns=(idx.st_atime_ns, idx.st_mtime_ns))
        self.assertNotEqual(hookio.content_tree(self.repo), head_tree)

    def test_e2e_08_state_dir_and_ignored_files_do_not_move_the_tree(self):
        t0 = hookio.content_tree(self.repo)
        self.write(".agents/state/verify.json", "{}")
        self.write("build/out.bin", "x")
        self.assertEqual(hookio.content_tree(self.repo), t0)
        self.write("new.txt", "n\n")                      # an untracked file that is not ignored moves it
        self.assertNotEqual(hookio.content_tree(self.repo), t0)

    def test_e2e_08_unmerged_index_has_no_index_tree(self):
        self.git("switch", "-q", "-c", "other")
        self.write("a.txt", "other\n")
        self.git("commit", "-qam", "other")
        self.git("switch", "-q", "main")
        self.write("a.txt", "main\n")
        self.git("commit", "-qam", "main")
        self.git("merge", "-q", "other", check=False)              # conflict
        self.assertIsNone(hookio.index_tree(self.repo))         # precommit: no tree to compare, never fresh


class VerifyStagedTest(StopBase):
    """VERIFYTREE (E2E-08, user 2026-10-30 '다수 관행으로 확정 (pre-commit 방식)'): `verb verify --staged` sets the unstaged
    and untracked changes aside, verifies exactly the staged content and always restores them (pre-commit's way)."""

    def setUp(self):
        super().setUp()
        self.head = self.git("rev-parse", "HEAD")
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.git("reset", "-q", "--hard", self.head)
        self.git("clean", "-qfd", "--", "staged")
        for f in ("fail.flag", "lint.flag", "notes.txt"):
            self.touch(f, False)
        self.git("update-ref", "-d", "refs/agentive/verify-staged", check=False)

    def git(self, *args, check=True):
        r = subprocess.run(["git", "-C", self.repo] + list(args), capture_output=True, text=True, env=GIT_ENV)
        if check:
            self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def write(self, rel, text):
        os.makedirs(os.path.dirname(self.path(rel)) or self.repo, exist_ok=True)
        with open(self.path(rel), "w", newline="") as f:
            f.write(text)

    def scene(self):
        """staged/a.txt staged as v1 and edited again (unstaged v2); staged/b.txt staged; fail.flag untracked (fails test)."""
        self.write("staged/a.txt", "v1\n")
        self.write("staged/b.txt", "b\n")
        self.git("add", "staged")
        self.write("staged/a.txt", "v2 not staged\n")
        self.touch("fail.flag")
        os.chmod(self.path("fail.flag"), 0o755)
        return {"staged/a.txt": "v2 not staged\n", "staged/b.txt": "b\n", "fail.flag": ""}

    def assert_restored(self, files):
        for rel, text in files.items():
            self.assertEqual(rtext(self.path(rel)), text, rel)
        self.assertTrue(os.stat(self.path("fail.flag")).st_mode & 0o100)
        self.assertEqual(self.git("diff", "--cached", "--name-only").splitlines(), ["staged/a.txt", "staged/b.txt"])
        self.assertEqual(self.git("show", ":staged/a.txt"), "v1")              # the index is untouched

    def test_e2e_08_verify_staged_runs_on_the_staged_content_and_restores(self):
        files = self.scene()
        self.assertEqual(self.verb("verify")[0], 1)             # the work tree (with fail.flag) is red
        rc, out = self.verb("verify", "--staged")
        self.assertEqual(rc, 0, out)                            # the staged content is green: fail.flag was set aside
        self.assertIn("NOTE verify --staged: 2 path(s) not staged", out)
        self.assert_restored(files)
        v = jload(self.path(".agents/state/verify.json"))
        self.assertEqual(schema_errors("verify-result", v), [])
        self.assertEqual(v["content_tree"], self.git("write-tree"))   # the record describes exactly the commit
        self.assertEqual(self.git("for-each-ref", "refs/agentive/"), "")   # backup removed after a checked restore

    def test_e2e_08_verify_staged_restores_when_the_verbs_fail(self):
        files = self.scene()
        self.git("add", "-f", "fail.flag")                      # now the staged content itself fails
        self.write("notes.txt", "untracked note\n")
        files["notes.txt"] = "untracked note\n"
        rc, out = self.verb("verify", "--staged")
        self.assertEqual(rc, 1, out)
        self.assertTrue(out.strip().splitlines()[-1].startswith("AGENTIVE_FAIL verb=test"), out)
        for rel, text in files.items():
            self.assertEqual(rtext(self.path(rel)), text, rel)
        self.assertEqual(self.git("for-each-ref", "refs/agentive/"), "")

    def test_e2e_08_verify_staged_restores_on_an_error_or_interrupt(self):
        from agentive import verb as verbmod
        manifest = ayaml.load(self.path(".agents/manifest.yaml"))
        for exc in (RuntimeError("boom"), KeyboardInterrupt()):
            files = self.scene()
            orig = verbmod._verify

            def broken(*a, **k):
                self.assertFalse(os.path.exists(self.path("fail.flag")))           # set aside while the verbs run
                self.assertEqual(rtext(self.path("staged/a.txt")), "v1\n")
                raise exc
            verbmod._verify = broken
            try:
                with self.assertRaises(type(exc)):
                    verbmod._verify_staged(self.repo, manifest)
            finally:
                verbmod._verify = orig
            self.assert_restored(files)
            self.assertEqual(self.git("for-each-ref", "refs/agentive/"), "")
            self.cleanup()

    def test_e2e_08_verify_staged_keeps_the_backup_when_the_restore_fails(self):
        from agentive import verb as verbmod
        manifest = ayaml.load(self.path(".agents/manifest.yaml"))
        files = self.scene()
        orig, calls = verbmod._set_paths, []

        def flaky(root, tree, paths):
            calls.append(tree)
            return orig(root, tree, paths) if len(calls) == 1 else (False, "simulated restore failure")
        verbmod._set_paths = flaky
        import contextlib
        import io
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                rc = verbmod._verify_staged(self.repo, manifest)
        finally:
            verbmod._set_paths = orig
        out = buf.getvalue()
        self.assertEqual(rc, 2, out)
        last = out.strip().splitlines()[-1]
        self.assertTrue(last.startswith("AGENTIVE_FAIL verb=verify exit=2"), last)
        self.assertIn("git restore --source=refs/agentive/verify-staged --worktree", last)
        self.assertNotEqual(self.git("for-each-ref", "refs/agentive/"), "")    # the backup is kept
        paths = self.git("diff", "--name-only", self.git("write-tree"), "refs/agentive/verify-staged").splitlines()
        self.git("restore", "--source=refs/agentive/verify-staged", "--worktree", "--", *paths)   # the printed recovery
        self.assert_restored(files)

    # --- 6e7903b CI failure: the GitHub runner has no git identity, `git commit-tree` for the backup ref was refused ---

    def _no_identity(self):
        """The no-identity environment, plus a repo-local `useConfigOnly` (git must not guess an identity from the host),
        proved to refuse `commit-tree` so the regression tests below cannot pass vacuously."""
        env = no_identity_env(ENV)
        self.git("config", "user.useConfigOnly", "true")
        self.addCleanup(self.git, "config", "--unset", "user.useConfigOnly", check=False)
        tree = self.git("write-tree")
        r = subprocess.run(["git", "-C", self.repo, "commit-tree", tree, "-m", "x"], capture_output=True, text=True, env=env)
        self.assertNotEqual(r.returncode, 0, "this environment must have no usable git identity")
        return env

    def _verb_without_identity(self, env, *args):
        r = subprocess.run([self.path(".agents/bin/verb")] + list(args), capture_output=True, text=True, cwd=self.repo, env=env)
        return r.returncode, r.stdout

    def test_ci_no_git_identity_verify_staged_runs_and_restores(self):
        env = self._no_identity()
        files = self.scene()
        rc, out = self._verb_without_identity(env, "verify", "--staged")
        self.assertEqual(rc, 0, out)
        self.assertIn("NOTE verify --staged: 2 path(s) not staged", out)
        self.assertNotIn("could not record the backup commit", out)
        self.assert_restored(files)
        self.assertEqual(self.git("for-each-ref", "refs/agentive/"), "")

    def test_ci_no_git_identity_backup_commit_uses_the_internal_identity(self):
        from agentive import verb as verbmod
        env = self._no_identity()
        self.scene()
        work = hookio.content_tree(self.repo)
        saved = {k: os.environ.get(k) for k in verbmod.BACKUP_IDENT}
        try:
            for k in verbmod.BACKUP_IDENT:
                os.environ.pop(k, None)
            os.environ.update({k: v for k, v in env.items() if k.startswith("GIT_CONFIG") or k == "XDG_CONFIG_HOME"})
            sha = verbmod._backup_commit(self.repo, work, self.head)
        finally:
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v
        self.assertTrue(sha)
        self.assertEqual(self.git("rev-parse", sha + "^{tree}"), work)
        self.assertEqual(self.git("log", "-1", "--format=%an <%ae> | %cn <%ce>", sha), "agentive <agentive@localhost> | agentive <agentive@localhost>")

    def test_ci_no_git_identity_signing_config_does_not_block_the_backup(self):
        env = self._no_identity()
        for k, v in (("commit.gpgsign", "true"), ("gpg.program", "false")):      # a signing setup that cannot sign here
            self.git("config", k, v)
            self.addCleanup(self.git, "config", "--unset", k, check=False)
        files = self.scene()
        rc, out = self._verb_without_identity(env, "verify", "--staged")
        self.assertEqual(rc, 0, out)
        self.assert_restored(files)


class VerbStopTest(StopBase):
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
        self.assertEqual(rc, 0)
        self.assert_done(out)
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


class StopFingerprintTest(StopBase):
    """V1-13, revised by VERIFYTREE (E2E-08): the Stop hook reruns verify only when the work content changed."""

    def setUp(self):
        super().setUp()
        self.extra = []

    def tearDown(self):
        for rel in self.extra:
            self.touch(rel, False)
        self.touch("fail.flag", False)

    def new(self, rel, text=""):
        self.extra.append(rel)
        with open(self.path(rel), "w") as f:
            f.write(text)

    def run_id(self):
        return jload(self.path(".agents/state/verify.json"))["run_id"]

    def test_e2e_08_content_tree_tracks_content_not_commits(self):
        """VERIFYTREE (E2E-08): the key is the tree of the work content; .agents/state is excluded, a commit changes nothing."""
        t0 = hookio.content_tree(self.repo)
        self.assertRegex(t0, r"^[a-f0-9]{40}$")
        self.assertEqual(hookio.content_tree(self.repo), t0)
        self.new("fp.txt", "one")
        t1 = hookio.content_tree(self.repo)
        self.assertNotEqual(t1, t0)
        self.new("fp.txt", "two")                               # same untracked name, other content
        self.assertNotEqual(hookio.content_tree(self.repo), t1)
        t2 = hookio.content_tree(self.repo)
        with open(self.path(".agents/state/scratch.log"), "w") as f:       # .agents/state is excluded
            f.write("x")
        self.extra.append(".agents/state/scratch.log")
        self.assertEqual(hookio.content_tree(self.repo), t2)
        self.touch("fp.txt", False)
        self.assertEqual(hookio.content_tree(self.repo), t0)
        self.assertEqual(t0, subprocess.run(["git", "-C", self.repo, "rev-parse", "HEAD^{tree}"], capture_output=True, text=True).stdout.strip())
        self.assertIsNone(hookio.content_tree(tempfile.gettempdir() + "/agentive-no-such-dir"))
        self.assertEqual([f for f in os.listdir(self.path(".git")) if f.startswith("agentive-")], [])    # the scratch index is removed

    def test_e2e_08_stop_after_commit_is_unchanged(self):
        head = subprocess.run(["git", "-C", self.repo, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        self.addCleanup(lambda: subprocess.run(["git", "-C", self.repo, "reset", "-q", "--hard", head], check=True, env=GIT_ENV))
        self.new("feature.txt", "new content\n")
        rc, out = self.verb("verify")                           # verify on the dirty tree, then the commit procedure
        self.assertEqual(rc, 0, out)
        rid = self.run_id()
        subprocess.run(["git", "-C", self.repo, "add", "feature.txt"], check=True, env=GIT_ENV)
        subprocess.run(["git", "-C", self.repo, "commit", "-qm", "feature"], check=True, env=GIT_ENV)
        self.extra.remove("feature.txt")
        rc, out, err = self.stop()
        self.assertEqual(rc, 0, err)
        self.assertTrue(out["systemMessage"].startswith("DONE (unchanged)"), out)
        self.assertEqual(self.run_id(), rid)                    # the commit alone did not rerun verify

    def test_e2e_08_verb_command_change_invalidates_the_record(self):
        rc, out, err = self.stop()
        rid = self.run_id()
        self.assertEqual(self.stop()[1]["systemMessage"][:16], "DONE (unchanged)")
        tree = hookio.content_tree(self.repo)
        orig = rtext(self.path(".agents/manifest.yaml"))
        self.edit_manifest(lambda m: m["verbs"]["lint"].__setitem__("cmd", "test ! -e lint.flag && true"))
        from agentive import verb as verbmod
        rec = jload(self.path(".agents/state/verify.json"))
        self.assertFalse(verbmod.fresh_for(rec, rec["content_tree"], ayaml.load(self.path(".agents/manifest.yaml"))))
        with open(self.path(".agents/manifest.yaml"), "w") as f:
            f.write(orig)
        self.assertTrue(verbmod.fresh_for(rec, tree, ayaml.load(self.path(".agents/manifest.yaml"))))
        self.assertEqual(self.run_id(), rid)

    def test_unchanged_green_tree_skips_verify(self):
        rc, out, err = self.stop()
        self.assertEqual(rc, 0)
        self.assert_done(out)
        v = jload(self.path(".agents/state/verify.json"))
        self.assertEqual(schema_errors("verify-result", v), [])
        self.assertRegex(v["content_tree"], r"^[a-f0-9]{40}$")
        rid = v["run_id"]
        rc, out, err = self.stop()
        self.assertEqual(rc, 0)
        self.assertTrue(out["systemMessage"].startswith("DONE (unchanged)"), out)
        self.assertEqual(self.run_id(), rid)                    # no rerun
        st = jload(self.path(".agents/state/stop.json"))
        self.assertEqual((st["last_reason"], st["consecutive_blocks"]), ("DONE", 0))
        self.assertEqual(schema_errors("stop-state", st), [])

    def test_red_unchanged_with_no_block_does_not_rerun_or_block(self):
        self.touch("fail.flag")
        self.verb("verify")                                     # the tree was already red before this turn
        rid = self.run_id()
        rc, out, err = self.stop()
        self.assertEqual(rc, 0, err)
        self.assertRegex(out["systemMessage"], r"^RED-UNCHANGED total_failures=1: no file changed since the last verify")
        self.assertEqual(self.run_id(), rid)
        st = jload(self.path(".agents/state/stop.json"))
        self.assertEqual((st["last_reason"], st["consecutive_blocks"], st["total_blocks"]), ("RED-UNCHANGED", 0, 0))
        self.assertEqual(schema_errors("stop-state", st), [])
        rc, out, err = self.stop("codex")                       # Codex: no plain stdout on Stop
        self.assertEqual((rc, out), (0, ""))
        self.assertEqual(self.run_id(), rid)

    def test_red_unchanged_after_a_block_uses_the_cached_result(self):
        self.edit_manifest(lambda m: m.__setitem__("stop_conditions", {"max_blocks": 8, "no_progress_rounds": 3}))
        self.touch("fail.flag")
        rc, out, err = self.stop()                              # changed tree: full verify, block 1
        self.assertEqual(rc, 2)
        rid = self.run_id()
        rc, out, err = self.stop()                              # unchanged, blocks > 0: cached verify.json, block 2
        self.assertEqual(rc, 2)
        self.assertIn("AGENTIVE_FAIL verb=test", err)
        self.assertEqual(self.run_id(), rid)
        st = jload(self.path(".agents/state/stop.json"))
        self.assertEqual((st["consecutive_blocks"], st["failure_window"], st["last_reason"]), (2, [1, 1], "BLOCK"))
        self.assertEqual(schema_errors("stop-state", st), [])
        rc, out, err = self.stop()                              # window [1,1,1] over 3 rounds: NO-PROGRESS, still no rerun
        self.assertEqual(rc, 0)
        self.assertIn("NO-PROGRESS", out["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(self.run_id(), rid)

    def test_cached_path_reaches_cap(self):
        self.edit_manifest(lambda m: m.__setitem__("stop_conditions", {"max_blocks": 2, "no_progress_rounds": 5}))
        self.touch("fail.flag")
        self.assertEqual(self.stop()[0], 2)
        rid = self.run_id()
        self.assertEqual(self.stop()[0], 2)
        rc, out, err = self.stop()
        self.assertEqual((rc, out["systemMessage"][:4]), (0, "CAP:"))
        self.assertEqual(self.run_id(), rid)

    def test_changed_tree_reruns_verify(self):
        self.touch("fail.flag")
        self.verb("verify")
        rid = self.run_id()
        self.new("another.txt", "edit")
        rc, out, err = self.stop()
        self.assertEqual(rc, 2)                                 # full rerun, red, blocks
        self.assertNotEqual(self.run_id(), rid)
        self.touch("fail.flag", False)                          # fixing it changes the tree again
        rc, out, err = self.stop()
        self.assertEqual(rc, 0)
        self.assert_done(out)

    def test_pending_verbs_are_named_with_the_two_exits(self):
        """PENDINGNOTICE (V2-23): DONE with some verb pending names it and both ways out; the greenfield case (all pending) keeps its own line."""
        rc, out, err = self.stop()
        msg = out["systemMessage"]
        self.assertEqual(rc, 0)
        self.assertIn("PENDING=typecheck has no command", msg)
        self.assertIn("give the command", msg)
        self.assertIn("review.pipeline.stages.RV1.require_checks", msg)
        self.assertNotIn("no stack evidence", msg)

    def test_e2e_19_stop_tail_says_a_pending_check_is_not_required(self):
        """E2E-19 (EFFECTIVECHECKS): the Stop tail no longer tells the user to remove a default check; it is not required."""
        msg = self.stop()[1]["systemMessage"]
        self.assertIn("until then the check is not required, unless review.pipeline.stages.RV1.require_checks", msg)
        self.assertNotIn("or remove the check from", msg)

    def test_editing_an_untracked_file_reruns_verify(self):
        self.new("wip.txt", "a")
        self.assert_done(self.stop()[1])
        rid = self.run_id()
        self.new("wip.txt", "b")
        self.assert_done(self.stop()[1])
        self.assertNotEqual(self.run_id(), rid)


class LintHookTest(Repo):
    """V1-13: PostToolUse lints only the edited file, only for a covered extension."""

    def setUp(self):
        for f in ("lint.flag", "lint.args", "main.go", "util.go", "page.ts"):
            self.touch(f, False)
        self.edit_manifest(self.record_lint)

    @staticmethod
    def record_lint(m):
        m["verbs"]["lint"]["cmd"] = "test ! -e lint.flag && echo >> lint.args"
        m["verbs"]["lint"]["file_args"] = True        # init derives false for `go vet ./...`; this stand-in command takes files

    def tearDown(self):
        for f in ("lint.flag", "lint.args", "main.go", "util.go", "page.ts"):
            self.touch(f, False)

    def post(self, file_path, tool="Edit"):
        return self.hook("post-tool-use-lint.py", {"tool_name": tool, "tool_input": {"file_path": self.path(file_path)}})

    def linted(self):
        p = self.path("lint.args")
        return rtext(p).split() if os.path.exists(p) else None

    def test_lints_only_the_edited_file_with_a_covered_extension(self):
        self.touch("main.go")
        self.assertEqual(self.post("main.go")[0], 0)
        self.assertEqual(self.linted(), ["main.go"])            # `verb lint -- main.go`, not the whole project

    def test_docs_and_other_extensions_are_skipped(self):
        self.touch("README.md")
        self.addCleanup(self.touch, "README.md", False)
        self.touch("lint.args", False)
        for rel in ("README.md", "AGENTS.md", "go.mod", ".agents/manifest.yaml"):
            self.assertEqual(self.post(rel)[0], 0, rel)
        self.assertIsNone(self.linted())
        self.touch("lint.flag")                                 # a docs edit must not fail on a red lint either
        self.assertEqual(self.post("README.md")[0], 0)

    def test_missing_file_and_outside_repo_are_skipped(self):
        self.assertEqual(self.post("gone.go")[0], 0)
        self.assertEqual(self.hook("post-tool-use-lint.py", {"tool_name": "Write", "tool_input": {"file_path": "/etc/hosts.go"}})[0], 0)
        self.assertIsNone(self.linted())

    def test_red_lint_blocks_with_the_failure_line(self):
        self.touch("main.go")
        self.touch("lint.flag")
        rc, out, err = self.post("main.go")
        self.assertEqual(rc, 2)
        self.assertIn("AGENTIVE_FAIL verb=lint", err)

    def test_verb_without_file_args_is_deferred_to_stop(self):
        self.edit_manifest(lambda m: m["verbs"]["lint"].__setitem__("file_args", False))
        self.touch("main.go")
        self.touch("lint.flag")
        self.assertEqual(self.post("main.go")[0], 0)            # skipped here, even though lint would fail
        self.assertIsNone(self.linted())
        rc, out, err = self.hook("stop-verify.py", {"stop_hook_active": False})
        self.assertEqual(rc, 2)                                 # Stop's verify runs lint once
        self.assertIn("AGENTIVE_FAIL verb=lint", err)

    def test_explicit_exts_override_the_derived_set(self):
        self.edit_manifest(lambda m: m["verbs"]["lint"].__setitem__("exts", [".ts"]))
        self.touch("main.go")
        self.touch("page.ts")
        self.post("main.go")
        self.assertIsNone(self.linted())
        self.post("page.ts")
        self.assertEqual(self.linted(), ["page.ts"])

    def test_pending_lint_is_skipped(self):
        self.edit_manifest(lambda m: m["verbs"].__setitem__("lint", {"status": "pending", "reason": "no_evidence"}))
        self.touch("main.go")
        self.assertEqual(self.post("main.go")[0], 0)
        self.assertIsNone(self.linted())

    def test_codex_apply_patch_paths_are_parsed(self):
        self.touch("main.go")
        self.touch("util.go")
        patch = ("*** Begin Patch\n*** Update File: main.go\n@@\n-a\n+b\n*** Add File: util.go\n+package x\n"
                 "*** Update File: README.md\n@@\n-a\n+b\n*** End Patch\n")
        rc, out, err = self.hook("post-tool-use-lint.py", {"tool_name": "apply_patch", "tool_input": {"command": patch}}, tool="codex")
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.linted(), ["main.go", "util.go"])     # README.md is a doc: dropped, the .go files linted together

    def test_codex_patch_parse_failure_skips(self):
        self.touch("main.go")
        self.touch("lint.flag")                                  # a run would fail: exit 0 proves the skip
        for cmd in ("not a patch at all", "*** Begin Patch\n*** Frobnicate: main.go\n*** End Patch\n", ""):
            rc, out, err = self.hook("post-tool-use-lint.py", {"tool_name": "apply_patch", "tool_input": {"command": cmd}}, tool="codex")
            self.assertEqual(rc, 0, cmd)
        self.assertIsNone(self.linted())

    def test_parse_helpers(self):
        self.assertEqual(lintscope.parse_patch_paths("*** Begin Patch\n*** Update File: a/b.go\n*** Move to: a/c.go\n"
                                                     "*** Delete File: old.go\n*** Update File: a/b.go\n*** End Patch"),
                         ["a/b.go", "a/c.go", "old.go"])
        self.assertEqual(lintscope.paths_from_hook_input({"tool_name": "apply_patch", "tool_input": {"command": "x"}}), ([], False))
        self.assertEqual(lintscope.paths_from_hook_input({"tool_name": "Edit", "tool_input": {"file_path": "a.go"}}), (["a.go"], True))
        self.assertEqual(lintscope.paths_from_hook_input({"tool_name": "Bash", "tool_input": {"command": "ls"}}), ([], True))

    def test_edit_lint_replaces_the_whole_repo_target(self):
        """E2E-04: `verb lint -- <file>` runs the lint command on the file, not `<cmd> . <file>` (the whole repo again)."""
        with open(self.path("lint.sh"), "w") as f:
            f.write('printf "%s\\n" "$@" > lint.args\n')
        self.addCleanup(self.touch, "lint.sh", False)
        self.touch("main.go")
        for cmd, want in (("sh lint.sh .", ["main.go"]), ("sh lint.sh ./...", ["main.go"]), ("sh lint.sh --strict ./", ["--strict", "main.go"])):
            self.edit_manifest(lambda m, c=cmd: m["verbs"]["lint"].__setitem__("cmd", c))
            self.assertEqual(self.post("main.go")[0], 0, cmd)
            self.assertEqual(self.linted(), want, cmd)
        self.edit_manifest(lambda m: m["verbs"]["lint"].__setitem__("cmd", "sh lint.sh scripts"))
        self.post("main.go")
        self.assertEqual(self.linted(), ["scripts", "main.go"])       # a path the manifest does not name stays: append

    def test_file_command_table(self):
        paths = {"src": [{"path": "src", "proof": "exists"}], "tests": [{"path": "tests/", "proof": "exists"}]}
        fc = lintscope.file_command
        self.assertEqual(fc("uv run ruff check --extend-exclude .agents,.claude,.codex .", ["src/a.py"], paths),
                         "uv run ruff check --extend-exclude .agents,.claude,.codex src/a.py")
        self.assertEqual(fc("npx eslint --ignore-pattern .agents/ --ignore-pattern .claude/ --ignore-pattern .codex/ .", ["a b.ts"]),
                         "npx eslint --ignore-pattern .agents/ --ignore-pattern .claude/ --ignore-pattern .codex/ 'a b.ts'")
        self.assertEqual(fc("npx biome check src tests", ["src/x.ts"], paths), "npx biome check src/x.ts")     # explicit manifest paths
        self.assertEqual(fc("npx biome check ./src ./tests/", ["src/x.ts"], paths), "npx biome check src/x.ts")
        self.assertEqual(fc("ruff check scripts", ["src/a.py"], paths), "ruff check scripts src/a.py")       # not a manifest path
        self.assertEqual(fc("flake8 --extend-exclude .agents,.claude,.codex", ["a.py"]), "flake8 --extend-exclude .agents,.claude,.codex a.py")
        self.assertEqual(fc("gofmt -l .", ["main.go"]), "gofmt -l main.go")
        self.assertEqual(fc("ruff check .", ["--fix"]), "ruff check . --fix")                                 # flags keep the target
        self.assertEqual(fc("ruff check . && mypy .", ["a.py"]), "ruff check . && mypy . a.py")             # shell-joined: whole
        self.assertEqual(fc(".", ["a.py"]), ". a.py")                                                       # never the command word

    def test_installed_layer_files_are_not_linted(self):
        """E2E-01: an edit under .agents, .claude or .codex is never handed to the project's linter."""
        sel = lintscope.select_files(self.repo, [self.path(".agents/lib/agentive/verb.py"), self.path(".agents/hooks/stop-verify.py"),
                                                 ".claude/settings.json"], {".py", ".json"})
        self.assertEqual(sel, [])
        self.touch("main.go")
        self.assertEqual(lintscope.select_files(self.repo, ["main.go"], {".go"}), ["main.go"])

    def test_derived_extensions(self):
        m = ayaml.load(self.path(".agents/manifest.yaml"))
        self.assertEqual(lintscope.lint_extensions(m), {".go"})
        ts = {"verbs": {"lint": {"cmd": "x", "evidence": {"files": ["package.json", "tsconfig.json"]}}}}
        self.assertIn(".ts", lintscope.lint_extensions(ts))
        self.assertNotIn(".md", lintscope.lint_extensions(ts))
        self.assertNotIn(".json", lintscope.lint_extensions(ts))
        self.assertEqual(lintscope.lint_extensions({"verbs": {"lint": {"cmd": "x", "evidence": {"files": ["Makefile"]}}}}), set())


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
                           "files": [{"path": ".claude/settings.json", "category": "claude_settings", "reason": "untrusted_folder",
                                      "next": "accept the workspace trust dialog once", "rendered_sha256": "a" * 64}]}, f)
            rc, out, err = self.hook("session-start-notice.py", {"source": "resume"})
            ctx = out["hookSpecificOutput"]["additionalContext"]
            self.assertIn("SUPPLY=mismatch:verify", ctx)
            self.assertIn("PENDING_CONFIRMATION=1 files written but not yet effective: untrusted_folder", ctx)
            self.assertIn("agentive:", out["systemMessage"])
            self.assertIn("accept the workspace trust dialog once", out["systemMessage"])
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

    def queue(self, n):
        p = self.path(".agents/state/harvest-queue.jsonl")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        orig = rtext(p) if os.path.exists(p) else None

        def restore():
            if orig is None:
                if os.path.exists(p):
                    os.unlink(p)
            else:
                with open(p, "w") as f:
                    f.write(orig)
        self.addCleanup(restore)
        with open(p, "w") as f:
            for i in range(n):
                f.write(json.dumps({"session_id": f"q{i}", "transcript_path": "t.jsonl", "reason": "other",
                                    "signals": {"stop_blocks": 3}, "at": "2026-10-29T00:00:00Z"}) + "\n")

    def start(self, source="startup", tool="claude", env=None):
        rc, out, err = self.hook("session-start-notice.py", {"source": source}, tool=tool, env=env)
        self.assertEqual(rc, 0, err)
        return out["hookSpecificOutput"]["additionalContext"], out.get("systemMessage", "")

    def test_harvest_line_threshold(self):
        self.queue(0)
        self.assertNotIn("HARVEST=", self.start()[0])
        self.queue(1)
        ctx, msg = self.start()
        self.assertNotIn("HARVEST=", ctx)                       # one queued session is not a repetition
        self.assertNotIn("distill", msg)
        self.queue(2)
        ctx, msg = self.start()
        self.assertIn("HARVEST=2; NEXT=ask_user_to_run:/distill; DO_NOT=run_distill_unasked", ctx.splitlines())
        self.assertIn("/distill", msg)
        self.assertIn("only after you agree", msg)
        self.queue(5)
        self.assertIn("HARVEST=5; NEXT=ask_user_to_run:/distill; DO_NOT=run_distill_unasked", self.start("resume")[0].splitlines())

    def test_harvest_line_codex_and_other_sources(self):
        self.queue(3)
        ctx, _ = self.start(tool="codex")
        self.assertIn("HARVEST=3; NEXT=ask_user_to_run:$distill; DO_NOT=run_distill_unasked", ctx.splitlines())
        for source in ("compact", "clear"):
            self.assertNotIn("HARVEST=", self.start(source)[0])

    def test_harvest_line_without_a_human(self):
        self.queue(2)
        ctx, msg = self.start(env=dict(ENV, AGENTIVE_HEADLESS="1"))
        self.assertIn("HARVEST=2; NEXT=none", ctx.splitlines())
        self.assertNotIn("ask_user_to_run:/distill", ctx)
        self.assertNotIn("distill", msg)
        ctx, _ = self.start(env=dict(ENV, CI="true"))
        self.assertIn("HARVEST=2; NEXT=none", ctx.splitlines())

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
