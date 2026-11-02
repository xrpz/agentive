# agentive-unit: selftest.component
# design: spec.work_loop.ci_ai_review_floor.setup_steps, decisions.accepted[CISETUP], docs/impl/B.md (render.ci_setup_steps)
"""render.ci_setup_steps (src/engine/cisetup.py) per ecosystem, and the `before:` / `after:` placement of render.merge
that lets .agents/custom/ insert a step by the id of a generated step (UV-24)."""
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PAYLOAD = os.path.join(ROOT, "plugins", "claude-code", "agentive", "payload")
sys.path.insert(0, os.path.join(PAYLOAD, "engine"))
sys.path.insert(0, os.path.join(PAYLOAD, "repo-layer", "dot-agents", "lib"))

import cisetup  # noqa: E402
import render  # noqa: E402

from agentive import ayaml  # noqa: E402


def fixture(name):
    return os.path.join(ROOT, "fixtures", name)


class CiSetupTest(unittest.TestCase):
    def tmp(self, files):
        d = tempfile.mkdtemp(prefix="agentive-cisetup-")
        self.addCleanup(shutil.rmtree, d, True)
        for rel, text in files.items():
            os.makedirs(os.path.dirname(os.path.join(d, rel)) or d, exist_ok=True)
            with open(os.path.join(d, rel), "w", encoding="utf-8") as f:
                f.write(text)
        return d

    def steps(self, root, eco):
        """The rendered block parsed as a YAML list (comments dropped)."""
        return ayaml.loads(cisetup.render(root, eco))

    def test_every_step_has_a_stable_toolchain_id(self):
        for fx, eco in (("ts", ["node"]), ("py", ["python"]), ("go", ["go"]), ("java", ["java"])):
            steps = self.steps(fixture(fx), eco)
            self.assertTrue(steps, fx)
            for s in steps:
                self.assertTrue(s["id"].startswith("toolchain-"), s)

    def test_node_uses_the_version_file_the_repo_has(self):
        d = self.tmp({"package.json": '{"name": "x", "engines": {"node": ">=22"}}', "package-lock.json": "{}"})
        s = self.steps(d, ["node"])
        self.assertEqual([x["id"] for x in s], ["toolchain-node"])
        self.assertEqual((s[0]["uses"], s[0]["with"]), ("actions/setup-node@v7", {"node-version-file": "package.json"}))
        d = self.tmp({"package.json": "{}", ".nvmrc": "22\n"})
        self.assertEqual(self.steps(d, ["node"])[0]["with"], {"node-version-file": ".nvmrc"})
        d = self.tmp({"package.json": "{}", ".node-version": "22\n", ".nvmrc": "20\n"})
        self.assertEqual(self.steps(d, ["node"])[0]["with"], {"node-version-file": ".nvmrc"})

    def test_node_without_a_version_says_what_to_set(self):
        text = cisetup.render(fixture("ts"), ["node"])
        self.assertIn("set node-version", text)
        self.assertEqual(self.steps(fixture("ts"), ["node"])[0].get("with"), None)

    def test_pnpm_and_yarn_and_bun(self):
        d = self.tmp({"package.json": '{"packageManager": "pnpm@10.4.0"}', "pnpm-lock.yaml": "", ".nvmrc": "22"})
        s = self.steps(d, ["node"])
        self.assertEqual([x["id"] for x in s], ["toolchain-pnpm", "toolchain-node"])           # pnpm before node: setup-node caches by it
        self.assertEqual(s[0]["uses"], "pnpm/action-setup@v6")
        self.assertNotIn("with", s[0])                                                          # version comes from packageManager
        d = self.tmp({"package.json": "{}", "pnpm-lock.yaml": ""})
        s = self.steps(d, ["node"])
        self.assertEqual(str(s[0]["with"]["version"]), "10")
        self.assertIn("packageManager", cisetup.render(d, ["node"]))
        d = self.tmp({"package.json": "{}", "yarn.lock": ""})
        s = self.steps(d, ["node"])
        self.assertEqual([x["id"] for x in s], ["toolchain-node", "toolchain-corepack"])
        self.assertEqual(s[1]["run"], "corepack enable")
        d = self.tmp({"package.json": "{}", "bun.lock": ""})
        self.assertEqual(self.steps(d, ["node"])[0]["uses"], "oven-sh/setup-bun@v2")

    def test_python_and_uv(self):
        s = self.steps(fixture("py"), ["python"])
        self.assertEqual([x["id"] for x in s], ["toolchain-python", "toolchain-uv"])           # uv is not preinstalled on the runner
        self.assertEqual(s[0]["uses"], "actions/setup-python@v7")
        self.assertEqual(s[0]["with"], {"python-version-file": "pyproject.toml"})
        self.assertEqual(s[1]["uses"], "astral-sh/setup-uv@v7")
        d = self.tmp({"pyproject.toml": "[project]\nname='x'\n", ".python-version": "3.13\n"})
        s = self.steps(d, ["python"])
        self.assertEqual([x["id"] for x in s], ["toolchain-python"])
        self.assertEqual(s[0]["with"], {"python-version-file": ".python-version"})
        d = self.tmp({"pyproject.toml": "[project]\nname='x'\n"})                               # no requires-python: the file is not offered
        self.assertNotIn("python-version-file", cisetup.render(d, ["python"]))

    def test_go(self):
        s = self.steps(fixture("go"), ["go"])
        self.assertEqual([(x["id"], x["uses"], x["with"]) for x in s], [("toolchain-go", "actions/setup-go@v7", {"go-version-file": "go.mod"})])

    def test_java(self):
        s = self.steps(fixture("java"), ["java"])
        self.assertEqual((s[0]["id"], s[0]["uses"]), ("toolchain-java", "actions/setup-java@v6"))
        self.assertEqual(s[0]["with"]["distribution"], "temurin")
        self.assertTrue(s[0]["with"]["java-version"])
        d = self.tmp({"build.gradle.kts": "plugins { java }\n", ".java-version": "21\n"})
        self.assertEqual(self.steps(d, ["java"])[0]["with"], {"distribution": "temurin", "java-version-file": ".java-version"})
        d = self.tmp({"pom.xml": "<project/>"})
        self.assertEqual(self.steps(d, ["java"])[0]["with"]["java-version"], "21")             # default when nothing is declared

    def test_runner_image_ecosystems_get_a_comment_only(self):
        text = cisetup.render(self.tmp({"Cargo.toml": ""}), ["rust"])
        self.assertEqual(self.steps(self.tmp({"Cargo.toml": ""}), ["rust"]) or [], [])
        self.assertIn("rust", text)
        self.assertNotIn("- id:", text)

    def test_no_evidence_gives_a_commented_placeholder(self):
        text = cisetup.render(self.tmp({}), [])
        self.assertNotIn("\n- id:", "\n" + text)
        self.assertTrue(all(ln.startswith("#") for ln in text.splitlines()), text)
        self.assertIn("before: verbs", text)

    def test_several_ecosystems_render_in_a_fixed_order(self):
        s = self.steps(fixture("py"), ["python", "node"])
        ids = [x["id"] for x in s]
        self.assertEqual(ids, ["toolchain-node", "toolchain-python", "toolchain-uv"])
        self.assertEqual(ids, [x["id"] for x in self.steps(fixture("py"), ["node", "python"])])


