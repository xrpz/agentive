# agentive-unit: selftest.component
# design: spec.repo_layer.files[f-agents-md].must_contain, spec.review_pipeline.stages[RV2], contracts[review-rules], spec.init_apply.managed_regions.regions
"""Shared-file regions written by C: AGENTS.md, CLAUDE.md, code_review.md, .gitignore, and the REVIEW.md copy rule."""
import os
import re
import sys
import unittest

import yaml

from helpers import LIB, ROOT, SRC, design, read, schema_errors

sys.path.insert(0, LIB)
from agentive import ayaml  # noqa: E402

# render.* keys published by B (docs/impl/B.md §1) plus the ones C requested there (docs/impl/C.md)
KNOWN_KEYS = {"render.gitignore_stack", "render.orientation", "render.verbs", "render.path_rules",
              "render.code_review_copy", "render.doc_pointers", "render.rules_managed", "render.rules_content"}
PLACEHOLDER = re.compile(r"%%agentive:([A-Za-z0-9_.<>\[\]-]+)%%")


def rules_doc():
    text = re.sub(r"(?m)^[ \t]*%%agentive:render\.rules_\w+%%\n", "", read(SRC, "code_review.md.regions", "rules.md"))   # engine placeholders (SECPATHS)
    m = re.search(r"^```yaml agentive-review-rules\n(.*?)^```$", text, re.S | re.M)
    assert m, "no ```yaml agentive-review-rules block"
    return m.group(1)


class ReviewRulesBlock(unittest.TestCase):
    def setUp(self):
        self.raw = rules_doc()
        self.doc = yaml.safe_load(self.raw)
        self.rv = {s["id"]: s for s in design()["spec"]["review_pipeline"]["stages"]}

    def test_schema_valid(self):
        self.assertEqual(schema_errors("review-rules", self.doc), [])

    def test_runtime_parser_agrees(self):
        self.assertEqual(ayaml.loads(self.raw), self.doc)

    def test_design_defaults_present(self):
        tb = self.doc["agentive-review-rules"]["trust_boundary"]
        have = [(g["glob"], g["reason"]) for g in tb["never_ai_approve"] if "glob" in g]
        for g in self.rv["RV2"]["default_globs"]:
            self.assertIn((g["glob"], g["reason"]), have)
        self.assertIn(("**/billing/**", "billing"), have)
        self.assertIn(("**/payment*/**", "billing"), have)
        for cat, globs in self.rv["RV2"]["confirm_scope"]["default_globs"].items():
            for g in globs:
                self.assertIn(g, tb["confirm_scope_globs"][cat], cat)

    def test_installed_hook_scripts_are_confirm_scope(self):
        hooks = self.doc["agentive-review-rules"]["trust_boundary"]["confirm_scope_globs"]["hooks"]
        self.assertIn(".agents/hooks/**", hooks)

    def test_tiers_match_rv4(self):
        t = self.doc["agentive-review-rules"]["tiers"]
        d = self.rv["RV4"]["defaults"]
        self.assertEqual(t["tier0_classes"], d["tier0_classes"])
        self.assertEqual(t["tier0_max_changed_lines"], d["tier0_max_changed_lines"])
        self.assertEqual(t["large_change_lines"], d["large_change_lines"])
        self.assertEqual(t["tier2_classes"], d["tier2_classes"])
        self.assertEqual(t["confirm_scope_without_human_tier"], 2)

    def test_findings_added_lines(self):
        """REVIEWRULES: the human review rules are the `## Code Review Rules` section of AGENTS.md (Codex GitHub review reads that
        heading; Claude Code reads AGENTS.md through CLAUDE.md), and code_review.md holds the machine block only."""
        ctx = read(SRC, "AGENTS.md.regions", "context.md")
        section = ctx.split("\n## Code Review Rules\n", 1)[1]
        self.assertIn("descriptive doc", section)
        self.assertIn("accepted ADR, rule or spec", section)
        self.assertIn("[user]", section)
        self.assertIn("[inferred]", section)
        self.assertEqual(ctx.count("## Code Review Rules"), 1)
        text = read(SRC, "code_review.md.regions", "rules.md")
        self.assertNotIn("Always check", text)
        self.assertNotIn("descriptive doc", text)
        self.assertNotIn("relitigate", text)
        self.assertIn("`## Code Review Rules` section of AGENTS.md", text)


