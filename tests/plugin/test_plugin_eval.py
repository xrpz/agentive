# agentive-unit: selftest.component
# design: decisions.accepted[PLUGINEVAL], decisions.accepted[MEASUREDWITH], spec.framework_repo
"""tools/plugin_eval.py (impl26 B3): the throw-away eval plugin is assembled from the generated plugin, the repo skills, the repo hooks and
the cases; the suite follows the official case format; the scaffold script builds a workspace; the eval never runs in selftest and has no
schedule. No model call is made here."""
import atexit
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

import yaml  # dev-time only

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import plugin_eval as pe  # noqa: E402
import selftest  # noqa: E402


_OUT = tempfile.mkdtemp(prefix="agentive-pe-test-")
atexit.register(shutil.rmtree, _OUT, True)
_PLUGIN = pe.build(_OUT)            # one assembly for the whole file: it installs the fixture and takes a few seconds


class Assembly(unittest.TestCase):
    out = _OUT
    plugin = _PLUGIN

    def test_the_plugin_directory_holds_skills_hooks_and_cases(self):
        for name in ("pr", "chain-intent", "verify", "precommit-review"):
            self.assertTrue(os.path.isfile(os.path.join(self.plugin, "skills", name, "SKILL.md")), name)
        for name in ("init", "update", "reprove"):                      # the plugin's own skills stay
            self.assertTrue(os.path.isfile(os.path.join(self.plugin, "skills", name, "SKILL.md")), name)
        with open(os.path.join(self.plugin, "hooks", "hooks.json"), encoding="utf-8") as f:
            hooks = json.load(f)["hooks"]
        for event in ("SessionStart", "PreToolUse", "Stop", "PostToolUse"):
            self.assertIn(event, hooks)
        cmds = json.dumps(hooks)
        self.assertIn("session-start-uninitialized.py", cmds)            # the plugin's own hook is kept
        self.assertIn("stop-verify.py", cmds)                            # and the repo layer's hooks are added
        self.assertIn("pre-tool-use-guard.py", cmds)
        self.assertEqual(len(hooks["SessionStart"]), 2)

    def test_the_suite_passes_the_format_check(self):
        self.assertEqual(pe.check_suite(self.plugin), [])

    def test_the_context_stand_in_and_the_scaffold_path_are_filled(self):
        for case in os.listdir(os.path.join(self.plugin, "evals")):
            d = os.path.join(self.plugin, "evals", case)
            if not os.path.isfile(os.path.join(d, "prompt.md")):
                continue
            fm, _ = pe.frontmatter(os.path.join(d, "prompt.md"))
            self.assertIn("Chain entry (intent.md needed?)", fm["append_system_prompt"], case)   # the installed AGENTS.md text
            with open(os.path.join(d, "scaffold.sh"), encoding="utf-8") as f:
                self.assertIn(self.plugin, f.read(), case)

    def test_nothing_is_written_into_the_repo(self):
        before = subprocess.run(["git", "status", "--short", "--", "plugins"], cwd=ROOT, capture_output=True, text=True).stdout
        pe.build(tempfile.mkdtemp(prefix="agentive-pe-test2-", dir=self.out))
        after = subprocess.run(["git", "status", "--short", "--", "plugins"], cwd=ROOT, capture_output=True, text=True).stdout
        self.assertEqual(before, after)

    def test_each_scaffold_mode_builds_a_workspace(self):
        for mode, branch in (("plain", "main"), ("typo", "main"), ("branch", "feat/slugify-docs")):
            ws, home = tempfile.mkdtemp(prefix="agentive-pe-ws-"), tempfile.mkdtemp(prefix="agentive-pe-home-")
            self.addCleanup(shutil.rmtree, ws, True)
            self.addCleanup(shutil.rmtree, home, True)
            env = {"PATH": os.environ["PATH"], "HOME": home, "TMPDIR": home}
            r = subprocess.run(["bash", os.path.join(self.plugin, "evals", "_shared", "scaffold.sh"), mode], cwd=ws, env=env,
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            out = subprocess.run(["git", "status", "--short"], cwd=ws, capture_output=True, text=True).stdout
            self.assertEqual(out, "", f"{mode}: the workspace is not clean:\n{out}")
            self.assertEqual(subprocess.run(["git", "branch", "--show-current"], cwd=ws, capture_output=True, text=True).stdout.strip(), branch)
            self.assertTrue(os.path.isfile(os.path.join(ws, ".agents", "manifest.yaml")))
            self.assertFalse(os.path.exists(os.path.join(ws, ".agents", "state", "verify.json")), "the Stop hook's record exists before the run")
            with open(os.path.join(ws, "README.md"), encoding="utf-8") as f:
                self.assertEqual("recieve" in f.read(), mode == "typo")


class Checker(unittest.TestCase):
    def _suite(self, **files):
        d = tempfile.mkdtemp(prefix="agentive-pe-suite-")
        self.addCleanup(shutil.rmtree, d, True)
        shutil.copytree(Assembly.plugin, os.path.join(d, "p"))
        for rel, text in files.items():
            p = os.path.join(d, "p", "evals", rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            if text is None:
                os.remove(p)
            else:
                with open(p, "w", encoding="utf-8") as f:
                    f.write(text)
        return os.path.join(d, "p")

    def test_the_checker_names_what_the_official_format_rejects(self):
        cases = {
            "guard-denies-force-push/graders/push-attempted.md": ("---\ntype: tool_used\ntool: Bash\nbogus: 1\n---\n", "unknown key bogus"),
            "guard-denies-force-push/graders/guard-blocked.md": ("---\ntype: regex\ntarget: trace\npattern: '(?s)a.b'\n---\n", "inline flags"),
            "verify-runs-at-stop/graders/code-written.md": ("---\ntype: magic\n---\n", "type must be one of"),
            "verify-runs-at-stop/prompt.md": ("---\nmax_turns: 5\nnope: 1\nappend_system_prompt: x\n---\nhi\n", "unknown key nope"),
        }
        for rel, (text, expect) in cases.items():
            problems = pe.check_suite(self._suite(**{rel: text}))
            self.assertTrue(any(expect in p for p in problems), (expect, problems))

    def test_a_case_without_a_grader_is_a_problem(self):
        d = self._suite()
        shutil.rmtree(os.path.join(d, "evals", "guard-denies-force-push", "graders"))
        self.assertTrue(any("no grader" in p for p in pe.check_suite(d)))

    def test_every_case_has_a_free_grader_and_a_result_grader(self):
        for case in sorted(os.listdir(pe.CASES)):
            gdir = os.path.join(pe.CASES, case, "graders")
            if not os.path.isdir(gdir):
                continue
            kinds = [pe.frontmatter(os.path.join(gdir, g))[0]["type"] for g in os.listdir(gdir)]
            self.assertTrue(set(kinds) & {"regex", "tool_used", "file_exists"}, case)


class Command(unittest.TestCase):
    def test_the_command_pins_the_models_and_stays_local(self):
        cmd = pe.eval_command("/p", "/o")
        cfg = pe.load_config()
        self.assertEqual(cmd[:4], ["claude", "plugin", "eval", "/p"])
        for flag, value in (("--model", cfg["model"]), ("--judge-model", cfg["judge_model"]), ("--threshold", str(cfg["threshold"]))):
            self.assertEqual(cmd[cmd.index(flag) + 1], value)
        for flag in ("--scaffold", "--trust-plugin", "--no-publish", "--max-cost-usd", "--json"):
            self.assertIn(flag, cmd)
        self.assertNotIn("--publish-report", cmd)
        self.assertLess(cmd.index("--json"), cmd.index("--allow-tools"))     # the page: the target goes before --json and --allow-tools

    def test_the_pins_are_the_official_ci_example_ids(self):
        cfg = pe.load_config()
        self.assertEqual((cfg["model"], cfg["judge_model"]), ("claude-sonnet-5", "claude-haiku-4-5"))


class NeverInSelftestNorScheduled(unittest.TestCase):
    def test_selftest_does_not_run_it(self):
        self.assertNotIn("plugin_eval", selftest.STEPS)
        with open(os.path.join(ROOT, "tools", "selftest.py"), encoding="utf-8") as f:
            text = f.read()
        self.assertNotIn("import plugin_eval", text)
        self.assertNotIn("subprocess.run([sys.executable, \"tools/plugin_eval.py", text)

    def test_the_workflow_is_manual_only_and_needs_the_secret(self):
        with open(os.path.join(ROOT, ".github", "workflows", "plugin-eval.yml"), encoding="utf-8") as f:
            text = f.read()
        doc = yaml.safe_load(text)
        self.assertEqual(list(doc["on"]), ["workflow_dispatch"])
        self.assertIn("secrets.ANTHROPIC_API_KEY", text)
        with open(os.path.join(ROOT, ".github", "workflows", "ci.yml"), encoding="utf-8") as f:
            self.assertNotIn("plugin_eval", f.read())


if __name__ == "__main__":
    unittest.main()
