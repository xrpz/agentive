# agentive-unit: selftest.component
# design: spec.plugin.invocation.repo_skills, spec.skill_scripts, spec.repo_checks.agentive_lint, spec.spec_stage.files, spec.repo_layer.files[f-precommit], decisions.accepted[REQSTATED]
"""Repo skills written by C: frontmatter, invocation, Codex agents/openai.yaml, script references, templates, wording."""
import json
import os
import re
import unittest

import yaml

from helpers import ROOT, SKILL_NAMES, SKILLS, SRC, design, read

FRONT = re.compile(r"\A---\n(.*?)\n---\n", re.S)
SCRIPT_REF = re.compile(r"(?:sh|bash|python3) (?:\.agents/skills/[a-z-]+/)?(scripts/[A-Za-z0-9_.-]+)")
CALL = re.compile(r"\b(?:sh|bash|python3) ((?:\.agents/skills/[a-z-]+/)?scripts/[A-Za-z0-9_.-]+)")


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

    def test_distill_is_not_started_from_the_harvest_line(self):
        """V1-16: the description must not tell the agent to run distill by itself because the queue has entries."""
        text, raw, fm = front("distill")
        desc = fm["description"]
        self.assertNotIn("Use when the harvest queue has entries", desc)
        self.assertIn("never to run it unasked", desc)
        self.assertIn("HARVEST=<n>", desc)

    def test_e2e_17_precommit_review_runs_inline(self):
        """E2E-17 (PRECOMMITINLINE): no context: fork and no background, so the implementer's conversation starts the reviewer
        subagent one level deep whatever the nesting depth; the body pins the orchestrator role, the foreground subagent, the
        fixed prompt and the self fallback with a reason that does not stop."""
        text, raw, fm = front("precommit-review")
        want = design()["spec"]["repo_layer"]["files"]
        want = next(f for f in want if f["id"] == "f-precommit")["frontmatter"]
        self.assertEqual({k: fm[k] for k in fm if k not in ("name", "description")}, want)
        self.assertNotIn("context", fm)
        self.assertNotIn("background", fm)
        for words in ("You orchestrate; you never review the change yourself.", "`run_in_background: false`",
                      "`Read <input> and do the task it describes. Reply with the JSON object only.`", "Add no context, intent, summary or argument of yours.",
                      '{"executed_by": "self", "reason": "no_agent_tool"}', '`"spawn_failed"`', "Do not stop", "`NOTE independence=self`",
                      "never from inside another subagent"):
            self.assertIn(words, text)

    def test_e2e_18_chain_plan_needs_the_plan_mode_approval(self):
        """E2E-18 (PLANMODEGATE): step 1 handles the guard's stop line (enter plan mode or stop); approval is only the plan-mode
        prompt's accept choice; without ExitPlanMode the skill prints the stop line; plan.md is never written through Bash. The
        stage-check keeps its environment rule (Codex, runs without the hook)."""
        text = read(SKILLS, "chain-plan", "SKILL.md")
        for words in ("when the command is denied with an `AGENTIVE_CHAIN=stop:...` reason", "the EnterPlanMode tool",
                      "Approval is only the accept choice of the plan-mode prompt", "never approval",
                      "If ExitPlanMode is not available", "Never write plan.md through Bash.", "the guard hook lets step 1 pass only in plan mode"):
            self.assertIn(words, text)
        sc = read(SKILLS, "chain-stage-check.sh")
        self.assertIn('if [ "${AGENTIVE_HEADLESS:-}" = 1 ] || [ -n "${CI:-}" ] || [ -n "${GITHUB_ACTIONS:-}" ]; then', sc)
        self.assertIn("PLANMODEGATE", sc)

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

    def test_skillpaths_scripts_are_called_repo_root_relative(self):
        """SKILLPATHS: every script call in SKILL.md and in script NEXT lines is `sh .agents/skills/<own skill>/scripts/<x>`,
        and every SKILL.md says its commands run from the repository root."""
        for name in SKILL_NAMES:
            texts = [read(SKILLS, name, "SKILL.md")]
            sd = os.path.join(SKILLS, name, "scripts")
            texts += [read(sd, f) for f in (os.listdir(sd) if os.path.isdir(sd) else [])]
            if name.startswith("chain-"):
                texts.append(read(SKILLS, "chain-stage-check.sh").replace("$skill", name))
            for t in texts:
                for ref in CALL.findall(t):
                    self.assertTrue(ref.startswith(f".agents/skills/{name}/scripts/"), f"{name}: {ref}")
            self.assertIn("Run every command below from the repository root", texts[0], name)

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
        self.assertEqual(self.headings(t)[:5], ["## Requirements", "## Design", "## How each requirement and constraint in the intent is met",
                                                "## Open questions from the intent", "## Areas of concern"])
        self.assertRegex(t, r"- R1 .*Verification:")

    def test_plan(self):
        t = read(SKILLS, "chain-plan", "templates", "plan.md")
        for h in ("## Risks", "## Decisions", "## Open questions"):
            self.assertIn(h, self.headings(t))
        self.assertIn("(no R: <reason>)", t)
        self.assertIn("[inferred]", t)


