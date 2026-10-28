# agentive-unit: selftest.component
# design: contracts[decisions], contracts[rubric], spec.init_apply.evidence_only.u6, spec.review_pipeline.stages[RV7b].rubric
""".agents/decisions.yaml (U1=C, U2=B, U6=agentive:rubric@1) and .agents/rubric.yaml (preset agentive:rubric@1)."""
import os
import sys
import unittest

import yaml

from helpers import LIB, SRC, design, read, schema_errors

sys.path.insert(0, LIB)
from agentive import ayaml  # noqa: E402


class Decisions(unittest.TestCase):
    def setUp(self):
        self.raw = read(SRC, "dot-agents", "decisions.yaml")
        self.doc = yaml.safe_load(self.raw)

    def test_schema_and_runtime_parser(self):
        self.assertEqual(schema_errors("decisions", self.doc), [])
        self.assertEqual(ayaml.loads(self.raw), self.doc)

    def test_values(self):
        wl = design()["spec"]["work_loop"]
        u1 = wl["merge"]["auto_merge_default"]
        self.assertEqual((self.doc["U1"]["value"], self.doc["U1"]["conditions"]), (u1["value"], u1["conditions"]))
        u2 = wl["precommit_review"]["final_confirmation"]
        self.assertEqual(self.doc["U2"]["value"], u2["value"])
        self.assertEqual(self.doc["U2"]["confirm_scope"], u2["asks_human_when"])
        self.assertEqual(self.doc["U2"]["non_interactive"], u2["non_interactive"]["value"])
        self.assertEqual(self.doc["U6"]["value"], wl["merge"]["numbers"]["value"])
        for k in ("U1", "U2", "U6"):
            self.assertEqual(self.doc[k]["decided_by"], "user")


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
        for d in ("system", "features", "guides"):
            self.assertTrue(os.path.isfile(os.path.join(SRC, "docs", d, "dot-gitkeep")))


if __name__ == "__main__":
    unittest.main()