class MergeBeforeAfterTest(unittest.TestCase):
    BASE = {"steps": [{"id": "checkout", "uses": "a"}, {"id": "toolchain-node", "uses": "n"}, {"id": "verbs", "run": "v"}]}

    def ids(self, over):
        return [s["id"] for s in render.merge(self.BASE, {"steps": over})["steps"]]

    def test_before_and_after_place_a_new_step_by_id(self):
        self.assertEqual(self.ids([{"id": "mine", "run": "x", "before": "toolchain-node"}]), ["checkout", "mine", "toolchain-node", "verbs"])
        self.assertEqual(self.ids([{"id": "mine", "run": "x", "after": "toolchain-node"}]), ["checkout", "toolchain-node", "mine", "verbs"])
        self.assertEqual(self.ids([{"id": "mine", "run": "x", "before": "checkout"}]), ["mine", "checkout", "toolchain-node", "verbs"])
        self.assertEqual(self.ids([{"id": "mine", "run": "x", "after": "verbs"}]), ["checkout", "toolchain-node", "verbs", "mine"])
        self.assertEqual(self.ids([{"id": "mine", "run": "x"}]), ["checkout", "toolchain-node", "verbs", "mine"])             # no placement: appended (unchanged)

    def test_placement_keys_are_not_merged_into_the_step(self):
        out = render.merge(self.BASE, {"steps": [{"id": "mine", "run": "x", "before": "verbs"}]})["steps"]
        self.assertEqual(out[2], {"id": "mine", "run": "x"})

    def test_two_inserts_keep_their_relative_order(self):
        got = self.ids([{"id": "a", "run": "1", "before": "verbs"}, {"id": "b", "run": "2", "before": "verbs"}])
        self.assertEqual(got, ["checkout", "toolchain-node", "a", "b", "verbs"])
        got = self.ids([{"id": "a", "run": "1", "before": "verbs"}, {"id": "b", "run": "2", "after": "a"}])
        self.assertEqual(got, ["checkout", "toolchain-node", "a", "b", "verbs"])

    def test_an_existing_id_is_still_replaced_in_place(self):
        out = render.merge(self.BASE, {"steps": [{"id": "toolchain-node", "with": {"node-version": "24"}}]})["steps"]
        self.assertEqual(out[1], {"id": "toolchain-node", "uses": "n", "with": {"node-version": "24"}})

    def test_unknown_reference_is_an_error_naming_the_ids(self):
        with self.assertRaises(render.InvalidOverride) as cm:
            render.merge(self.BASE, {"steps": [{"id": "mine", "run": "x", "before": "nope"}]})
        self.assertIn("nope", str(cm.exception))
        self.assertIn("toolchain-node", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
