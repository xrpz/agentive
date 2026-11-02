# agentive-unit: selftest.component
# design: spec.spec_stage.chain.start_check, spec.spec_stage.chain.flow, decisions.accepted[OPENMARK]
"""stage-check.sh: one source installed into chain-intent, chain-spec and chain-plan; checks the previous artifact on
origin's default branch. Also the chain-spec open-question handoff command (B's handoff writer)."""
import os
import re
import shlex
import unittest

import yaml

from helpers import SKILLS, DepthSandbox, Sandbox, git, read, schema_errors, write


class StageCheck(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox({"README.md": "# demo\n", "docs/plans/done/intent.md": "# Intent\n",
                           "docs/plans/specced/intent.md": "# Intent\n", "docs/plans/specced/spec.md": "Intent: x\n"})

    def tearDown(self):
        self.sb.close()

    def check(self, stage, slug, env=None):
        p = self.sb.run(["sh", self.sb.script(f"chain-{stage}", "stage-check.sh"), slug], env=env)
        return p.returncode, p.stdout.strip().splitlines()[-1] if p.stdout.strip() else ""

    def test_intent_stage(self):
        self.assertEqual(self.check("intent", "new-thing"), (0, "AGENTIVE_CHAIN=ok:intent:new-thing DEFAULT=main"))
        rc, line = self.check("intent", "done")
        self.assertEqual(rc, 1)
        self.assertTrue(line.startswith("AGENTIVE_CHAIN=stop:intent:done NEXT="), line)
        self.assertIn("same change", line)

    def test_spec_stage_needs_intent_on_default_branch(self):
        rc, line = self.check("spec", "new-thing")
        self.assertEqual((rc, line), (1, "AGENTIVE_CHAIN=stop:spec:new-thing NEXT=merge the intent PR (intent/new-thing) first, then run /chain-spec new-thing"))
        self.assertEqual(self.check("spec", "done")[0], 0)

    def test_a_local_branch_does_not_count(self):
        git(self.sb.repo, "switch", "-q", "-c", "intent/local")
        write(self.sb.repo, "docs/plans/local/intent.md", "# Intent\n")
        git(self.sb.repo, "add", "docs")
        git(self.sb.repo, "commit", "-q", "-m", "intent")
        self.assertEqual(self.check("spec", "local")[0], 1)

    def test_plan_stage(self):
        rc, line = self.check("plan", "done")
        self.assertEqual((rc, line), (1, "AGENTIVE_CHAIN=stop:plan:done NEXT=merge the spec PR (spec/done) first, then run /chain-plan done"))
        self.assertEqual(self.check("plan", "specced")[0], 0)

    def test_plan_stage_needs_a_human(self):
        for env in ({"AGENTIVE_HEADLESS": "1"}, {"CI": "true"}, {"GITHUB_ACTIONS": "true"}):
            rc, line = self.check("plan", "specced", env)
            self.assertEqual(rc, 1)
            self.assertEqual(line, "AGENTIVE_CHAIN=stop:plan:specced NEXT=plan approval needs a human: run /chain-plan specced in an interactive session")

    def test_bad_slugs(self):
        for slug in ("archive", "Bad_Slug", "-x", ""):
            rc, line = self.check("intent", slug)
            self.assertEqual(rc, 1, slug)
            self.assertIn("AGENTIVE_CHAIN=stop:intent:", line)

    def test_ls_remote_fallback_without_origin_head(self):
        git(self.sb.repo, "remote", "set-head", "origin", "-d")
        self.assertEqual(self.check("spec", "done"), (0, "AGENTIVE_CHAIN=ok:spec:done DEFAULT=main"))

    def test_no_default_branch(self):
        git(self.sb.repo, "remote", "set-head", "origin", "-d")
        git(self.sb.repo, "remote", "set-url", "origin", self.sb.tmp + "/missing.git")
        rc, line = self.check("spec", "done")
        self.assertEqual((rc, line), (1, "AGENTIVE_CHAIN=stop:spec:done NEXT=check that origin is reachable (git ls-remote --symref origin HEAD), "
                                         "then rerun sh .agents/skills/chain-spec/scripts/stage-check.sh done"))

    def test_e2e_06_depth_clone_of_an_empty_origin_finds_the_intent(self):
        """BASEREF (E2E-06): a clone without a fetch refspec gets origin/<default> from the explicit-refspec fetch; before,
        the NEXT `git fetch origin main` updated only FETCH_HEAD and the stop came back."""
        sb = DepthSandbox({"README.md": "# demo\n", "docs/plans/later/intent.md": "# Intent\n"})
        self.addCleanup(sb.close)
        self.assertNotEqual(sb.run(["git", "rev-parse", "--verify", "--quiet", "refs/remotes/origin/main"]).returncode, 0)
        p = sb.run(["sh", sb.script("chain-spec", "stage-check.sh"), "later"])
        self.assertEqual((p.returncode, p.stdout.strip().splitlines()[-1]), (0, "AGENTIVE_CHAIN=ok:spec:later DEFAULT=main"), p.stdout)
        self.assertEqual(git(sb.repo, "symbolic-ref", "refs/remotes/origin/HEAD"), "refs/remotes/origin/main")

    def test_e2e_06_offline_with_a_local_ref_notes_on_stdout(self):
        git(self.sb.repo, "remote", "set-url", "origin", self.sb.tmp + "/missing.git")
        p = self.sb.run(["sh", self.sb.script("chain-spec", "stage-check.sh"), "done"])
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn("NOTE: git fetch of main failed; checking the local origin/main instead", p.stdout)

    def test_e2e_06_renamed_default_branch_is_detected_again(self):
        git(self.sb.origin, "branch", "-m", "main", "trunk")
        git(self.sb.origin, "symbolic-ref", "HEAD", "refs/heads/trunk")
        p = self.sb.run(["sh", self.sb.script("chain-spec", "stage-check.sh"), "done"])
        self.assertEqual((p.returncode, p.stdout.strip().splitlines()[-1]), (0, "AGENTIVE_CHAIN=ok:spec:done DEFAULT=trunk"), p.stdout)
        self.assertIn("NOTE: origin/HEAD=main remote HEAD=trunk", p.stdout)

    def test_merged_on_origin_is_seen_after_fetch(self):
        other = self.sb.tmp + "/other"
        git(self.sb.tmp, "clone", "-q", self.sb.origin, other)
        write(other, "docs/plans/later/intent.md", "# Intent\n")
        git(other, "add", "docs")
        git(other, "-c", "user.name=t", "-c", "user.email=t@e", "commit", "-q", "-m", "merge intent")
        git(other, "push", "-q", "origin", "main")
        self.assertEqual(self.check("spec", "later")[0], 0)


class OpenMarker(unittest.TestCase):
    """OPENMARK: /chain-plan stops while the default branch's spec.md has an `- [OPEN] <question>` bullet."""

    def setUp(self):
        spec = "Intent: docs/plans/{0}/intent.md\n\n## Open questions from the intent\n\n{1}\n"
        self.sb = Sandbox({
            "README.md": "# demo\n",
            "docs/plans/openq/spec.md": spec.format("openq", "- Which index? -> answered: btree\n- [OPEN] May the migration drop the legacy users table?"),
            "docs/plans/starred/spec.md": spec.format("starred", "* [OPEN]   Is the retention period 30 or 90 days?"),
            "docs/plans/answered/spec.md": spec.format("answered", "- May the migration drop the table? -> answered: no\n"
                                                      "A prose mention of the [OPEN] marker, and `- [OPEN] x` in a code span, are not bullets.\n"
                                                      "- [OPEN]\n"),
        })
        self.addCleanup(self.sb.close)

    def check(self, slug, env=None):
        p = self.sb.run(["sh", self.sb.script("chain-plan", "stage-check.sh"), slug], env=env or {"TERM": "dumb"})
        return p.returncode, p.stdout.strip().splitlines()[-1] if p.stdout.strip() else ""

    def test_plan_stops_while_an_open_bullet_is_on_the_default_branch(self):
        for slug in ("openq", "starred"):
            rc, line = self.check(slug)
            self.assertEqual((rc, line), (1, f"AGENTIVE_CHAIN=stop:plan:{slug} NEXT=answer the [OPEN] questions in docs/plans/{slug}/spec.md through a spec PR "
                                              f"(replace each [OPEN] with its answer), merge it, then run /chain-plan {slug}"))

    def test_answered_questions_and_prose_do_not_stop_the_plan(self):
        self.assertEqual(self.check("answered"), (0, "AGENTIVE_CHAIN=ok:plan:answered DEFAULT=main"))

    def test_a_local_spec_branch_with_the_marker_does_not_count(self):
        """Only the default branch's spec.md is read, like the existence check."""
        git(self.sb.repo, "switch", "-q", "-c", "spec/answered")
        write(self.sb.repo, "docs/plans/answered/spec.md", "Intent: x\n\n- [OPEN] a new local question\n")
        git(self.sb.repo, "commit", "-qam", "local question")
        self.assertEqual(self.check("answered")[0], 0)

    def test_shell_and_python_agree_on_what_a_bullet_is(self):
        import subprocess
        import sys
        sys.path.insert(0, os.path.join(SKILLS, "..", "lib"))
        from agentive import common
        pattern = re.search(r"grep -Eq '(\^\[\[:space:\]\]\*\[-\*\][^']*\\\[OPEN[^']*)'", read(SKILLS, "chain-stage-check.sh")).group(1)
        for text, expected in (("- [OPEN] a question", 1), ("* [OPEN]  a question", 1), ("  - [OPEN] indented", 1), ("- [OPEN]", 0),
                               ("[OPEN] no bullet", 0), ("prose [OPEN] here", 0), ("- [open] lower case", 0), ("- [OPEN]x", 1)):
            self.assertEqual(len(common.open_questions(text)), expected, text)
            shell = subprocess.run(["grep", "-Eq", pattern], input=text + "\n", text=True).returncode == 0
            self.assertEqual(shell, bool(expected), (pattern, text))


class SpecHandoff(unittest.TestCase):
    def test_open_question_handoff_command_in_chain_spec(self):
        """V1-03 / SKILLPATHS: the handoff is a skill script (covered by the headless allowlist `Bash(sh .agents/skills/*)`),
        called the way SKILL.md says, repo-root relative from the repository root (spec.skill_scripts.call)."""
        if not os.path.isfile(os.path.join(SKILLS, "..", "lib", "agentive", "handoff.py")):
            self.skipTest("B's handoff module is not there yet")
        skill = read(SKILLS, "chain-spec", "SKILL.md")
        self.assertNotIn("python3 -c", skill)
        cmd = re.search(r"`(sh \.agents/skills/chain-spec/scripts/handoff\.sh .*?)`", skill).group(1)
        cmd = cmd.replace('"<the question>"', shlex.quote("May the migration delete the old audit rows?")).replace('"<what the human must decide>"', shlex.quote("the data owner decides the retention period"))
        sb = Sandbox()
        try:
            p = sb.run(["sh", "-c", cmd])
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            line = p.stdout.strip().splitlines()[-1]
            self.assertTrue(line.startswith("HANDOFF="), line)
            text = read(sb.repo, line[len("HANDOFF="):])
            front = yaml.safe_load(text.split("---\n")[1])
            self.assertEqual(front["reason"], "open_question")
            self.assertEqual(schema_errors("handoff", front), [])
            self.assertFalse(os.path.isdir(os.path.join(sb.repo, ".agents", "lib", "agentive", "__pycache__")))   # V1-08
        finally:
            sb.close()


class SkillPathsBakedStage(unittest.TestCase):
    def test_skillpaths_stage_is_baked_into_each_copy(self):
        """SKILLPATHS (supersedes the V1-04 $0 detection): tools/generate.py writes the skill directory into each copy, so
        the stage is right from the repository root, through .claude/skills, from the skill root, and even for a copy
        moved elsewhere; the source holds the placeholder and no $0 logic."""
        src = read(SKILLS, "chain-stage-check.sh")
        self.assertIn("skill=%%agentive:gen.install_skill%%", src)
        self.assertNotIn('dirname -- "$0"', src)
        sb = Sandbox({"README.md": "# demo\n"})
        try:
            for st in ("intent", "spec", "plan"):
                self.assertIn(f"skill=chain-{st}\n", read(sb.script(f"chain-{st}", "stage-check.sh")))
            os.makedirs(os.path.join(sb.repo, ".claude", "skills"))
            os.symlink("../../.agents/skills/chain-intent", os.path.join(sb.repo, ".claude", "skills", "chain-intent"))
            moved = os.path.join(sb.tmp, "elsewhere.sh")
            with open(sb.script("chain-intent", "stage-check.sh"), encoding="utf-8") as f, open(moved, "w", encoding="utf-8") as g:
                g.write(f.read())
            for args in (["sh", ".agents/skills/chain-intent/scripts/stage-check.sh", "new-thing"],
                         ["sh", os.path.join(sb.repo, ".claude", "skills", "chain-intent", "scripts", "stage-check.sh"), "new-thing"],
                         ["sh", "-c", "cd .agents/skills/chain-intent && sh scripts/stage-check.sh new-thing"],
                         ["sh", moved, "new-thing"]):
                p = sb.run(args)
                self.assertEqual((p.returncode, p.stdout.strip().splitlines()[-1]), (0, "AGENTIVE_CHAIN=ok:intent:new-thing DEFAULT=main"), args)
            p = sb.run(["sh", ".agents/skills/chain-spec/scripts/stage-check.sh"])
            self.assertEqual(p.stdout.strip().splitlines()[-1],
                             "AGENTIVE_CHAIN=stop:spec:none NEXT=pass the slug: sh .agents/skills/chain-spec/scripts/stage-check.sh <slug>")
        finally:
            sb.close()


if __name__ == "__main__":
    unittest.main()
