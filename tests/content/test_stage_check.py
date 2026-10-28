# agentive-unit: selftest.component
# design: spec.spec_stage.chain.start_check, spec.spec_stage.chain.flow
"""stage-check.sh: one source installed into chain-intent, chain-spec and chain-plan; checks the previous artifact on
origin's default branch. Also the chain-spec open-question handoff command (B's handoff writer)."""
import os
import re
import shlex
import unittest

import yaml

from helpers import SKILLS, Sandbox, git, read, schema_errors, write


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
        self.assertEqual((rc, line), (1, "AGENTIVE_CHAIN=stop:spec:done NEXT=set origin/HEAD (git remote set-head origin --auto)"))

    def test_merged_on_origin_is_seen_after_fetch(self):
        other = self.sb.tmp + "/other"
        git(self.sb.tmp, "clone", "-q", self.sb.origin, other)
        write(other, "docs/plans/later/intent.md", "# Intent\n")
        git(other, "add", "docs")
        git(other, "-c", "user.name=t", "-c", "user.email=t@e", "commit", "-q", "-m", "merge intent")
        git(other, "push", "-q", "origin", "main")
        self.assertEqual(self.check("spec", "later")[0], 0)


class SpecHandoff(unittest.TestCase):
    def test_open_question_handoff_command_in_chain_spec(self):
        if not os.path.isfile(os.path.join(SKILLS, "..", "lib", "agentive", "handoff.py")):
            self.skipTest("B's handoff module is not there yet")
        cmd = re.search(r"`(PYTHONPATH=\.agents/lib python3 -c .*?)`", read(SKILLS, "chain-spec", "SKILL.md")).group(1)
        cmd = cmd.replace('"<the question>"', shlex.quote("Delete old rows?")).replace('"<what the human must decide>"', shlex.quote("decide retention"))
        sb = Sandbox()
        try:
            p = sb.run(["sh", "-c", cmd])
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            text = read(sb.repo, p.stdout.strip())
            front = yaml.safe_load(text.split("---\n")[1])
            self.assertEqual(front["reason"], "open_question")
            self.assertEqual(schema_errors("handoff", front), [])
        finally:
            sb.close()


if __name__ == "__main__":
    unittest.main()
