# agentive-unit: selftest.component
# design: spec.plugin.invocation.repo_skills, spec.skill_scripts, spec.repo_checks.agentive_lint, spec.spec_stage.files, spec.repo_layer.files[f-precommit]
"""Repo skills written by C: frontmatter, invocation, Codex agents/openai.yaml, script references, templates, wording."""
import os
import re
import unittest

import yaml

from helpers import ROOT, SKILL_NAMES, SKILLS, SRC, design, read

FRONT = re.compile(r"\A---\n(.*?)\n---\n", re.S)
SCRIPT_REF = re.compile(r"(?:sh|bash|python3) (?:\.agents/skills/[a-z-]+/)?(scripts/[A-Za-z0-9_.-]+)")


def front(name):
    text = read(SKILLS, name, "SKILL.md")
    m = FRONT.match(text)
    assert m, f"{name}: SKILL.md must start with frontmatter"
    return text, m.group(1), yaml.safe_load(m.group(1))


class Skills(unittest.TestCase):
    def test_skill_set_is_the_designed_exception_list(self):
        listed = design()["spec"]["plugin"]["invocation"]["repo_skills"]
        for name in SKILL_NAMES:
            self.assertIn(name, listed)
        self.assertEqual(sorted(n for n in os.listdir(SKILLS) if os.path.isdir(os.path.join(SKILLS, n))), SKILL_NAMES)

    def test_frontmatter(self):
        for name in SKILL_NAMES:
            text, raw, fm = front(name)
            self.assertEqual(fm["name"], name)
            self.assertTrue(fm.get("description"), name)
            self.assertLessEqual(len(fm["description"]), 1024, name)
            self.assertIn("disable-model-invocation: false", raw, f"{name}: invocation must be written explicitly")
            self.assertIs(fm["disable-model-invocation"], False)
            self.assertNotIn("allowed-tools", fm, name)
            self.assertNotIn("model", fm, name)
            self.assertLessEqual(len(text.splitlines()), 500, name)

    def test_argument_hints(self):
        hints = {n: front(n)[2].get("argument-hint") for n in SKILL_NAMES}
        for n in ("chain-intent", "chain-spec", "chain-plan"):
            self.assertEqual(hints[n], "<slug>")
        self.assertEqual(hints["pr"], "[--explain]")
        self.assertEqual(hints["pr-feedback"], "[<pr number>]")
        self.assertEqual(hints["distill"], "[<harvest-queue path>]")

    def test_precommit_review_runs_forked(self):
        fm = front("precommit-review")[2]
        want = design()["spec"]["repo_layer"]["files"]
        want = next(f for f in want if f["id"] == "f-precommit")["frontmatter"]
        for k, v in want.items():
            self.assertEqual(fm[k], v, k)

    def test_codex_openai_yaml(self):
        for name in SKILL_NAMES:
            raw = read(SKILLS, name, "agents", "openai.yaml")
            self.assertIn(f"# agentive-unit: rl.skill.{name}", raw.splitlines()[0])
            self.assertIn("allow_implicit_invocation: true", raw)
            doc = yaml.safe_load(raw)
            self.assertIs(doc["policy"]["allow_implicit_invocation"], True)
            self.assertEqual(doc["interface"]["display_name"], name)

    def test_referenced_scripts_exist_and_no_claude_only_variables(self):
        for name in SKILL_NAMES:
            text = read(SKILLS, name, "SKILL.md")
            self.assertNotIn("CLAUDE_SKILL_DIR", text)
            self.assertNotIn("${CLAUDE_PLUGIN_ROOT}", text)
            for rel in SCRIPT_REF.findall(text):
                if rel == "scripts/stage-check.sh":
                    self.assertTrue(name.startswith("chain-"))
                    continue
                self.assertTrue(os.path.isfile(os.path.join(SKILLS, name, rel)), f"{name}: {rel}")

    def test_scripts_carry_headers_and_are_executable(self):
        for d, _, files in os.walk(SKILLS):
            for f in files:
                p = os.path.join(d, f)
                if f.endswith((".sh", ".py")):
                    head = read(p).splitlines()[:3]
                    self.assertTrue(head[0].startswith("#!"), p)
                    self.assertTrue(any("agentive-unit: rl.skill." in h for h in head), p)
                    self.assertTrue(os.access(p, os.X_OK), p)


class Templates(unittest.TestCase):
    def headings(self, text):
        return [ln for ln in text.splitlines() if ln.startswith("## ")]

    def test_intent(self):
        t = read(SKILLS, "chain-intent", "templates", "intent.md")
        self.assertIn("Author: <name>. Status: draft | accepted.", t)
        self.assertEqual(self.headings(t), ["## Problem", "## Proposed outcome", "## Affected users and systems",
                                            "## Constraints", "## Open questions"])

    def test_spec(self):
        t = read(SKILLS, "chain-spec", "templates", "spec.md")
        self.assertEqual(t.splitlines()[0], "Intent: docs/plans/<slug>/intent.md")
        self.assertEqual(self.headings(t)[:5], ["## Requirements", "## Design", "## How each constraint in the intent is met",
                                                "## Open questions from the intent", "## Areas of concern"])
        self.assertRegex(t, r"- R1 .*Verification:")

    def test_plan(self):
        t = read(SKILLS, "chain-plan", "templates", "plan.md")
        for h in ("## Risks", "## Decisions", "## Open questions"):
            self.assertIn(h, self.headings(t))
        self.assertIn("(no R: <reason>)", t)
        self.assertIn("[inferred]", t)


class Wording(unittest.TestCase):
    def test_no_forbidden_terms(self):
        roots = [SKILLS, os.path.join(SRC, "AGENTS.md.regions"), os.path.join(SRC, "CLAUDE.md.regions"),
                 os.path.join(SRC, "code_review.md.regions"), os.path.join(SRC, "dot-agents", "rules"), os.path.join(SRC, "docs")]
        files = [os.path.join(SRC, "dot-agents", n) for n in ("decisions.yaml", "rubric.yaml")]
        files += [os.path.join(SRC, "dot-agents", "lib", "agentive", n) for n in ("prbody.py", "prstate.py", "distillq.py", "gitgh.py")]
        for r in roots:
            for d, _, fs in os.walk(r):
                files += [os.path.join(d, f) for f in fs]
        for p in files:
            text = read(p).lower()
            for bad in ("\u0075nattended", "\ubb34\uc778"):  # banned words, escaped so they never appear in the repo
                self.assertNotIn(bad, text, p)

    def test_no_gh_pr_commands(self):
        for name in SKILL_NAMES:
            text = read(SKILLS, name, "SKILL.md")
            self.assertIsNone(re.search(r"`gh pr ", text), name)


if __name__ == "__main__":
    unittest.main()
