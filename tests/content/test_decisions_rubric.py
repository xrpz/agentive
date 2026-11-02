# agentive-unit: selftest.component
# design: contracts[rubric], decisions.accepted[DEFAULTS], spec.init_apply.evidence_only.u6, spec.review_pipeline.stages[RV7b].rubric
"""Framework defaults (U1=C as review.auto_merge low_risk, U2=B, U6=agentive:rubric@1; no decisions.yaml) and .agents/rubric.yaml."""
import os
import sys
import unittest

import yaml

from helpers import LIB, ROOT, SRC, design, read, schema_errors

sys.path.insert(0, LIB)
from agentive import ayaml  # noqa: E402


class Defaults(unittest.TestCase):
    """DEFAULTS (V1-18): no .agents/decisions.yaml is installed; U1 / U2 / U6 live in framework code and the rubric preset."""

    def test_no_decisions_file_is_installed(self):
        self.assertFalse(os.path.exists(os.path.join(SRC, "dot-agents", "decisions.yaml")))
        self.assertFalse(os.path.exists(os.path.join(ROOT, "design", "schemas", "decisions.schema.json")))

    def test_auto_merge_default_matches_the_design(self):
        sys.path.insert(0, LIB)
        from agentive.review import config
        u1 = design()["spec"]["work_loop"]["merge"]["auto_merge_default"]
        self.assertEqual(config.AUTO_MERGE_DEFAULT, u1["default_setting"])
        self.assertEqual(list(config.AUTO_MERGE_VALUES), u1["values"])
        self.assertEqual(u1["key"], "review.auto_merge")
        self.assertEqual(u1["value"], "C")

    def test_u2_constant_matches_the_design(self):
        from agentive.review import config
        u2 = design()["spec"]["work_loop"]["precommit_review"]["final_confirmation"]
        self.assertEqual(u2["value"], "B")
        self.assertEqual(sorted(u2["asks_human_when"]), sorted(config.U2_CATEGORIES))
        self.assertEqual(u2["non_interactive"]["value"], "pr_only_tier2")

    def test_u6_is_the_rubric_preset_name(self):
        from agentive.review import config
        self.assertEqual(design()["spec"]["work_loop"]["merge"]["numbers"]["value"], config.RUBRIC_PRESET_ID)
        self.assertEqual(yaml.safe_load(read(SRC, "dot-agents", "rubric.yaml"))["preset"], config.RUBRIC_PRESET_ID)


class Rubric(unittest.TestCase):
    def setUp(self):
        self.raw = read(SRC, "dot-agents", "rubric.yaml")
        self.doc = yaml.safe_load(self.raw)

    def test_schema_and_runtime_parser(self):
        self.assertEqual(schema_errors("rubric", self.doc), [])
        self.assertEqual(ayaml.loads(self.raw), self.doc)

    def test_matches_design_preset(self):
        rv7b = next(s for s in design()["spec"]["review_pipeline"]["stages"] if s["id"] == "RV7b")["rubric"]
        self.assertEqual(self.doc["preset"], rv7b["preset"])
        self.assertEqual(self.doc["human_if_score_gt"], rv7b["human_if_score_gt"])
        self.assertEqual([(i["id"], i["weight"], i["hit"]) for i in self.doc["items"]],
                         [(i["id"], i["weight"], i["hit"]) for i in rv7b["items"]])
        self.assertEqual(self.doc["shared_globs"], ["**/shared/**", "**/common/**"])

    def test_override_example_is_valid(self):
        self.assertEqual(schema_errors("rubric", {"extends": "agentive:rubric@1", "items": [{"id": "size", "enabled": False}]}), [])


class AdrTemplate(unittest.TestCase):
    def test_status_field_and_dirs(self):
        t = read(SRC, "docs", "adr", "0000-template.md")
        self.assertRegex(t, r"(?m)^Status: proposed \| accepted")
        for d in ("system", "features", "guides"):    # NOMAP: no empty docs folders are pre-created
            self.assertFalse(os.path.exists(os.path.join(SRC, "docs", d)), d)


if __name__ == "__main__":
    unittest.main()
