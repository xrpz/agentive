# agentive-unit: selftest.component
# design: spec.repo_layer.files[f-rules], spec.repo_layer.files[f-reviewers], spec.init_apply.evidence_only, spec.init_apply.update, spec.init_apply.empty_dir.stated_stack
"""Regression tests for the 2026-10-29 implementation verification findings (one test per finding id) in the engine:
V1-05 (project rule files, revised by RULESNEUTRAL: one renderer for update and agentive-lint), NOBOUNDARYLINE, V1-14 (stated stack wording), V1-19 (first update after a build proof changes nothing),
V2-11 (Codex read-only roles), V2-21 (status names files written but not yet effective), E2E-33 (generated evidence from the
tracked state only)."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_engine import ENV, Base  # noqa: E402

import evidence  # noqa: E402
import prove  # noqa: E402


class RegressionTest(Base):
    def init(self, *extra):
        self.put("go.mod", "module x\n\ngo 1.22\n")
        self.git("init", "-q", "-b", "main")
        rc, out, changed = self.engine("init", "--no-managed-review-copy", *extra)
        self.assertEqual(rc, 0, out)
        return out

    def lint(self, *args):
        r = subprocess.run([sys.executable, os.path.join(self.repo, ".agents", "bin", "agentive-lint")] + list(args),
                           capture_output=True, text=True, cwd=self.repo, env=ENV)
        return r.returncode, r.stdout

    def test_v1_05_project_rule_files_reach_agents_md_and_claude_rules(self):
        """V1-05, revised by RULESNEUTRAL: a project rule in .agents/rules reaches AGENTS.md (Codex and, through @AGENTS.md,
        Claude Code); a merged rule gets no .claude/rules copy (no double read)."""
        self.init()
        self.put(".agents/rules/go-style.md", "---\npaths: [\"**/*.go\"]\n---\n\n# Go style\n\nWrap errors with %w.\n")
        rc, out, changed = self.engine("update")
        self.assertEqual(rc, 0, out)
        agents = self.read("AGENTS.md")
        self.assertIn("### go-style (paths: **/*.go)", agents)
        self.assertIn("Wrap errors with %w.", agents)
        self.assertNotIn("No rules yet.", agents)
        mirror = os.path.join(self.repo, ".claude", "rules", "go-style.md")
        self.assertFalse(os.path.lexists(mirror))                                               # merged: no copy
        self.assertEqual(self.read(".agents/rules/go-style.md").count("Wrap errors"), 1)        # the user's file is never written
        rc, out, changed = self.engine("update")
        self.assertEqual(changed, [], out)                                                      # idempotent
        os.unlink(os.path.join(self.repo, ".agents", "rules", "go-style.md"))
        rc, out, changed = self.engine("update")
        self.assertIn("No rules yet.", self.read("AGENTS.md"))
        self.assert_contracts()

    def test_rulesneutral_global_rules_section_and_lint_renders_like_update(self):
        """RULESNEUTRAL: a rule without paths: goes under `### All files`; `agentive-lint rules --write` (what /distill
        runs headless) renders the region exactly as /agentive:update does, so update changes nothing afterwards and
        `agentive-lint rules` (in `ci`) catches a rule committed without the render."""
        self.init()
        self.git("add", "-A")
        self.put(".agents/rules/team-style.md", "# Team style\n\nName booleans is_*, has_*.\n")
        self.put(".agents/rules/go-style.md", "---\npaths: [\"**/*.go\"]\n---\n\n# Go style\n\nWrap errors with %w.\n")
        rc, out = self.lint("rules")
        self.assertEqual(rc, 1, out)
        self.assertIn("LINT rules AGENTS.md: region path-rules differs from what .agents/rules renders (merged mode)", out)
        self.assertIn("NEXT: run .agents/bin/agentive-lint rules --write", out)
        rc, out = self.lint("rules", "--write")
        self.assertEqual(rc, 0, out)
        self.assertIn("AGENTIVE_RULES wrote AGENTS.md", out)
        agents = self.read("AGENTS.md")
        a, g = agents.index("### All files"), agents.index("### go-style (paths: **/*.go)")
        self.assertLess(a, g)
        self.assertIn("#### team-style\n##### Team style", agents)
        self.assertIn("### go-style (paths: **/*.go)\n#### Go style", agents)
        self.assertIn("Managed by agentive; edits inside this region", agents)                  # notice line kept (REGIONNOSHA)
        self.assertEqual(self.lint("rules")[0], 0)
        self.assertEqual(self.lint("rules", "--write")[1].splitlines()[-1], "AGENTIVE_LINT=ok (rules --write, 0 changed); NEXT=none")
        self.assertFalse(os.path.lexists(os.path.join(self.repo, ".claude", "rules", "team-style.md")))
        before = self.read("AGENTS.md")
        rc, out, changed = self.engine("update")
        # same renderer: AGENTS.md is not rewritten and nothing drifts; only the manifest records the new region hash
        self.assertEqual((rc, changed), (0, [".agents/manifest.yaml"]), out)
        self.assertEqual(self.read("AGENTS.md"), before)
        self.assertNotIn("AGENTIVE_DRIFT", out)
        self.assertEqual(self.engine("update")[2], [])

    def test_rulesneutral_pointer_mode_copies_and_removes_them_when_merged_again(self):
        """RULESNEUTRAL: when the rules no longer fit AGENTS.md, pointer lines + one .claude/rules copy per rule (T1); when
        they fit again, the copies agentive made are removed (by update and by agentive-lint rules --write); a
        Claude-only rule file the user wrote there is never touched."""
        self.put("AGENTS.md", "# Project\n\n" + "".join(f"- team note {i}\n" for i in range(170)))
        self.init()
        self.put(".claude/rules/mine.md", "# Claude only\n")
        self.put(".agents/rules/go-style.md", "---\npaths: [\"**/*.go\"]\n---\n\n# Go style\n\nWrap errors with %w.\n")
        rc, out, changed = self.engine("update")
        self.assertEqual(rc, 0, out)
        agents = self.read("AGENTS.md")
        self.assertIn("- go-style: read .agents/rules/go-style.md before you change a file matching **/*.go", agents)
        self.assertNotIn("Wrap errors with %w.", agents)
        mirror = os.path.join(self.repo, ".claude", "rules", "go-style.md")
        self.assertTrue(os.path.lexists(mirror))
        self.assertEqual(self.lint("rules")[0], 0)
        text = self.read("AGENTS.md")
        self.put("AGENTS.md", text.replace("".join(f"- team note {i}\n" for i in range(170)), "- team note\n"))
        self.assertIn("agentive copy of a rule that is merged into AGENTS.md", self.lint("rules")[1])
        rc, out = self.lint("rules", "--write")
        self.assertEqual(rc, 0, out)
        self.assertFalse(os.path.lexists(mirror))
        self.assertTrue(os.path.isfile(os.path.join(self.repo, ".claude", "rules", "mine.md")))
        self.assertIn("Wrap errors with %w.", self.read("AGENTS.md"))
        rc, out, changed = self.engine("update")
        self.assertEqual((rc, changed), (0, [".agents/manifest.yaml"]), out)                    # only the record of the removed copy goes
        self.assertNotIn(".claude/rules/go-style.md", self.manifest()["generated"])

    def test_noboundaryline_agents_md_has_no_boundary_or_service_lines(self):
        """NOBOUNDARYLINE: the generated regions hold no `Module boundaries:` line and no service facts lines."""
        self.init()
        agents = self.read("AGENTS.md")
        self.assertNotIn("Module boundaries", agents)
        self.assertNotIn("Issues:", agents)
        self.assertNotIn("render.boundary_line", agents)

    def test_v1_14_stated_stack_with_a_build_verb(self):
        self.put("README.md", "# Plan\n\nWe will build this in TypeScript.\n")
        self.assertEqual(evidence._stated_stack(self.repo), {"stack": "TypeScript", "source": "README.md"})
        self.put("README.md", "# Plan\n\nGo to the docs page.\n")
        self.assertIsNone(evidence._stated_stack(self.repo))

    def test_v1_19_generated_path_label_does_not_flip_after_a_build(self):
        self.put("package.json", json.dumps({"name": "x", "scripts": {"build": "tsc"}}))
        self.put(".gitignore", "dist/\n")
        self.git("init", "-q", "-b", "main")
        self.git("add", ".gitignore")                                # E2E-33: only a tracked ignore rule is evidence
        ev1 = evidence.scan(self.repo)
        os.makedirs(os.path.join(self.repo, "dist"))                 # the build proof creates the ignored output directory
        ev2 = evidence.scan(self.repo)
        self.assertEqual(ev1["paths"]["generated"], ev2["paths"]["generated"])
        self.assertIn({"path": "dist", "proof": "gitignored"}, ev2["paths"]["generated"])

    def test_e2e_33_untracked_self_ignored_output_does_not_change_generated(self):
        """E2E-33 (decision e2e_32_36): `uv build` writes dist/.gitignore with `*`; that untracked rule, inside the candidate, is no
        evidence, so the scan before and after the proof run (and after rm -rf dist) is the same."""
        self.put("pyproject.toml", "[project]\nname = \"x\"\nversion = \"0\"\n\n[build-system]\nrequires = [\"hatchling\"]\n"
                                   "build-backend = \"hatchling.build\"\n")
        self.put("uv.lock", "version = 1\n")
        self.git("init", "-q", "-b", "main")
        self.git("add", "-A")
        before = evidence.scan(self.repo)["paths"]["generated"]
        self.put("dist/.gitignore", "*\n")                           # what uv build leaves behind
        self.put("dist/x-0.tar.gz", "x")
        self.assertEqual(evidence.scan(self.repo)["paths"]["generated"], before)
        self.assertNotIn("dist", [g["path"] for g in before])

    def test_e2e_33_machine_local_ignore_rules_are_no_evidence(self):
        self.put("pyproject.toml", "[project]\nname = \"x\"\n\n[build-system]\nrequires = [\"hatchling\"]\n")
        self.git("init", "-q", "-b", "main")
        self.git("add", "-A")
        with open(os.path.join(self.repo, ".git", "info", "exclude"), "a") as f:
            f.write("build/\n")
        fd, excludes = tempfile.mkstemp(prefix="agentive-global-excludes-")
        with os.fdopen(fd, "w") as f:
            f.write("htmlcov/\n")
        self.addCleanup(os.unlink, excludes)
        self.git("config", "core.excludesFile", os.path.abspath(excludes))
        self.assertEqual(evidence.scan(self.repo)["paths"]["generated"], [])
        # a tracked rule counts; an untracked .gitignore does not
        self.put(".gitignore", "build/\n")
        self.assertEqual(evidence.scan(self.repo)["paths"]["generated"], [])
        self.git("add", ".gitignore")
        self.assertIn({"path": "build", "proof": "gitignored"}, evidence.scan(self.repo)["paths"]["generated"])

    def test_e2e_33_exists_means_a_tracked_file(self):
        self.put("go.mod", "module x\n\ngo 1.22\n")
        self.git("init", "-q", "-b", "main")
        self.git("add", "-A")
        self.put("bin/tool", "x")                                     # an untracked build output
        self.assertEqual(evidence.scan(self.repo)["paths"]["generated"], [])
        self.git("add", "bin/tool")
        self.assertEqual(evidence.scan(self.repo)["paths"]["generated"], [{"path": "bin", "proof": "exists"}])

    def test_v2_11_codex_roles_carry_the_read_only_limit(self):
        self.init()
        toml = self.read(".codex/agents/classifier.toml")
        self.assertIn("Read-only role: never edit, create, move or delete a repository file yourself", toml)
        self.assertIn("Run shell commands only to read", toml)                  # classifier has no Bash in its Claude tools
        verifier = self.read(".codex/agents/verifier.toml")
        self.assertIn("Read-only role:", verifier)
        self.assertNotIn("Run shell commands only to read", verifier)           # verifier runs tests (tools include Bash)

    def test_v2_21_status_names_not_effective_files(self):
        self.init()
        p = os.path.join(self.repo, ".agents", "state", "pending-confirmation.json")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"command": "/agentive:init", "framework_version": "0.1.0", "files": [
                {"path": ".codex/hooks.json", "category": "hooks", "reason": "codex_hooks_untrusted", "next": "trust the hooks in Codex /hooks"}]}, f)
        rc, out, _ = self.engine("status")
        self.assertIn("AGENTIVE_STATUS not-effective .codex/hooks.json reason=codex_hooks_untrusted; NEXT=trust the hooks in Codex /hooks", out)
        self.assertIn("not_effective=1", out)


if __name__ == "__main__":
    unittest.main()


class E2E47And48Test(Base):
    """E2E-47: the build proof removes the directories it created. E2E-48: python evidence puts `__pycache__/` and `*.egg-info/` in the
    `.gitignore#state` region; another stack gets nothing extra."""

    def prove_build(self, cmd):
        verbs = {"build": {"cmd": cmd}}
        lines = []
        prove.prove_verbs(self.repo, verbs, ["build"], None, lines.append)
        return verbs, lines

    def test_e2e_47_build_proof_removes_the_directories_it_created_and_nothing_else(self):
        self.put("keep/old.txt", "x")
        self.put("src/pkg/__init__.py", "")
        os.makedirs(os.path.join(self.repo, ".venv"))            # a dependency store from the setup proof stays
        cmd = ("mkdir -p dist build/lib src/pkg.egg-info keep/out .cache-new && echo w > dist/a.whl && echo f > made.txt "
               "&& touch .venv/created-by-build")
        verbs, lines = self.prove_build(cmd)
        self.assertTrue(verbs["build"]["proof"]["pass_run_id"].startswith("proof-build-pass-"))
        for gone in ("dist", "build", "src/pkg.egg-info", "keep/out", ".cache-new"):
            self.assertFalse(self.exists(gone), gone)
        for kept in ("keep/old.txt", "src/pkg/__init__.py", ".venv/created-by-build", "made.txt", ".agents/state/logs"):
            self.assertTrue(self.exists(kept), kept)               # old paths, files and the logs stay
        clean = [ln for ln in lines if ln.startswith("AGENTIVE_PROOF_CLEAN verb=build removed=")]
        self.assertEqual(len(clean), 1, lines)
        self.assertIn("build", clean[0])
        self.assertIn("dist", clean[0])

    def test_e2e_47_a_directory_that_existed_before_the_proof_is_never_removed(self):
        self.put("dist/old.whl", "x")
        verbs, lines = self.prove_build("mkdir -p dist/new && echo y > dist/new/b.whl")
        self.assertTrue(self.exists("dist/old.whl"))
        self.assertFalse(self.exists("dist/new"))                   # new at depth 2
        self.assertFalse(any(ln.startswith("AGENTIVE_PROOF_CLEAN") and "removed=dist," in ln for ln in lines))

    def test_e2e_47_failed_build_proof_is_still_cleaned_and_other_verbs_are_not(self):
        verbs, lines = self.prove_build("mkdir dist; exit 1")
        self.assertFalse(self.exists("dist"))
        self.assertEqual(verbs["build"]["proof"]["pass_run_id"], "unproved")
        lint = {"lint": {"cmd": "mkdir -p .lint-cache"}}
        prove.prove_verbs(self.repo, lint, ["lint"], None, lambda ln: None)
        self.assertTrue(self.exists(".lint-cache"))                  # only the build proof cleans up

    def test_e2e_47_init_prove_outside_git_leaves_no_dist_for_the_next_update(self):
        """Outside git `exists` is a directory test, so a leftover dist/ became paths.generated at the first update."""
        self.put("go.mod", "module x\n\ngo 1.22\n")
        ok = {"cmd": "true", "tier": "inner", "evidence": {"rank": 1, "source": "test", "files": ["go.mod"]}}
        self.write_values({"tools": {}, "verbs": {"build": dict(ok, cmd="mkdir -p dist && echo w > dist/a.whl", tier="outer")}})
        rc, out, _ = self.engine("init", "--no-managed-review-copy", "--prove")
        self.assertEqual(rc, 0, out)
        self.assertFalse(self.exists("dist"), out)
        self.assertIn("AGENTIVE_PROOF_CLEAN verb=build removed=dist", out)
        self.write_values({"tools": {}})
        rc, out, changed = self.engine("update")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.manifest()["paths"]["generated"], [])

    def gitignore_region(self):
        text = self.read(".gitignore")
        return text.split("# AGENTIVE START state\n", 1)[1].split("# AGENTIVE END state", 1)[0]

    def test_e2e_48_python_evidence_adds_pycache_and_egg_info_to_the_region(self):
        self.git("init", "-q", "-b", "main")
        self.put("pyproject.toml", "[project]\nname = \"x\"\nversion = \"0.1.0\"\n")
        rc, out, _ = self.engine("init", "--no-managed-review-copy")
        self.assertEqual(rc, 0, out)
        body = [ln for ln in self.gitignore_region().splitlines() if ln and not ln.startswith("#")]
        self.assertEqual(body, [".agents/state/", ".claude/worktrees/", "__pycache__/", "*.egg-info/"])
        rc, out, changed = self.engine("update")
        self.assertEqual(changed, [], out)

    def test_e2e_48_other_stacks_get_only_the_state_lines(self):
        self.git("init", "-q", "-b", "main")
        self.put("go.mod", "module x\n\ngo 1.22\n")
        rc, out, _ = self.engine("init", "--no-managed-review-copy")
        self.assertEqual(rc, 0, out)
        body = [ln for ln in self.gitignore_region().splitlines() if ln and not ln.startswith("#")]
        self.assertEqual(body, [".agents/state/", ".claude/worktrees/"])


class IndexMissingNextTest(unittest.TestCase):
    """E2E-56: an unreadable payload index names the generated engine when the engine runs from the framework src/, and the
    reinstall command when an installed payload lacks its index."""

    def run_engine(self, engine):
        repo = tempfile.mkdtemp(prefix="agentive-e2e56-")
        self.addCleanup(shutil.rmtree, repo, ignore_errors=True)
        r = subprocess.run([sys.executable, engine, "status", "--repo", repo], capture_output=True, text=True, env=ENV)
        return r.returncode, r.stdout.strip().splitlines()

    def test_e2e_56_src_run_names_the_generated_engine(self):
        from test_engine import ROOT
        rc, lines = self.run_engine(os.path.join(ROOT, "src", "engine", "apply.py"))
        self.assertEqual(rc, 2, lines)
        self.assertEqual(len(lines), 1, lines)
        self.assertTrue(lines[0].startswith("AGENTIVE_ERROR payload index "), lines)
        self.assertIn("NEXT=", lines[0])
        self.assertIn("python3 plugins/claude-code/agentive/payload/engine/apply.py", lines[0])
        self.assertIn("python3 tools/generate.py", lines[0])
        self.assertNotIn("reinstall", lines[0])

    def test_e2e_56_installed_payload_without_index_keeps_reinstall(self):
        from test_engine import PAYLOAD
        tmp = tempfile.mkdtemp(prefix="agentive-e2e56-payload-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        copy = os.path.join(tmp, "payload")
        shutil.copytree(PAYLOAD, copy, ignore=shutil.ignore_patterns("__pycache__"))
        os.unlink(os.path.join(copy, "index.json"))
        rc, lines = self.run_engine(os.path.join(copy, "engine", "apply.py"))
        self.assertEqual(rc, 2, lines)
        self.assertEqual(len(lines), 1, lines)
        self.assertIn("NEXT=reinstall the agentive plugin (claude plugin update agentive@<name>)", lines[0])
        self.assertNotIn("plugins/claude-code", lines[0])
