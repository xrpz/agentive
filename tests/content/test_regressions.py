# agentive-unit: selftest.component
# design: spec.context_management.headless.standard_cmd, spec.repo_layer.files[f-agents-md], spec.distribution, spec.compat
"""Regression tests for the 2026-10-29 implementation verification findings (one test per finding id) in what agents read:
V1-03 (the headless standard command is written out in the skills that run headless), V1-10 / V2-12 (AGENTS.md names the
Codex forms), V1-15 / V2-20 (README install line matches the design), V2-24 (the Codex bootstrap check in the root AGENTS.md
prints the skills.lock hash)."""
import json
import os
import re
import subprocess
import unittest

import yaml

from helpers import PAYLOAD_SKILLS, ROOT, SKILL_NAMES, SKILLS, SRC, YAML_LOADER, read


def design():
    with open(os.path.join(ROOT, "design", "framework.yaml"), encoding="utf-8") as f:
        return yaml.load(f, Loader=YAML_LOADER)


class RegressionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec = design()["spec"]

    def test_v1_03_headless_skills_write_the_design_standard_command(self):
        """HEADLESSBINS: the skills hold a generate-time placeholder and the payload copy is the design's command verbatim."""
        std = self.spec["context_management"]["headless"]["standard_cmd"]
        for skill in ("janitor", "pr-feedback", "distill"):
            self.assertIn("%%agentive:gen.headless_cmd%%", read(SKILLS, skill, "SKILL.md"), skill)
            text = read(PAYLOAD_SKILLS, skill, "SKILL.md")
            self.assertIn(std, text, skill)
            self.assertNotIn("dontAsk ...", text, skill)
        self.assertNotIn("python3 -c", read(SKILLS, "chain-spec", "SKILL.md"))   # the handoff is a skill script the allowlist covers

    def allow_patterns(self):
        std = self.spec["context_management"]["headless"]["standard_cmd"]
        tools = re.search(r'--allowedTools "([^"]*)"', std).group(1)
        return [t[len("Bash("):-1] for t in tools.split(",") if t.startswith("Bash(")], tools.split(",")

    def test_headlessbins_commit_path_commands_are_allowed(self):
        """HEADLESSBINS: every `.agents/bin/...` and skill-script command the work-loop skills run is matched by an allow
        entry of the headless standard command (dontAsk denies the rest); no `.agents/bin/*` wildcard."""
        bash, _ = self.allow_patterns()
        self.assertIn(".agents/bin/agentive-lint *", bash)
        self.assertIn(".agents/bin/agentive-review *", bash)
        self.assertNotIn(".agents/bin/*", bash)
        rx = [re.compile("^" + ".*".join(re.escape(x) for x in p.split("*")) + "$") for p in bash]
        cmd = re.compile(r"`((?:sh \.agents/skills/|\.agents/bin/)[^`]*)`")
        seen = 0
        for name in SKILL_NAMES:
            for c in cmd.findall(read(PAYLOAD_SKILLS, name, "SKILL.md")):
                if "<slug>`" in c or c.endswith("/<slug>"):
                    continue
                if c.strip() in (".agents/bin/verb", ".agents/bin/"):
                    continue
                seen += 1
                self.assertTrue(any(r.match(c) for r in rx), f"{name}: `{c}` is not covered by the headless allowlist")
        self.assertGreater(seen, 10)

    def test_headlessbins_skill_tool_needs_no_allow_entry(self):
        """HEADLESSBINS: the standard command lists no Skill entry. Source: 'By default, Claude can invoke any skill that
        doesn't have disable-model-invocation: true set' (https://code.claude.com/docs/en/skills); a dontAsk run without a
        Skill entry invoked project skills, plain and context: fork (Claude Code 2.1.312). The tools reference table says
        Permission required: Yes for Skill (https://code.claude.com/docs/en/tools-reference), so this is not testable
        offline: the content test pins the choice and the agent-run self-test is the live check."""
        _, tools = self.allow_patterns()
        self.assertFalse([t for t in tools if t == "Skill" or t.startswith("Skill(")])
        for name in SKILL_NAMES:      # a skill that disables model invocation would need a human; none of the work-loop skills does
            self.assertIn("disable-model-invocation: false", read(SKILLS, name, "SKILL.md"), name)
        self.assertIn("No Skill entry", self.spec["context_management"]["headless"]["standard_cmd_revised"])

    def test_skillpaths_headless_command_keeps_the_project_directory(self):
        """SKILLPATHS: CLAUDE_BASH_MAINTAIN_PROJECT_WORKING_DIR=1 (code.claude.com/docs/en/tools-reference) is set by the
        standard command itself, so a cd by the agent does not break the repo-root-relative script calls."""
        std = self.spec["context_management"]["headless"]["standard_cmd"]
        self.assertTrue(std.startswith("AGENTIVE_HEADLESS=1 CLAUDE_BASH_MAINTAIN_PROJECT_WORKING_DIR=1 claude -p "), std)
        self.assertIn("CLAUDE_BASH_MAINTAIN_PROJECT_WORKING_DIR", self.spec["context_management"]["headless"]["env"])

    def test_v1_10_agents_md_names_the_codex_forms(self):
        ctx = read(SRC, "AGENTS.md.regions", "context.md")
        self.assertNotIn("@code_review.md", ctx)                                    # REVIEWRULES: no import, a pointer line instead
        self.assertIn("Review gate config: code_review.md", ctx)
        self.assertRegex(ctx, r"Codex: a `/<skill>` in this file and in skill text is `\$<skill>`")
        self.assertNotIn("Claude Code import", ctx)                                 # the import is gone, so is the Codex remark about it
        self.assertIn("Session start: follow the `NEXT=`", ctx)
        self.assertLessEqual(len([ln for ln in ctx.splitlines() if ln.strip()]), 20)

    def test_v1_15_readme_install_line_matches_design(self):
        readme = read(ROOT, "README.md")
        alt = self.spec["distribution"]["claude_code"]["one_command_alt"]
        cmd = alt["cmd"].replace("<owner/repo>", "xrpz/agentive")
        self.assertIn(f"`{cmd}`", readme)
        self.assertIn(f">= {self.spec['compat']['claude_code']['min']}", readme)
        self.assertIn(alt["min_version"], readme)
        self.assertNotIn("2.1.275", readme)

    def test_v2_24_bootstrap_check_prints_the_lock_hash(self):
        agents = read(ROOT, "AGENTS.md")
        m = re.search(r"run `(python3 -c '.*?')( \.agents/skills/agentive-init)`", agents)
        self.assertIsNotNone(m, "bootstrap check line missing")
        with open(os.path.join(ROOT, "plugins", "codex", "agentive", "skills.lock"), encoding="utf-8") as f:
            lock = json.load(f)["skills"]
        for name in ("agentive-init", "agentive-update"):
            d = os.path.join(ROOT, "plugins", "codex", "agentive", "skills", name)
            out = subprocess.run(["sh", "-c", m.group(1) + ' "$1"', "sh", d], capture_output=True, text=True, check=True).stdout.strip()
            self.assertEqual(out, lock[name]["sha256"], name)


if __name__ == "__main__":
    unittest.main()