class ChainRequirementText(unittest.TestCase):
    """REQSTATED: every stated requirement is one bullet in the user's terms (chain-intent), every requirement bullet and constraint is
    mapped to an R<n>, a design item or `(not adopted: <reason>)` (chain-spec), and the spec-PR reviewer flags an unmapped bullet."""

    def test_intent_skill_and_template_keep_every_stated_requirement(self):
        skill = read(SKILLS, "chain-intent", "SKILL.md")
        step5 = next(ln for ln in skill.splitlines() if ln.startswith("5. "))
        for phrase in ("Every requirement the user stated is one bullet under `## Proposed outcome`", "in the user's own terms",
                       "never merge two requirements into one bullet", "never drop one", "do not ask again what was already said"):
            self.assertIn(phrase, step5)
        self.assertIn("one bullet per requirement the user stated", read(SKILLS, "chain-intent", "templates", "intent.md"))

    def test_spec_skill_and_template_map_every_requirement_bullet(self):
        skill = read(SKILLS, "chain-spec", "SKILL.md")
        line = next(ln for ln in skill.splitlines() if "requirement bullet" in ln)
        self.assertIn("`## Proposed outcome`", line)
        self.assertIn("an R<n>, a design item, or `(not adopted: <reason>)`", line)
        tpl = read(SKILLS, "chain-spec", "templates", "spec.md")
        self.assertIn("- <requirement bullet or constraint from intent.md> -> R<n> | <design item> | (not adopted: <reason>)", tpl)
        self.assertNotIn("How each constraint in the intent is met", tpl + skill)

    def test_spec_pr_reviewer_flags_an_unmapped_bullet(self):
        text = " ".join(read(SRC, "dot-agents", "agents", "correctness-reviewer.md").split())
        self.assertIn("every requirement bullet of `## Proposed outcome`, every constraint and every open question of the intent is mapped or answered", text)
        self.assertIn("is an important finding", text.split("(not adopted: <reason>)")[1][:80 + len("is an important finding")])

    def test_design_names_the_decision(self):
        files = design()["spec"]["spec_stage"]["files"]
        self.assertIn("(not adopted: <reason>)", files["spec"])
        self.assertIn("REQSTATED", files["intent"])
        self.assertIn("no deterministic R<n> lint", files["spec"])           # the review check stays a review check


