# agentive-unit: selftest.component
# design: decisions.accepted[DEPHUMAN], decisions.accepted[LOCKFILEDEPS], spec.review_pipeline.stages[RV1], spec.review_pipeline.stages[RV2], spec.review_pipeline.stages[RV3], spec.review_pipeline.stages[RV8], docs/impl/decisions-log.md §20
"""E2E-20 (DEPHUMAN, user 2026-10-31): a PR that changes a dependency list or a lockfile goes to a human (tier 2); a package file
edit that leaves the dependency entries alone ([tool.ruff], package.json scripts) is no dependency change; dependency review is
information only (its missing result never decides the tier)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers as h  # noqa: E402
from test_secpaths import FakeDiff, block, load  # noqa: E402
import test_stages as ts  # noqa: E402
from test_regressions import eval_report  # noqa: E402

from agentive.review import post, rv1, rv2, rv3, rv4  # noqa: E402

PY = """[project]
name = "shop"
version = "0.1.0"
dependencies = [
  "httpx[http2]>=0.27",  # the client ]
]

[dependency-groups]
dev = [{include-group = "lint"}, "pytest>=8"]
lint = ["ruff"]

[tool.ruff]
line-length = 100
select = ["E", "F"]
"""
PKG = '{"name": "web", "scripts": {"test": "jest"}, "dependencies": {"react": "^18.3.1"}}\n'
PR = {"author_association": "OWNER", "is_fork": False, "labels": []}
TIERS = {"tier0_classes": ["docs", "format", "lockfile"], "tier0_max_changed_lines": 200, "large_change_lines": 1000,
         "tier2_classes": ["security"]}


class E2E20DependencyChangeTest(unittest.TestCase):
    R = load(block()).value

    def changes(self, path, base, head, st="M"):
        diff = FakeDiff(head={path: head}, base={path: base})
        return diff, [(st, path, None)], rv1.dependency_changes(diff, [(st, path, None)])

    def gate(self, path, base, head, checks=None, pr=PR, st="M"):
        diff, statuses, dc = self.changes(path, base, head, st)
        sig = {"env_vars_changed": [], "agentive_check_tags": {}, "dependency_changes": dc}
        got, always = rv2.run(diff, statuses, {}, self.R, {"verbs": {}}, sig, pr=pr, checks=checks)
        decided, _ = rv3.classify_rules(diff, statuses, {}, self.R, got["hits"], dep_files=dc["package_files"])
        rv3_out = rv3.finish(decided, [], False)
        tier, reasons = rv4.rules_tier(got, always, {"human_confirmation": "not_required"}, rv3_out, 10, TIERS)
        return dc, always, rv3_out["classes"], tier, reasons

    def test_e2e_20_ruff_only_pyproject_edit_is_not_a_dependency_change(self):
        head = PY.replace('select = ["E", "F"]', 'select = ["E", "F", "B"]').replace("line-length = 100", "line-length = 120")
        dc, always, classes, tier, reasons = self.gate("pyproject.toml", PY, head)
        self.assertFalse(rv1.is_dependency_change(dc), dc)
        self.assertEqual(dc["package_files"], [])
        self.assertEqual(classes, ["config"])                       # was deps (rubric change_type 14) before DEPHUMAN
        self.assertNotIn("dependency_change", [c for c, _ in always])
        self.assertEqual(tier, 1)
        # package.json scripts only: config, no dependency change
        dc, always, classes, tier, _ = self.gate("package.json", PKG, PKG.replace('"jest"', '"jest --ci"'))
        self.assertEqual((rv1.is_dependency_change(dc), classes, always), (False, ["config"], []))

    def test_e2e_20_extend_select_is_not_a_dependency(self):
        head = PY.replace('select = ["E", "F"]', 'select = ["E", "F"]\nextend-select = ["B", "UP"]')
        _, _, dc = self.changes("pyproject.toml", PY, head)
        self.assertEqual(dc["added"], [])                           # the old reader made pypi:b and pypi:up new dependencies
        self.assertEqual(set(rv1._deps_pyproject(head)), {"pypi:httpx", "pypi:pytest", "pypi:ruff"})
        self.assertNotIn("pypi:lint", rv1._deps_pyproject(head))     # {include-group = "lint"} names a group, not a package

    def test_e2e_20_lockfile_only_change_goes_to_a_human(self):
        for checks in (None, [{"name": "dependency-review", "status": "pass"}]):
            dc, always, classes, tier, reasons = self.gate("uv.lock", "version = 1\n", "version = 1\n# httpx 0.28.1\n", checks=checks)
            self.assertEqual(dc["lockfiles"], ["uv.lock"])
            self.assertEqual(classes, ["lockfile"])
            self.assertEqual(tier, 2, checks)                       # even with lockfile in tier0_classes and a dependency-review pass
            self.assertIn("dependency_change", reasons)
            line = [x for x in reasons if x.startswith("dependency_change: ")][0]
            self.assertIn("lockfile uv.lock", line)
            self.assertIn("never auto-merged", line)
        # a pure rename of a lockfile with the same content is no change
        diff = FakeDiff(head={"b/uv.lock": "x"}, base={"a/uv.lock": "x"})
        self.assertEqual(rv1.dependency_changes(diff, [("R", "b/uv.lock", "a/uv.lock")])["lockfiles"], [])

    def test_e2e_20_new_dependency_goes_to_a_human(self):
        head = PY.replace('  "httpx[http2]>=0.27",  # the client ]\n', '  "httpx[http2]>=0.27",  # the client ]\n  "left_pad==1.0",\n')
        dc, always, classes, tier, reasons = self.gate("pyproject.toml", PY, head)
        self.assertEqual(dc["added"], ["pypi:left-pad"])
        self.assertEqual((classes, tier), (["deps"], 2))
        detail = dict(always)["dependency_change"]
        self.assertTrue(detail.startswith("human review needed: dependency change (added pypi:left-pad), never auto-merged"), detail)
        # a changed spec and a removal are dependency changes too; an unreadable package.json fails closed
        dc, _, _, tier, _ = self.gate("pyproject.toml", PY, PY.replace(">=0.27", ">=0.28"))
        self.assertEqual(([c["name"] for c in dc["changed"]], tier), (["pypi:httpx"], 2))
        dc, _, _, tier, _ = self.gate("package.json", PKG, PKG.replace(', "dependencies": {"react": "^18.3.1"}', ""))
        self.assertEqual((dc["removed"], tier), (["npm:react"], 2))
        dc, always, classes, tier, _ = self.gate("package.json", PKG, PKG.replace("}\n", ",\n"))
        self.assertEqual((dc["unreadable"], classes, tier), (["package.json"], ["deps"], 2))
        self.assertIn("could not read package.json", dict(always)["dependency_change"])
        # RV8: human_required with the one-line cause; the summary comment shows it once
        r = ts.GateTest.decide(self, tier=2, rv4_reasons=reasons, explainer=({"section": True}, []))
        self.assertEqual(r["decision"], "human_required")
        self.assertIn("dependency_change", r["reason_codes"])
        self.assertEqual([x for x in post.reason_lines(r["reasons"]) if "dependency change" in x],
                         [dict(self.gate("pyproject.toml", PY, head)[1])["dependency_change"]])
        self.assertIn("official package", rv1.DEPENDENCY_NEXT)

    def test_e2e_20_dependency_review_unavailable_is_no_longer_the_deciding_reason(self):
        # no dependency-review result and no dependency change: nothing goes to a human for it (was critical_cve_unchecked)
        head = PY.replace("line-length = 100", "line-length = 120")
        for checks in (None, [], [{"name": "dependency-review", "status": "pass"}]):
            dc, always, _, tier, reasons = self.gate("pyproject.toml", PY, head, checks=checks)
            self.assertEqual(always, [], checks)
            self.assertNotIn("critical_cve_unchecked", reasons)
            self.assertEqual(tier, 1)
        # with a dependency change the reason is dependency_change and the review state is only information
        head = PY.replace(">=0.27", ">=0.28")
        _, always, _, _, reasons = self.gate("pyproject.toml", PY, head, checks=None)
        self.assertEqual([c for c, _ in always], ["dependency_change"])
        self.assertIn("dependency review did not run for this commit (information only)", dict(always)["dependency_change"])
        _, always, _, _, _ = self.gate("pyproject.toml", PY, head, checks=[{"name": "dependency-review", "status": "pass"}])
        self.assertIn("dependency review ran and found no critical CVE (information only)", dict(always)["dependency_change"])
        # a reported critical CVE keeps its own line; without a PR (precommit, reviewer eval) no review state is named
        _, always, _, _, _ = self.gate("pyproject.toml", PY, head, checks=[{"name": "dependency-review", "status": "fail"}])
        self.assertEqual([c for c, _ in always], ["always_human", "dependency_change"])
        _, always, _, _, _ = self.gate("pyproject.toml", PY, head, pr=None)
        self.assertNotIn("dependency review", dict(always)["dependency_change"])

    def test_e2e_20_parsers_read_only_dependency_entries(self):
        self.assertEqual(rv1._deps_package_json("{bad"), None)
        self.assertEqual(set(rv1._deps_package_json('{"dependencies": {"a": "1"}, "overrides": {"b": "2"}, "scripts": {"x": "y"}}')),
                         {"npm:a", "npm-override:b"})
        self.assertEqual(set(rv1._deps_requirements("flask==3 # web\n-r base.txt\n--index-url https://x/simple\n./local\n")),
                         {"pypi:flask", "pypi-option:-r base.txt", "pypi-option:--index-url https://x/simple", "pypi-url:./local"})
        go = rv1._deps_go_mod("module m\nrequire (\n\tgithub.com/a/b v1.2.3\n\tgolang.org/x/sys v0.1.0 // indirect\n)\nreplace example.com/c => ../c\n")
        self.assertEqual(go, {"go:github.com/a/b": "v1.2.3", "go:golang.org/x/sys": "v0.1.0 indirect", "go-replace:example.com/c": "../c"})
        cargo = rv1._deps_cargo('[package]\nname = "x"\n[dependencies]\nserde = { version = "1",\n  features = ["derive"] }\n'
                                '[target.\'cfg(unix)\'.dependencies]\nnix = "0.27"\n[dependencies.tokio]\nversion = "1"\n[patch.crates-io]\nfoo = { git = "u" }\n')
        self.assertEqual(set(cargo), {"cargo:serde", "cargo:nix", "cargo:tokio", "cargo-patch:foo"})
        poetry = rv1._deps_pyproject('[tool.poetry.dependencies]\npython = "^3.11"\nrequests = "^2"\n[tool.poetry.group.dev.dependencies]\npytest = "*"\n'
                                     '[tool.poetry.scripts]\nserve = "app:main"\n')
        self.assertEqual(set(poetry), {"pypi:requests", "pypi:pytest"})
        # go indirect requires are not new direct dependencies
        diff = FakeDiff(head={"go.mod": "module m\nrequire (\n\tgithub.com/a/b v1\n\tgolang.org/x/y v0.1.0 // indirect\n)\n"}, base={"go.mod": "module m\n"})
        dc = rv1.dependency_changes(diff, [("M", "go.mod", None)])
        self.assertEqual((dc["added"], dc.pop("_direct_added")), (["go:github.com/a/b", "go:golang.org/x/y"], ["go:github.com/a/b"]))


class E2E20PipelineTest(unittest.TestCase):
    """The same rule through the installed review command (reviewer eval profile: deterministic stages, no LLM tasks)."""

    def test_e2e_20_pipeline_tool_config_vs_dependency_and_lockfile(self):
        repo = h.Repo({"pyproject.toml": PY, "uv.lock": "version = 1\n", "src/app.py": "x = 1\n"})
        try:
            base = repo.commit("base")
            repo.write({"pyproject.toml": PY.replace("line-length = 100", "line-length = 120")})
            rep = eval_report(repo, base, repo.commit("ruff"), work="e1")
            self.assertEqual(rep["rv3"]["classes"], ["config"])
            self.assertNotIn("dependency_change", rep["rv4"]["reasons"])
            self.assertEqual(rep["rv1"]["signals"]["dependency_changes"]["package_files"], [])
            self.assertEqual(rep["rv1"]["signals"]["new_direct_dependencies"], [])
            repo.write({"uv.lock": "version = 1\n[[package]]\nname = \"httpx\"\n"})
            rep = eval_report(repo, base, repo.commit("lock"), work="e2")
            self.assertEqual(rep["rv4"]["tier"], 2)
            self.assertIn("dependency_change", rep["rv4"]["reasons"])
            self.assertEqual(rep["rv1"]["signals"]["dependency_changes"]["lockfiles"], ["uv.lock"])
            self.assertEqual(h.schema_validator("review-report")(rep), [])
        finally:
            repo.cleanup()


if __name__ == "__main__":
    unittest.main()
