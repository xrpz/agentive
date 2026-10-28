# agentive-unit: selftest.component
# design: spec.plugin.invocation, spec.plugin.command_flags, spec.skill_scripts, spec.init_apply.session_start, spec.distribution.codex.codex_only_bootstrap
"""Plugin layer files (sources under src/plugin/ and the generated plugin directories)."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CLAUDE = os.path.join(ROOT, "plugins", "claude-code", "agentive")
CODEX = os.path.join(ROOT, "plugins", "codex", "agentive")
sys.path.insert(0, os.path.join(ROOT, "src", "repo-layer", "dot-agents", "lib"))
from agentive import ayaml  # noqa: E402

def rtext(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def jload(path):
    return json.loads(rtext(path))


ENV = {k: v for k, v in os.environ.items() if k not in ("CI", "GITHUB_ACTIONS", "AGENTIVE_HEADLESS")}


def frontmatter(path):
    text = rtext(path)
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    assert m, path
    return ayaml.loads(m.group(1)), text


class SkillTest(unittest.TestCase):
    def test_claude_skills_invocation_written_explicitly(self):
        want = {"init": True, "update": True, "reprove": False}
        for name, dmi in want.items():
            fm, text = frontmatter(os.path.join(CLAUDE, "skills", name, "SKILL.md"))
            self.assertEqual(fm["name"], name)
            self.assertIs(fm["disable-model-invocation"], dmi, name)
            self.assertTrue(fm["description"])
            self.assertLessEqual(len(text.splitlines()), 500)
            self.assertIn("${CLAUDE_PLUGIN_ROOT}/payload/engine/apply.py", text)
            self.assertNotIn("CLAUDE_SKILL_DIR", text)
        self.assertIn("--managed-review-copy", frontmatter(os.path.join(CLAUDE, "skills", "init", "SKILL.md"))[0]["argument-hint"])
        self.assertIn("--force", frontmatter(os.path.join(CLAUDE, "skills", "update", "SKILL.md"))[0]["argument-hint"])

    def test_codex_skills_and_openai_yaml(self):
        for name in ("agentive-init", "agentive-update"):
            d = os.path.join(CODEX, "skills", name)
            fm, text = frontmatter(os.path.join(d, "SKILL.md"))
            self.assertEqual(fm["name"], name)
            self.assertIs(fm["disable-model-invocation"], True)
            for bad in ("CLAUDE_SKILL_DIR", "CLAUDE_PLUGIN_ROOT", "allowed-tools", "$ARGUMENTS"):
                self.assertNotIn(bad, text, (name, bad))
            self.assertIn("--tool codex", text)
            oa = ayaml.load(os.path.join(d, "agents", "openai.yaml"))
            self.assertIs(oa["policy"]["allow_implicit_invocation"], False)
            self.assertTrue(os.path.isfile(os.path.join(d, "payload", "engine", "apply.py")), "each Codex skill is self-contained")
            self.assertTrue(os.path.isfile(os.path.join(d, "payload", "index.json")))

    def test_no_plugin_bin_and_hooks_json(self):
        self.assertFalse(os.path.exists(os.path.join(CLAUDE, "bin")))
        h = jload(os.path.join(CLAUDE, "hooks", "hooks.json"))
        cmd = h["hooks"]["SessionStart"][0]["hooks"][0]
        self.assertEqual(cmd["args"], ["${CLAUDE_PLUGIN_ROOT}/hooks/session-start-uninitialized.py"])
        self.assertTrue(os.access(os.path.join(CLAUDE, "hooks", "session-start-uninitialized.py"), os.X_OK))


class PluginHookTest(unittest.TestCase):
    def run_hook(self, cwd, env=None):
        r = subprocess.run([sys.executable, os.path.join(CLAUDE, "hooks", "session-start-uninitialized.py")],
                           input=json.dumps({"cwd": cwd, "source": "startup"}), capture_output=True, text=True, env=env or ENV)
        return r.stdout.strip()

    def test_uninitialized_ready_and_no_human(self):
        d = tempfile.mkdtemp()
        try:
            self.assertEqual(self.run_hook(d), "AGENTIVE_STATE=uninitialized; NEXT=ask_user_to_run:/agentive:init; DO_NOT=reproduce init steps")
            self.assertEqual(self.run_hook(d, dict(ENV, AGENTIVE_HEADLESS="1")), "AGENTIVE_STATE=uninitialized; NEXT=none")
            self.assertEqual(self.run_hook(d, dict(ENV, CLAUDE_CODE_REMOTE="true")).split(";")[1].strip(), "NEXT=ask_user_to_run:/agentive:init")
            os.makedirs(os.path.join(d, ".agents"))
            open(os.path.join(d, ".agents", "manifest.yaml"), "w").close()
            self.assertEqual(self.run_hook(d), "")
        finally:
            shutil.rmtree(d)


class RepoLayerTemplateTest(unittest.TestCase):
    def test_settings_template_shape(self):
        s = jload(os.path.join(ROOT, "src", "repo-layer", "dot-claude", "settings.json"))
        events = set(s["hooks"])
        self.assertEqual(events, {"SessionStart", "PreToolUse", "PostToolUse", "Stop", "FileChanged", "SessionEnd"})
        guard = s["hooks"]["PreToolUse"][0]
        self.assertEqual(guard["matcher"], "Bash|PowerShell")
        self.assertEqual(guard["hooks"][0]["onFailure"], "block")
        self.assertTrue(s["sandbox"]["enabled"])
        self.assertNotIn("network", s["sandbox"])          # the repo sets no allowedDomains
        for ev in s["hooks"].values():
            for grp in ev:
                for h in grp["hooks"]:
                    self.assertNotIn("CLAUDE_PROJECT_DIR\"", h.get("command", ""))
                    for a in h.get("args", []):
                        self.assertTrue(a.startswith("${CLAUDE_PROJECT_DIR}/.agents/hooks/"), a)
                        self.assertTrue(os.path.isfile(os.path.join(ROOT, "src", "repo-layer", "dot-agents", "hooks", os.path.basename(a))), a)

    def test_codex_hooks_deny_only_and_known_events(self):
        h = jload(os.path.join(ROOT, "src", "repo-layer", "dot-codex", "hooks.json"))
        self.assertEqual(set(h["hooks"]), {"SessionStart", "PreToolUse", "PostToolUse", "Stop", "SessionEnd"})
        for ev in h["hooks"].values():
            for grp in ev:
                for x in grp["hooks"]:
                    self.assertIn("--tool codex", x["command"])
        self.assertLessEqual(h["hooks"]["SessionEnd"][0]["hooks"][0]["timeout"], 3)
        src = rtext(os.path.join(ROOT, "src", "repo-layer", "dot-agents", "hooks", "pre-tool-use-guard.py"))
        self.assertNotRegex(src, r'"permissionDecision":\s*"ask"')

    def test_no_forbidden_words(self):
        for base in (os.path.join(ROOT, "src", "plugin"), os.path.join(ROOT, "src", "engine"), os.path.join(ROOT, "src", "repo-layer", "dot-agents", "hooks")):
            for d, _dirs, files in os.walk(base):
                for f in files:
                    if f.endswith((".md", ".py", ".json", ".yaml", ".sh")):
                        t = rtext(os.path.join(d, f)).lower()
                        self.assertNotIn("\u0075nattended", t, f)  # banned word, escaped
                        self.assertNotIn("\ubb34\uc778", t, f)  # banned word, escaped


if __name__ == "__main__":
    unittest.main()
