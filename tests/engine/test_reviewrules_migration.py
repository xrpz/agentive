# agentive-unit: selftest.component
# design: decisions.accepted[REVIEWRULES], spec.init_apply.managed_regions, spec.init_apply.update, spec.repo_layer.files[f-agents-md]
"""REVIEWRULES: the installed AGENTS.md has no code_review.md import, and /agentive:update moves an install made by the previous
template over to the new one through the managed-region rewrite (no migration code)."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_engine import ENV, PAYLOAD, TODAY, Base  # noqa: E402  (the engine test fixtures: temporary repo, put/read, schema checks)

OLD_POINTER = ("Review gate config: code_review.md holds the machine rules of the CI review gate (security paths, confirm scope, tiers); "
               "open it only to change them. What a reviewer checks is under Code Review Rules below.\n")
OLD_CODEX = "Codex: a `/<skill>` in this file and in skill text is `$<skill>` (for example `$verify`, `$pr`).\n"
SECTION = ("\n## Code Review Rules\n\n"
           "- Report a change that leaves a descriptive doc (paths, commands, counts, behavior) wrong, and code that violates an accepted ADR, rule or spec "
           "(a human decides; neither side is changed in this PR).\n"
           "- Do not relitigate decisions tagged [user]; review decisions tagged [inferred].\n")
ALWAYS_CHECK = ("\nAlways check:\n"
                "- A descriptive doc (paths, commands, counts, behavior) that this change makes wrong and does not update in the same change.\n"
                "- Code that violates an accepted ADR, rule or spec: report it for a human to decide; neither side is changed in this PR.\n"
                "- Do not relitigate decisions tagged [user]; review decisions tagged [inferred].\n")
HEADER_NEW = "The block below is machine data for the review pipeline (what a reviewer checks is the `## Code Review Rules` section of AGENTS.md):"
HEADER_OLD = "The block below is machine data for the review pipeline:"


def patch(path, old, new):
    with open(path, encoding="utf-8") as f:
        text = f.read()
    assert old in text, (path, old[:60])
    with open(path, "w", encoding="utf-8") as f:
        f.write(text.replace(old, new, 1))


class InstalledText(Base):
    def install(self):
        self.git("init", "-q")
        self.put("README.md", "# demo\n")
        rc, out, _ = self.engine("init", "--no-managed-review-copy")
        self.assertEqual(rc, 0, out)

    def test_agents_md_has_a_pointer_and_the_review_rules_and_no_import(self):
        self.install()
        agents = self.read("AGENTS.md")
        self.assertNotIn("@code_review.md", agents)
        self.assertNotIn("Claude Code import", agents)
        self.assertIn(OLD_POINTER, agents)
        self.assertEqual(agents.count("\n## Code Review Rules\n"), 1)
        section = agents.split("\n## Code Review Rules\n", 1)[1].split("<!-- AGENTIVE END context -->")[0]
        for words in ("descriptive doc", "accepted ADR, rule or spec", "[user]", "[inferred]"):
            self.assertIn(words, section)
        # CLAUDE.md still reaches AGENTS.md with its one import and AGENTS.md imports nothing, so Claude loads two files, not three
        self.assertIn("@AGENTS.md", self.read("CLAUDE.md"))
        self.assertNotRegex(agents.replace("`$verify`", ""), r"(?m)^[^`\n]*(?<![\w`])@[\w./-]+\.\w+")

    def test_code_review_md_holds_the_machine_block_only(self):
        self.install()
        rules = self.read("code_review.md")
        self.assertNotIn("Always check", rules)
        self.assertNotIn("relitigate", rules)
        self.assertIn("```yaml agentive-review-rules", rules)
        self.assertIn("`## Code Review Rules` section of AGENTS.md", rules)
        self.assertTrue(rules.rstrip().endswith("<!-- AGENTIVE END rules -->"), rules[-120:])

    def test_the_reviewer_prompt_names_the_agents_md_section(self):
        self.install()
        prompt = self.read(".agents/agents/correctness-reviewer.md")
        self.assertIn("`## Code Review Rules` in AGENTS.md", prompt)
        self.assertNotIn("as the criteria file says", prompt)


class MigrationFromThePreviousTemplate(Base):
    """An install made by the template before REVIEWRULES (rebuilt from a copy of the payload with the two region templates put back)."""

    def setUp(self):
        super().setUp()
        self.old_payload = tempfile.mkdtemp(prefix="agentive-old-payload-")
        shutil.rmtree(self.old_payload)
        shutil.copytree(PAYLOAD, self.old_payload)
        self.addCleanup(shutil.rmtree, self.old_payload, True)
        ctx = os.path.join(self.old_payload, "repo-layer", "AGENTS.md.regions", "context.md")
        patch(ctx, OLD_POINTER + OLD_CODEX, "Review rules: @code_review.md\n"
                                            "Codex: a `/<skill>` in this file and in skill text is `$<skill>` (for example `$verify`, `$pr`); "
                                            "`@code_review.md` is a Claude Code import, so open code_review.md yourself.\n")
        patch(ctx, SECTION, "")
        rules = os.path.join(self.old_payload, "repo-layer", "code_review.md.regions", "rules.md")
        patch(rules, HEADER_NEW, HEADER_OLD)
        with open(rules, "a", encoding="utf-8") as f:
            f.write(ALWAYS_CHECK)

    def run_engine(self, payload, *args):
        cmd = [sys.executable, os.path.join(payload, "engine", "apply.py"), *args, "--repo", self.repo, "--today", TODAY]
        r = subprocess.run(cmd, capture_output=True, text=True, env=ENV)
        out = r.stdout
        changed = out.split("AGENTIVE_CHANGED:\n", 1)[1].split() if "AGENTIVE_CHANGED:\n" in out else []
        return r.returncode, out, changed

    def old_install(self):
        self.git("init", "-q")
        self.put("README.md", "# demo\n")
        rc, out, _ = self.run_engine(self.old_payload, "init", "--no-managed-review-copy")
        self.assertEqual(rc, 0, out)
        self.assertIn("Review rules: @code_review.md\n", self.read("AGENTS.md"))
        self.assertIn("Always check:", self.read("code_review.md"))
        self.commit("old agentive")

    def test_update_rewrites_both_regions_and_then_changes_nothing(self):
        self.old_install()
        rc, out, changed = self.run_engine(PAYLOAD, "update")
        self.assertEqual(rc, 0, out)
        self.assertIn("AGENTS.md", changed)
        self.assertIn("code_review.md", changed)
        agents, rules = self.read("AGENTS.md"), self.read("code_review.md")
        self.assertNotIn("@code_review.md", agents)
        self.assertIn(OLD_POINTER, agents)
        self.assertEqual(agents.count("## Code Review Rules"), 1)
        self.assertNotIn("Always check", rules)
        report = json.loads(self.read(".agents/state/drift-report.json"))
        self.assertEqual((report["items"], report.get("errors", [])), ([], []), "nothing the user edited, so no drift")
        self.assert_contracts()
        self.commit("update")
        rc, out, changed = self.run_engine(PAYLOAD, "update")
        self.assertEqual((rc, changed), (0, []), out)

    def test_a_user_edited_region_is_reported_not_overwritten_and_accept_moves_it(self):
        self.old_install()
        self.put("AGENTS.md", self.read("AGENTS.md").replace("Worktrees: untracked files", "Worktrees (my words): untracked files"))
        rc, out, changed = self.run_engine(PAYLOAD, "update")
        self.assertEqual(rc, 0, out)
        self.assertIn("AGENTIVE_DRIFT id=AGENTS.md#context reason=customized", out)
        self.assertIn("Review rules: @code_review.md", self.read("AGENTS.md"))     # kept: the user changed the region
        self.assertNotIn("Always check", self.read("code_review.md"))                # the untouched region moved on
        rc, out, changed = self.run_engine(PAYLOAD, "update", "--accept", "AGENTS.md#context")
        self.assertIn("AGENTS.md", changed)
        self.assertNotIn("@code_review.md", self.read("AGENTS.md"))
        self.assertIn("## Code Review Rules", self.read("AGENTS.md"))
        self.assertNotIn("my words", self.read("AGENTS.md"))


if __name__ == "__main__":
    unittest.main()
