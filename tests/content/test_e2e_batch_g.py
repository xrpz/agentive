# agentive-unit: selftest.component
# design: decisions.accepted[HEADLESSHINT], spec.guard.push_without_refspec, spec.repo_layer.files[f-agents-md], docs/impl/decisions-log.md §23
"""E2E-32 and E2E-36 (decision e2e_32_36, 2026-11-01) in the repo-layer text: the AGENTS.md headless line, the chain skills that
no longer ask for `$?`, branches created with --no-track and pushes as `git push -u origin HEAD`, the standard command with
--settings in every payload copy."""
import os
import re
import unittest

from helpers import PAYLOAD_SKILLS, SKILLS, SKILL_NAMES, SRC, design, read


class E2E32Text(unittest.TestCase):
    def test_e2e_32_agents_md_names_the_headless_bash_rules(self):
        ctx = read(SRC, "AGENTS.md.regions", "context.md")
        line = next(ln for ln in ctx.splitlines() if ln.startswith("Headless runs (`claude -p`)"))
        for words in ("one allowlisted command per Bash call", "no `; echo $?`", "no `cd`", "no redirect or heredoc into a file",
                      "no `sed -i`", "Write and Edit", "`.agents/bin/verb`", "refuses that one command only"):
            self.assertIn(words, line)
        self.assertLessEqual(len([ln for ln in ctx.splitlines() if ln.strip()]), 20)

    def test_e2e_32_skills_never_ask_for_the_exit_code(self):
        for name in SKILL_NAMES:
            text = read(SKILLS, name, "SKILL.md")
            self.assertNotIn("On exit 1 print", text, name)
            self.assertNotIn("EXIT=$?", text, name)
        for name in ("chain-intent", "chain-spec", "chain-plan"):
            self.assertIn("stage-check.sh <slug>` alone in its Bash call", read(SKILLS, name, "SKILL.md"), name)

    def test_e2e_32_payload_skills_carry_the_standard_command_with_settings(self):
        std = design()["spec"]["context_management"]["headless"]["standard_cmd"]
        self.assertIn("--settings '{\"sandbox\":{\"autoAllowBashIfSandboxed\":false}}'", std)
        for skill in ("janitor", "pr-feedback", "distill"):
            self.assertIn(std, read(PAYLOAD_SKILLS, skill, "SKILL.md"), skill)


class E2E36Text(unittest.TestCase):
    def test_e2e_36_branches_are_created_without_an_upstream(self):
        seen = 0
        for name in SKILL_NAMES:
            for m in re.finditer(r"`git switch -c [^`]*origin/[^`]*`", read(SKILLS, name, "SKILL.md")):
                seen += 1
                self.assertIn("--no-track", m.group(0), name)
        self.assertEqual(seen, 5)                         # chain-spec, chain-plan, the janitor and chain-intent twice (A-9: AGENTS.md allows only this form); the second is the resume of a branch only the remote has

    def test_e2e_36_pushes_name_the_branch(self):
        for name in SKILL_NAMES:
            text = read(SKILLS, name, "SKILL.md")
            for m in re.finditer(r"`git push[^`]*`", text):
                self.assertTrue(m.group(0).startswith("`git push -u origin HEAD"), f"{name}: {m.group(0)}")
        self.assertIn("`git push -u origin HEAD` once", read(SKILLS, "pr-feedback", "SKILL.md"))


class E2E59ToolFlagText(unittest.TestCase):
    def test_e2e_59_no_repo_skill_or_script_text_tells_an_agent_to_pass_tool(self):
        for name in SKILL_NAMES:
            text = read(SKILLS, name, "SKILL.md")
            self.assertNotIn("--tool", text, name)
        self.assertIn("agentive-review precommit` (no flag", read(SKILLS, "precommit-review", "SKILL.md"))
        self.assertIn("pr-body.sh draft --model <the model you run as>`", read(SKILLS, "pr", "SKILL.md"))
        # the AGENTS.md / CLAUDE.md templates of the installed layer name no flag either
        for rel in ("AGENTS.md.regions", "CLAUDE.md.regions"):
            base = os.path.join(SRC, rel)
            for f in sorted(os.listdir(base)):
                self.assertNotIn("--tool", read(SRC, rel, f), f"{rel}/{f}")


if __name__ == "__main__":
    unittest.main()
