# agentive-unit: selftest.sample_repo_tests
# design: spec.framework_repo.self_tests (sample_repo_tests, deterministic part), spec.init_apply.update, docs/impl/B.md §2 (engine CLI, --prove)
"""V1-21 / V1-19 / E2E-33 regressions: `init --prove` on the sample fixtures proves every verb with a command (the fixtures are green
on their own toolchain), and the first `update` after the proofs changes nothing. A stack whose tools are not installed on
this machine is skipped (the deterministic tests in test_init_apply.py need no toolchain)."""
import os
import shutil
import tempfile
import unittest

from test_init_apply import ENGINE, changed, copy_fixture, engine, git, PAYLOADS

TOOLS = {"ts": ["node", "npm", "tsc"], "go": ["go", "golangci-lint"], "py": ["uv"]}


class ProveTest(unittest.TestCase):
    def run_stack(self, stack):
        missing = [t for t in TOOLS[stack] if not shutil.which(t)]
        if missing:
            self.skipTest(f"{stack}: {', '.join(missing)} not installed")
        d = tempfile.mkdtemp(prefix=f"agentive-prove-{stack}-")
        self.addCleanup(shutil.rmtree, d, True)
        copy_fixture(stack, d)
        git(d, "init", "-q", "-b", "main")
        git(d, "add", "-A")
        git(d, "commit", "-q", "-m", "fixture")
        values = os.path.join(d, ".git", "agentive-values.json")
        with open(values, "w", encoding="utf-8") as f:
            f.write('{"tools": {}}')
        rc, out = engine(PAYLOADS["claude"], "init", "--repo", d, "--no-managed-review-copy", "--prove", "--values", values)
        self.assertEqual(rc, 0, out[-3000:])
        self.assertNotIn("AGENTIVE_PROOF_FAIL", out, out[-3000:])
        self.assertNotIn("AGENTIVE_UNPROVED", out.replace("AGENTIVE_UNPROVED verb=verify", ""), out[-3000:])
        rc, out = engine(PAYLOADS["claude"], "update", "--repo", d, "--values", values)
        self.assertEqual(rc, 0, out[-3000:])
        self.assertEqual(changed(out), [], out[-3000:])

    @unittest.skipUnless(os.path.isfile(ENGINE), "engine not built")
    def test_v1_21_ts_fixture_proves_green_and_v1_19_update_is_a_noop(self):
        self.run_stack("ts")

    @unittest.skipUnless(os.path.isfile(ENGINE), "engine not built")
    def test_v1_21_go_fixture_proves_green(self):
        self.run_stack("go")

    @unittest.skipUnless(os.path.isfile(ENGINE), "engine not built")
    def test_e2e_33_py_fixture_update_after_uv_build_is_a_noop(self):
        """E2E-33: the build proof (`uv build`) leaves an untracked dist/.gitignore with `*`; the first update changes nothing."""
        self.run_stack("py")


if __name__ == "__main__":
    unittest.main()
