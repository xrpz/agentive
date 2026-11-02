# agentive-unit: selftest.component
# design: decisions.accepted[SELFTESTFAST], decisions.accepted[SIZEBUDGET], spec.framework_repo.self_tests
"""tools/selftest.py: the fast subset before a commit, the full set for CI and releases, per-step elapsed time."""
import os
import re
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import selftest  # noqa: E402

ALL = list(selftest.STEPS)
CHEAP = ["generate_check", "schema_validation", "size_budget", "version_sync_check"]


def run(*args):
    r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "selftest.py"), *args], capture_output=True, text=True, cwd=ROOT)
    return r.returncode, r.stdout + r.stderr


class Plan(unittest.TestCase):
    def test_a_change_to_the_templates_the_engine_or_the_design_runs_everything(self):
        for path in ("src/repo-layer/AGENTS.md.regions/context.md", "src/repo-layer/dot-agents/lib/agentive/review/rv1.py",
                     "src/engine/apply.py", "src/plugin/claude-code/skills/init/SKILL.md", "src/units/content.yaml",
                     "design/framework.yaml", "design/schemas/manifest.schema.json"):
            steps, areas, why = selftest.plan(["tools/design.py", path])
            self.assertEqual((steps, areas), (ALL, None), path)
            self.assertTrue(why.startswith("full:") and path in why, why)

    def test_an_unknown_path_runs_everything(self):
        for path in ("scripts/new_tool.sh", "requirements-dev.txt", "tests/loose_file.py", "tests/nosucharea/test_x.py"):
            steps, areas, why = selftest.plan([path])
            self.assertEqual((steps, areas), (ALL, None), path)
            self.assertTrue(why.startswith("full:"), why)

    def test_git_that_cannot_list_files_runs_everything(self):
        self.assertEqual(selftest.plan(None)[:2], (ALL, None))

    def test_a_test_file_runs_its_own_area(self):
        steps, areas, why = selftest.plan(["tests/review/test_findings.py"])
        self.assertEqual((steps, areas), (["generate_check", "schema_validation", "size_budget", "component_tests", "version_sync_check"], ["review"]))
        self.assertTrue(why.startswith("fast:"), why)

    def test_tool_and_doc_changes_run_the_generator_area(self):
        for path in ("tools/design.py", "tools/selftest.py", "docs/IMPLEMENTATION.md", "docs/impl/decisions-log.md", "framework.yaml"):
            steps, areas, _ = selftest.plan([path])
            self.assertEqual(areas, ["generator"], path)
            self.assertIn("component_tests", steps)

    def test_fixtures_and_sample_repo_tests_run_the_fixture_step_only(self):
        for path in ("fixtures/py/pyproject.toml", "tests/sample_repo/test_prove.py"):
            steps, areas, _ = selftest.plan([path])
            self.assertEqual((steps, areas), (["generate_check", "schema_validation", "size_budget", "sample_repo_tests", "version_sync_check"], []), path)

    def test_generated_outputs_and_version_files_add_nothing(self):
        steps, areas, why = selftest.plan(["plugins/claude-code/agentive/payload/index.json", "docs/map/index.html", "docs/DECISIONS.md",
                                           "design/INDEX.md", ".changeset/x.md", "package.json", "CHANGELOG.md"])
        self.assertEqual((steps, areas), (CHEAP, []))
        self.assertTrue(why.startswith("fast:"), why)

    def test_no_change_still_runs_the_cheap_steps(self):
        self.assertEqual(selftest.plan([])[:2], (CHEAP, []))

    def test_areas_come_from_the_tests_directory(self):
        self.assertEqual(selftest.component_areas(), sorted(selftest.component_areas()))
        for area in ("generator", "engine", "review", "content", "hooks", "ci", "plugin", "lib"):
            self.assertIn(area, selftest.component_areas())
        for own_step in ("sample_repo", "design", "budget"):
            self.assertNotIn(own_step, selftest.component_areas())


class Steps(unittest.TestCase):
    def test_ci_runs_every_step_of_the_full_set(self):
        """CI and a release run the full set: each step of STEPS has a line in the selftest job of ci.yml, and no other step does."""
        with open(os.path.join(ROOT, ".github", "workflows", "ci.yml"), encoding="utf-8") as f:
            ci = f.read()
        in_ci = re.findall(r"run: python3 tools/selftest\.py (\w+)\s*$", ci, re.M)
        self.assertEqual(in_ci, ALL)

    def test_an_unknown_step_or_option_is_refused(self):
        rc, out = run("no_such_step")
        self.assertEqual(rc, 2, out)
        self.assertIn("unknown step", out)
        rc, out = run("--fast", "generate_check")
        self.assertEqual(rc, 2, out)
        rc, out = run("--nope")
        self.assertEqual(rc, 2, out)

    def test_every_step_line_carries_its_elapsed_time(self):
        rc, out = run("version_sync_check")
        self.assertEqual(rc, 0, out)
        self.assertRegex(out, r"(?m)^SELFTEST version_sync_check ok \(\d+\.\ds\)$")
        self.assertRegex(out, r"(?m)^SELFTEST: ok \(\d+\.\ds\)$")

    def test_plan_prints_the_plan_and_runs_nothing(self):
        rc, out = run("--plan")
        self.assertEqual(rc, 0, out)
        self.assertEqual(len(out.strip().splitlines()), 1, out)
        self.assertRegex(out, r"^SELFTEST plan: (fast|full): ")

    def test_component_tests_refuse_a_misspelled_area(self):
        ok, msg = selftest.step_component_tests(["generater"])
        self.assertFalse(ok)
        self.assertIn("no such test area", msg)

    def test_areas_run_side_by_side_and_are_reported_in_sorted_order_with_their_time(self):
        """The areas are separate processes at the same time; the message order does not depend on which finished first."""
        import threading
        import time
        from unittest import mock
        started, lock = [], threading.Lock()
        both_running = threading.Barrier(2, timeout=20)

        def fake_discover(start, top=None):
            area = os.path.basename(start)
            with lock:
                started.append(area)
            both_running.wait()                      # returns only when a second area is running at the same time
            time.sleep(0.3 if area == "ci" else 0.0)  # "ci" finishes last although it is listed first
            return 0, "Ran 3 tests\n\nOK"

        with mock.patch.object(selftest, "_discover", fake_discover):
            ok, msg = selftest.step_component_tests(["lib", "ci"], jobs=2)
        self.assertTrue(ok, msg)
        lines = msg.splitlines()
        self.assertEqual([ln.split(":")[0] for ln in lines], ["tests/ci", "tests/lib"])
        for ln in lines:
            self.assertRegex(ln, r": OK \(\d+\.\ds\)$")

    def test_a_failing_area_fails_the_step_and_shows_its_output_while_the_others_still_report(self):
        from unittest import mock

        def fake_discover(start, top=None):
            return (1, "boom 1\nboom 2") if start.endswith("lib") else (0, "OK")

        with mock.patch.object(selftest, "_discover", fake_discover):
            ok, msg = selftest.step_component_tests(["ci", "lib"], jobs=2)
        self.assertFalse(ok)
        self.assertRegex(msg, r"(?m)^tests/ci: OK \(\d+\.\ds\)$")
        self.assertRegex(msg, r"(?m)^tests/lib \(\d+\.\ds\):$")
        self.assertIn("boom 2", msg)


if __name__ == "__main__":
    unittest.main()