class Wording(unittest.TestCase):
    def test_no_forbidden_terms(self):
        roots = [SKILLS, os.path.join(SRC, "AGENTS.md.regions"), os.path.join(SRC, "CLAUDE.md.regions"),
                 os.path.join(SRC, "code_review.md.regions"), os.path.join(SRC, "dot-agents", "rules"), os.path.join(SRC, "docs")]
        files = [os.path.join(SRC, "dot-agents", n) for n in ("rubric.yaml",)]
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

    GRAPHQL = re.compile(r"\bgh (?:pr|issue) [a-z]|gh api graphql|\bgraphql\b", re.I)

    def _installed_texts(self):
        for base in (SRC, os.path.join(ROOT, "src", "engine")):
            for d, dirs, fs in os.walk(base):
                dirs[:] = [x for x in dirs if x != "__pycache__"]
                for f in fs:
                    p = os.path.join(d, f)
                    try:
                        yield p, read(p)
                    except UnicodeDecodeError:
                        continue

    def test_e2e_15_no_graphql_in_next_lines_or_instruction_templates(self):
        """E2E-15 (RESTONLY): GitHub is read and written through gh api (REST); gh pr / gh issue use GraphQL, which the proxy of
        cloud sessions and Routines runs rejects. No NEXT line of any installed script or the engine, and no AGENTS.md, CLAUDE.md
        or code_review.md template, tells an agent to run them. The only GraphQL instruction is /pr-feedback's resolve step
        (with its reply-only fallback); the CI gate's `gh pr merge --auto` runs on the GitHub runner, not through that proxy."""
        for p, text in self._installed_texts():
            for ln in text.splitlines():
                if "NEXT" in ln or "AGENTIVE_REPORT" in ln or "AGENTIVE_NEXT" in ln:
                    self.assertIsNone(self.GRAPHQL.search(ln), f"{p}: {ln.strip()[:160]}")
        for region in ("AGENTS.md.regions", "CLAUDE.md.regions", "code_review.md.regions"):
            for f in os.listdir(os.path.join(SRC, region)):
                self.assertIsNone(self.GRAPHQL.search(read(SRC, region, f)), f"{region}/{f}")
        for name in SKILL_NAMES:
            hits = [ln for ln in read(SKILLS, name, "SKILL.md").splitlines() if "gh api graphql" in ln]
            self.assertEqual(len(hits), 1 if name == "pr-feedback" else 0, name)
            if hits:
                self.assertIn("only reply and report \"resolve pending\"", hits[0])

    def test_e2e_15_gh_pr_is_called_only_for_auto_merge(self):
        """E2E-15: the one gh pr call in code is enable_auto_merge in the CI gate job (no REST endpoint, S36)."""
        calls = []
        for p, text in self._installed_texts():
            if p.endswith(".py") or "/bin/" in p:
                calls += [(p, m.group(0)) for m in re.finditer(r"\[\s*\"(?:pr|issue)\"\s*,\s*\"[a-z]+\"", text)]
        self.assertEqual([c[1] for c in calls], ['["pr", "merge"'], calls)
        self.assertTrue(calls[0][0].endswith(os.path.join("review", "github.py")))


class SkillGuidance(unittest.TestCase):
    """SKILLGUIDE (2026-11-01): fragile prose commands are skill scripts, no time-dependent sentence in the init skills, sources in the design."""

    def test_fragile_commands_are_scripts_the_skill_calls(self):
        pf = read(SKILLS, "pr-feedback", "SKILL.md")
        self.assertIn("sh .agents/skills/pr-feedback/scripts/pr-reply.sh <n> <comment id>", pf)
        self.assertNotIn("-f body=", pf)
        self.assertNotIn("/replies", pf)
        jan = read(SKILLS, "janitor", "SKILL.md")
        self.assertIn("sh .agents/skills/janitor/scripts/label-pr.sh <id> <pr number>", jan)      # the repair named by a failed publish (E2E-40)
        self.assertIn("--label agentive-janitor:<id>", jan)
        self.assertNotIn("labels[]", jan)
        self.assertNotIn("/labels", jan)
        for skill, script in (("pr-feedback", "pr-reply.sh"), ("janitor", "label-pr.sh")):
            p = os.path.join(SKILLS, skill, "scripts", script)
            self.assertTrue(os.access(p, os.X_OK), p)
            text = read(p)
            self.assertTrue(text.startswith("#!/bin/sh\n"), p)       # POSIX sh: the same script runs for Claude Code and Codex
            self.assertNotIn("CLAUDE_SKILL_DIR", text)
            self.assertNotIn("graphql", text.lower())

    def test_init_skills_name_the_fact_without_a_date(self):
        for rel in (("src", "plugin", "claude-code", "skills", "init", "SKILL.md"),
                    ("src", "plugin", "codex", "skills", "agentive-init", "SKILL.md")):
            text = " ".join(read(ROOT, *rel).split())
            self.assertIn("managed Code Review (Team/Enterprise; generally available)?", text, rel[-2])
            self.assertNotRegex(text, r"generally available since", rel[-2])
            self.assertNotRegex(text, r"since 20\d\d-\d\d-\d\d", rel[-2])
        self.assertNotIn("generally available since", json.dumps(design()["spec"]))

    def test_skill_guidance_sources_are_design_sources(self):
        srcs = {x["id"]: x for x in design()["sources"]}
        want = {"S228": "https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices",
                "S229": "https://agentskills.io/specification",
                "S230": "https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-fable-5"}
        for sid, url in want.items():
            self.assertEqual(srcs[sid]["url"], url)
            self.assertEqual(srcs[sid]["fetched_at"], "2026-11-01")
        rules = design()["spec"]["skill_scripts"]
        self.assertEqual(rules["src"], ["S228", "S229", "S230"])
        self.assertIn("authoring_rules", rules)
        ids = [d["id"] for d in design()["decisions"]["accepted"]]
        self.assertIn("SKILLGUIDE", ids)


if __name__ == "__main__":
    unittest.main()