class AgentsMd(unittest.TestCase):
    def setUp(self):
        self.ctx = read(SRC, "AGENTS.md.regions", "context.md")
        self.files = {f["id"]: f for f in design()["spec"]["repo_layer"]["files"]}

    def test_triage_line_is_the_designed_text(self):
        must = self.files["f-agents-md"]["must_contain"]
        triage = next(m for m in must if m.startswith("the chain-entry triage line"))
        exact = re.search(r"`(Chain entry \(intent\.md needed\?\):.*?)`", triage).group(1)
        self.assertIn(exact, self.ctx)

    def test_chain_pointer_and_review_rules(self):
        self.assertIn("Chain tasks: before working on docs/plans/<slug>/, read intent.md, spec.md and plan.md there", self.ctx)
        # REVIEWRULES (2026-11-02): no @-import of code_review.md; a pointer line says where the machine rules are and when to open them
        self.assertNotIn("@code_review.md", self.ctx)
        self.assertIn("Review gate config: code_review.md holds the machine rules of the CI review gate", self.ctx)
        self.assertIn("open it only to change them", self.ctx)
        self.assertIn("/run-skill-generator", self.ctx)
        self.assertIn("Docs vs code:", self.ctx)
        # NOBOUNDARYLINE (2026-10-29): no generated boundary line and no service facts lines
        self.assertNotIn("render.boundary_line", self.ctx)
        self.assertNotIn("render.service_facts", self.ctx)
        self.assertNotIn("Module boundaries", self.ctx)

    def test_harvest_line_proposes_distill_once_and_never_runs_it_unasked(self):
        """V1-16: one short line where the agent reads it."""
        lines = [ln for ln in self.ctx.splitlines() if ln.startswith("Harvest:")]
        self.assertEqual(len(lines), 1)
        for must in ("HARVEST=<n>", "/distill", "$distill", "once at the end of the task", "only after the human agrees"):
            self.assertIn(must, lines[0])
        self.assertLessEqual(len(lines[0]), 260)
    def test_short(self):
        self.assertLessEqual(len(self.ctx.splitlines()), 30)

    def test_path_rules_heading(self):
        pr = read(SRC, "AGENTS.md.regions", "path-rules.md")
        self.assertTrue(pr.startswith("## Path rules (generated)\n"))
        # RULESNEUTRAL: agentive-lint rules --write rebuilds this region from agentive.rulesync, so both must agree
        import sys
        sys.path.insert(0, os.path.join(SRC, "dot-agents", "lib"))
        from agentive import rulesync
        self.assertEqual(pr, rulesync.HEADING + "\n\n%%agentive:render.path_rules%%\n")

    def test_placeholders_are_known(self):
        for rel in ("AGENTS.md.regions/context.md", "AGENTS.md.regions/path-rules.md", "CLAUDE.md.regions/imports.md",
                    "code_review.md.regions/rules.md", "dot-gitignore.regions/state.txt", "REVIEW.md", "dot-agents/rules/structure.md"):
            for key in PLACEHOLDER.findall(read(SRC, rel)):
                self.assertIn(key, KNOWN_KEYS, rel)


class OtherRegions(unittest.TestCase):
    def test_claude_md_imports(self):
        lines = read(SRC, "CLAUDE.md.regions", "imports.md").splitlines()
        self.assertEqual(lines[0], "@AGENTS.md")
        self.assertIn("# Compact instructions", lines)

    def test_gitignore_state(self):
        lines = [x for x in read(SRC, "dot-gitignore.regions", "state.txt").splitlines() if x.strip()]
        # E2E-48: the last line is the stack placeholder (patterns of the stack found by evidence); the rest is the designed list
        self.assertEqual(lines[-1], "%%agentive:render.gitignore_stack%%")
        self.assertEqual(lines[:-1], design()["spec"]["repo_layer"]["gitignore_state"])

    def test_review_copy_is_only_the_copy_placeholder(self):
        self.assertEqual(read(SRC, "REVIEW.md"), "%%agentive:render.code_review_copy%%")

    def test_units_declare_designed_regions(self):
        with open(os.path.join(ROOT, "src", "units", "content.yaml"), encoding="utf-8") as f:
            units = {u["id"]: u for u in yaml.safe_load(f)["units"]}
        known = {r["id"] for r in design()["spec"]["init_apply"]["managed_regions"]["regions"]}
        self.assertEqual({f"AGENTS.md#{r}" for r in units["rl.agents-md"]["regions"]} | {"CLAUDE.md#imports", "code_review.md#rules", ".gitignore#state"},
                         known)
        self.assertEqual(units["rl.review-copy"]["when"], "review.managed_code_review_copy")


if __name__ == "__main__":
    unittest.main()
