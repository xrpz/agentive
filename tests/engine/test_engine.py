# agentive-unit: selftest.component
# design: spec.init_apply (entry_rule, empty_dir, managed_regions, update, custom_overrides, headless_init_update, conflicts D10), docs/impl/B.md §2
"""The apply engine (src/engine/, run from the generated Claude Code payload) on temporary repos.

Every file the engine writes that a design contract names is validated against its schema."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PAYLOAD = os.path.join(ROOT, "plugins", "claude-code", "agentive", "payload")
ENGINE = os.path.join(PAYLOAD, "engine", "apply.py")
sys.path.insert(0, os.path.join(PAYLOAD, "engine"))
sys.path.insert(0, os.path.join(PAYLOAD, "repo-layer", "dot-agents", "lib"))

from jsonschema import Draft202012Validator  # noqa: E402
from referencing import Registry, Resource  # noqa: E402

from agentive import ayaml  # noqa: E402
import evidence  # noqa: E402
import regions  # noqa: E402
import render  # noqa: E402

_REG, _S = Registry(), {}
for _n in sorted(os.listdir(os.path.join(ROOT, "design", "schemas"))):
    with open(os.path.join(ROOT, "design", "schemas", _n), encoding="utf-8") as _f:
        _s = json.load(_f)
    _S[_n[:-len(".schema.json")]] = _s
    _REG = _REG.with_resource(_s["$id"], Resource.from_contents(_s))

ENV = {k: v for k, v in os.environ.items() if k not in ("CI", "GITHUB_ACTIONS", "AGENTIVE_HEADLESS")}
TODAY = "2026-10-28"


def schema_errors(name, inst):
    return [f"{'/'.join(map(str, e.absolute_path))}: {e.message}" for e in Draft202012Validator(_S[name], registry=_REG).iter_errors(inst)]


def has_entry(install):
    with open(os.path.join(PAYLOAD, "index.json"), encoding="utf-8") as f:
        return any(e["install"] == install for e in json.load(f)["entries"])


class Base(unittest.TestCase):
    def setUp(self):
        self.repo = tempfile.mkdtemp(prefix="agentive-engine-")
        self.values = os.path.join(self.repo, "..", os.path.basename(self.repo) + "-values.json")
        self.write_values({"tools": {}})

    def tearDown(self):
        shutil.rmtree(self.repo, ignore_errors=True)
        if os.path.exists(self.values):
            os.unlink(self.values)

    def write_values(self, v):
        with open(self.values, "w", encoding="utf-8") as f:
            json.dump(v, f)

    def put(self, rel, text):
        p = os.path.join(self.repo, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write(text)

    def read(self, rel):
        with open(os.path.join(self.repo, rel), encoding="utf-8", newline="") as f:
            return f.read()

    def exists(self, rel):
        return os.path.lexists(os.path.join(self.repo, rel))

    def git(self, *args):
        subprocess.run(["git", "-C", self.repo] + list(args), check=True, capture_output=True,
                       env=dict(ENV, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t"))

    def commit(self, msg="c"):
        self.git("add", "-A")
        self.git("commit", "-qm", msg)

    def engine(self, *args, env=None, values=True):
        cmd = [sys.executable, ENGINE] + list(args) + ["--repo", self.repo, "--today", TODAY]
        if values:
            cmd += ["--values", self.values]
        r = subprocess.run(cmd, capture_output=True, text=True, env=env or ENV)
        out = r.stdout
        changed = out.split("AGENTIVE_CHANGED:\n", 1)[1].split() if "AGENTIVE_CHANGED:\n" in out else None
        return r.returncode, out, changed

    def manifest(self):
        return ayaml.load(os.path.join(self.repo, ".agents", "manifest.yaml"))

    def assert_contracts(self):
        """Validate every contract file present in the repo."""
        self.assertEqual(schema_errors("manifest", self.manifest()), [])
        for rel, name in ((".agents/skills.lock", "skills-lock"), (".agents/state/drift-report.json", "drift-report"),
                          (".agents/state/pending-confirmation.json", "pending-confirmation")):
            if self.exists(rel):
                self.assertEqual(schema_errors(name, json.loads(self.read(rel))), [], rel)
        for rel, name in ((".agents/guard.yaml", "guard-rules"), (".agents/compat.yaml", "version-compat"),
                          (".agents/framework.yaml", "project-config")):
            if self.exists(rel):
                self.assertEqual(schema_errors(name, ayaml.loads(self.read(rel))), [], rel)


class EvidenceTest(Base):
    def test_greenfield_has_no_ecosystem(self):
        self.put("README.md", "# demo\n")
        ev = evidence.scan(self.repo)
        self.assertEqual(ev["ecosystems"], [])
        self.assertEqual(ev["verbs"], {})
        self.assertIsNone(ev["stated"])

    def test_stated_stack_from_readme(self):
        self.put("README.md", "# demo\n\nStack: Python 3.12 with FastAPI\n")
        self.assertEqual(evidence.scan(self.repo)["stated"], {"stack": "Python", "source": "README.md"})

    def test_node_scripts_and_lockfile(self):
        self.put("package.json", json.dumps({"scripts": {"test": "jest", "lint": "eslint .", "build": "tsc -p ."},
                                             "devDependencies": {"typescript": "5", "jest": "29"}}))
        self.put("pnpm-lock.yaml", "lockfileVersion: 9\n")
        self.put("tsconfig.json", "{}")
        v = evidence.scan(self.repo)["verbs"]
        self.assertEqual(v["test"]["cmd"], "pnpm test")
        self.assertEqual(v["test"]["fail_count"], r"^\s+●")
        self.assertEqual(v["lint"]["cmd"], "pnpm run lint")
        self.assertEqual(v["setup"]["cmd"], "pnpm install --frozen-lockfile")
        self.assertEqual(v["typecheck"]["cmd"], "pnpm exec tsc --noEmit")
        self.assertEqual(v["typecheck"]["evidence"]["rank"], 4)
        self.assertEqual(v["verify"]["cmd"], ".agents/bin/verb verify")
        self.assertNotIn("run", v)

    def test_python_uv_pytest_ruff(self):
        self.put("pyproject.toml", "[project]\nname='x'\ndependencies=[]\n[tool.pytest.ini_options]\n[tool.ruff]\n[tool.uv]\n")
        self.put("uv.lock", "version = 1\n")
        v = evidence.scan(self.repo)["verbs"]
        self.assertEqual(v["test"]["cmd"], "uv run pytest")
        self.assertEqual(v["lint"]["cmd"], "uv run ruff check .")
        self.assertEqual(v["setup"]["cmd"], "uv sync --frozen")
        self.assertNotIn("typecheck", v)

    def test_go_defaults_and_makefile_rank(self):
        self.put("go.mod", "module x\n\ngo 1.22\n")
        self.put("main.go", "package main\nfunc main() {}\n")
        self.put("Makefile", "test:\n\tgo test -race ./...\n")
        v = evidence.scan(self.repo)["verbs"]
        self.assertEqual(v["test"]["cmd"], "make test")          # rank 2 beats the rank 4 default
        self.assertEqual(v["lint"]["cmd"], "go vet ./...")
        self.assertEqual(v["run"]["cmd"], "go run .")

    def test_java_maven_wrapper(self):
        self.put("pom.xml", "<project><build><plugins><plugin><artifactId>maven-checkstyle-plugin</artifactId></plugin></plugins></build></project>")
        self.put("mvnw", "#!/bin/sh\n")
        v = evidence.scan(self.repo)["verbs"]
        self.assertEqual(v["test"]["cmd"], "./mvnw -B test")
        self.assertEqual(v["lint"]["cmd"], "./mvnw -B -q checkstyle:check")

    def test_secret_scanner_wired_into_verify(self):
        self.put("go.mod", "module x\n")
        self.put(".gitleaks.toml", "")
        ev = evidence.scan(self.repo)
        self.assertTrue(ev["secret_scanner"])
        self.assertIn("gitleaks", ev["verbs"]["verify"]["cmd"])


class RenderTest(unittest.TestCase):
    CTX = {"blocks": {"render.a": "x\ny", "render.empty": "", "render.list": ["A", "B"]}, "manifest": {"verbs": {"test": {"cmd": "t"}}},
           "config": {"review": {"managed_code_review_copy": True}}, "ecosystems": ["node"]}

    def test_block_inline_and_removed_line(self):
        out = render.render_text("a\n  %%agentive:render.a%%\n%%agentive:render.empty%%\nv=%%agentive:verbs.test.cmd%%\n", self.CTX)
        self.assertEqual(out, "a\n  x\n  y\nv=t\n")

    def test_unknown_key_and_multiline_inline_fail(self):
        with self.assertRaises(render.RenderError):
            render.render_text("%%agentive:nope.x%%\n", self.CTX)
        with self.assertRaises(render.RenderError):
            render.render_text("v=%%agentive:render.a%%\n", self.CTX)

    def test_json_value_placeholder(self):
        out = json.loads(render.render("x.json", '{"d": "%%agentive:render.list%%", "s": "v %%agentive:verbs.test.cmd%%"}', self.CTX))
        self.assertEqual(out, {"d": ["A", "B"], "s": "v t"})

    def test_when(self):
        w = lambda e: render.eval_when(e, self.CTX)  # noqa: E731
        self.assertTrue(w("review.managed_code_review_copy"))
        self.assertFalse(w("rule_packs.boundary"))
        self.assertTrue(w("stack:node and evidence"))
        self.assertFalse(w("stack:go or not evidence"))
        self.assertTrue(w("verb:test"))

    def test_override_rules(self):
        base = {"a": 1, "m": {"x": 1}, "ids": [{"id": "p", "v": 1}], "l": [1]}
        over = {"a": 2, "m": {"y": 2}, "ids": [{"id": "p", "v": 9}, {"id": "q"}], "l": [2]}
        self.assertEqual(render.merge(base, over), {"a": 2, "m": {"x": 1, "y": 2}, "ids": [{"id": "p", "v": 9}, {"id": "q"}], "l": [1, 2]})
        with self.assertRaises(render.InvalidOverride):
            render.merge({"m": {"x": 1}}, {"m": 3})

    def test_text_override_fenced_block_merge(self):
        base = "# R\n\n```yaml agentive-review-rules\na: 1\nl: [x]\n```\n"
        out = render.apply_override("code_review.md", base, "```yaml agentive-review-rules\nl: [y]\n```\n\nextra line\n")
        self.assertIn("extra line", out)
        block = out.split("```yaml agentive-review-rules\n", 1)[1].split("```", 1)[0]
        self.assertEqual(ayaml.loads(block), {"a": 1, "l": ["x", "y"]})


class RegionTest(unittest.TestCase):
    def test_splice_keeps_outside_bytes_and_crlf(self):
        text = "\ufeffuser line\r\n<!-- AGENTIVE START context -->\r\nold\r\n<!-- AGENTIVE END context -->\r\ntail no newline"
        span = regions.find(text, "md", "context")
        new = regions.splice(text, span, regions.render_region("md", "context", "new body\n", TODAY, None, "\r\n"))
        self.assertTrue(new.startswith("\ufeffuser line\r\n"))
        self.assertTrue(new.endswith("<!-- AGENTIVE END context -->\r\ntail no newline"))
        self.assertEqual(regions.body_hash(new, regions.find(new, "md", "context")), regions.text_hash("new body"))

    def test_malformed(self):
        for t in ("<!-- AGENTIVE START c -->\n", "<!-- AGENTIVE END c -->\n<!-- AGENTIVE START c -->\n",
                  "<!-- AGENTIVE START c -->\n<!-- AGENTIVE START d -->\n<!-- AGENTIVE END c -->\n"):
            with self.assertRaises(regions.Malformed):
                regions.find(t, "md", "c")

    def test_hash_ignores_provenance_and_trailing_space(self):
        a = "# AGENTIVE START state\n# Verified 2026-01-01 against none. Managed by agentive; x\n.agents/state/  \n# AGENTIVE END state\n"
        self.assertEqual(regions.body_hash(a, regions.find(a, "hash", "state")), regions.text_hash(".agents/state/\n"))


class InitUpdateTest(Base):
    def init(self, *extra, env=None):
        return self.engine("init", "--confirm-all", "--no-managed-review-copy", *extra, env=env)

    def test_greenfield_init_pending_and_idempotent(self):
        self.git("init", "-q")
        self.put("README.md", "# demo\n\nWe will use Python as our language.\n")
        rc, out, changed = self.init()
        self.assertEqual(rc, 0, out)
        m = self.manifest()
        self.assertEqual(m["verbs"]["test"], {"status": "pending", "reason": "no_evidence", "todo": {"stack": "Python", "source": "README.md"}})
        self.assertIn("AGENTIVE_PENDING verb=test", out)
        self.assertIn(".agents/manifest.yaml", changed)
        self.assertIn("근거 없음 · 대기", self.read("AGENTS.md"))
        self.assertIn("TODO: Python named in README.md", self.read("AGENTS.md"))
        settings = json.loads(self.read(".claude/settings.json"))
        self.assertIn("Read(.env)", settings["permissions"]["deny"])
        self.assertIn("Bash(gh pr merge)", settings["permissions"]["deny"])
        self.assertIn("Bash(git reset --hard *)", settings["permissions"]["ask"])
        self.assertIn("package.json", settings["hooks"]["FileChanged"][0]["matcher"])
        self.assertEqual(settings["hooks"]["PreToolUse"][0]["hooks"][0]["onFailure"], "block")
        self.assert_contracts()
        rc, out, changed = self.engine("update", "--confirm-all")
        self.assertEqual(rc, 0, out)
        self.assertEqual(changed, [], out)
        self.assert_contracts()

    def test_node_repo_permissions_from_paths(self):
        self.git("init", "-q")
        self.put("package.json", json.dumps({"scripts": {"test": "node --test"}}))
        self.put(".gitignore", "dist/\nnode_modules/\n")
        os.makedirs(os.path.join(self.repo, "vendor"))
        rc, out, _ = self.init()
        self.assertEqual(rc, 0, out)
        deny = json.loads(self.read(".claude/settings.json"))["permissions"]["deny"]
        self.assertIn("Read(./dist/**)", deny)
        self.assertIn("Edit(./dist/**)", deny)
        self.assertNotIn("Read(./node_modules/**)", deny)
        g = self.read(".gitignore")
        self.assertTrue(g.startswith("dist/\nnode_modules/\n\n# AGENTIVE START state\n"), g)
        self.assertEqual(self.manifest()["verbs"]["test"]["cmd"], "npm test")
        self.assert_contracts()

    def test_entry_checks_and_status(self):
        rc, out, _ = self.engine("update")
        self.assertEqual(rc, 1)
        self.assertIn("AGENTIVE_STOP", out)
        self.assertIn("NEXT=ask the user to run /agentive:init", out)
        self.assertEqual(self.init()[0], 0)
        rc, out, _ = self.engine("init")
        self.assertEqual(rc, 1)
        self.assertIn("AGENTIVE_STATUS summary modified=0", out)
        self.assertIn("NEXT=ask the user to run /agentive:update", out)
        rc, out, changed = self.engine("status")
        self.assertEqual((rc, changed), (0, []))
        self.assertIn("/doctor prompt-audit", out)
        rc, out, _ = self.engine("init", "--managed-review-copy", "--no-managed-review-copy")
        self.assertEqual(rc, 2)

    def test_drift_force_and_accept(self):
        self.git("init", "-q")
        self.assertEqual(self.init()[0], 0)
        self.put(".codex/config.toml", self.read(".codex/config.toml") + "# my edit\n")
        rc, out, changed = self.engine("update", "--confirm-all")
        self.assertEqual(rc, 0, out)
        self.assertIn("AGENTIVE_DRIFT id=.codex/config.toml reason=customized", out)
        self.assertNotIn(".codex/config.toml", changed)
        rep = json.loads(self.read(".agents/state/drift-report.json"))
        self.assertEqual([i["id"] for i in rep["items"]], [".codex/config.toml"])
        self.assertEqual(rep["items"][0]["override_path"], ".agents/custom/.codex/config.toml")
        self.assert_contracts()
        rc, out, changed = self.engine("update", "--confirm-all", "--force")
        self.assertIn(".codex/config.toml", changed)
        rep = json.loads(self.read(".agents/state/drift-report.json"))
        self.assertTrue(rep["force"])
        self.assertEqual([f["id"] for f in rep["forced"]], [".codex/config.toml"])
        self.assertNotIn("# my edit", self.read(".codex/config.toml"))
        self.assert_contracts()

    def test_regions_user_text_kept_and_region_drift(self):
        self.git("init", "-q")
        self.put("AGENTS.md", "# Mine\r\n\r\nkeep this\r\n")
        self.assertEqual(self.init()[0], 0)
        text = self.read("AGENTS.md")
        self.assertTrue(text.startswith("# Mine\r\n\r\nkeep this\r\n\r\n<!-- AGENTIVE START context -->\r\n"), text[:120])
        self.put("AGENTS.md", text.replace("Review rules: @code_review.md", "Review rules: @code_review.md (edited)"))
        rc, out, changed = self.engine("update", "--confirm-all")
        self.assertIn("AGENTIVE_DRIFT id=AGENTS.md#context reason=customized", out)
        self.assertNotIn("AGENTS.md", changed)
        rep = json.loads(self.read(".agents/state/drift-report.json"))
        self.assertEqual(rep["items"][0]["section"], "context")
        self.assert_contracts()
        rc, out, changed = self.engine("update", "--confirm-all", "--accept", "AGENTS.md#context")
        self.assertIn("AGENTS.md", changed)
        self.assertNotIn("(edited)", self.read("AGENTS.md"))
        self.assertTrue(self.read("AGENTS.md").startswith("# Mine\r\n\r\nkeep this\r\n"))
        # malformed markers: the file is not written at all
        self.put("AGENTS.md", self.read("AGENTS.md").replace("<!-- AGENTIVE END context -->", ""))
        before = self.read("AGENTS.md")
        rc, out, changed = self.engine("update", "--confirm-all")
        self.assertEqual(self.read("AGENTS.md"), before)
        rep = json.loads(self.read(".agents/state/drift-report.json"))
        self.assertEqual({e["code"] for e in rep["errors"]}, {"malformed_markers"})
        self.assert_contracts()

    def test_d10_conflict_existing_settings(self):
        self.git("init", "-q")
        self.put(".claude/settings.json", '{"permissions": {"allow": ["Bash(ls)"]}}\n')
        rc, out, changed = self.init()
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.read(".claude/settings.json"), '{"permissions": {"allow": ["Bash(ls)"]}}\n')
        m = self.manifest()
        self.assertEqual(m["conflicts"][0]["path"], ".claude/settings.json")
        self.assertTrue(self.exists(m["conflicts"][0]["proposal"]))
        rc, out, changed = self.engine("update", "--confirm-all", "--force")
        self.assertEqual(self.read(".claude/settings.json"), '{"permissions": {"allow": ["Bash(ls)"]}}\n')   # no_baseline is never forced
        self.assert_contracts()

    def test_custom_overrides(self):
        self.git("init", "-q")
        self.put(".agents/custom/.claude/settings.json", '{"permissions": {"allow": ["Bash(make *)"]}}\n')
        self.put(".agents/custom/.agents/guard.yaml", "rules:\n  - {id: reset-hard, decision: deny}\n  - {id: my-rule, decision: deny, match: {kind: argv_prefix, value: [\"make deploy\"]}, reason: deploys, next: ask a human}\n")
        self.assertEqual(self.init()[0], 0)
        s = json.loads(self.read(".claude/settings.json"))
        self.assertEqual(s["permissions"]["allow"], ["Bash(make *)"])
        self.assertIn("Bash(make deploy)", s["permissions"]["deny"])
        self.assertIn("Bash(git reset --hard)", s["permissions"]["deny"])
        g = ayaml.loads(self.read(".agents/guard.yaml"))
        self.assertEqual(next(r for r in g["rules"] if r["id"] == "my-rule")["source"], "repo")
        self.assertEqual(self.manifest()["generated"][".claude/settings.json"]["overrides"], [".agents/custom/.claude/settings.json"])
        self.assert_contracts()
        self.assertEqual(self.engine("update", "--confirm-all")[2], [])
        # an override that turns deny into ask is rejected; guard.yaml stays as it was
        self.put(".agents/custom/.agents/guard.yaml", "rules:\n  - {id: push-force, decision: ask}\n")
        rc, out, changed = self.engine("update", "--confirm-all")
        self.assertIn("invalid_override .agents/guard.yaml", out)
        self.assertNotIn(".agents/guard.yaml", changed)
        self.assert_contracts()

    def test_headless_skips_confirm_scope_then_continuation(self):
        self.git("init", "-q")
        rc, out, changed = self.init(env=dict(ENV, AGENTIVE_HEADLESS="1"))
        self.assertEqual(rc, 0, out)
        self.assertFalse(self.exists(".claude/settings.json"))
        self.assertFalse(self.exists(".agents/hooks/stop-verify.py"))
        self.assertTrue(self.exists(".agents/bin/verb"))
        self.assertIn("AGENTIVE_CONFIRM category=claude_settings path=.claude/settings.json", out)
        pc = json.loads(self.read(".agents/state/pending-confirmation.json"))
        self.assertEqual(pc["command"], "/agentive:init")
        self.assert_contracts()
        rc, out, changed = self.engine("init", "--confirm-all")
        self.assertEqual(rc, 0, out)
        self.assertIn(".claude/settings.json", changed)
        self.assertFalse(self.exists(".agents/state/pending-confirmation.json"))
        self.assertEqual(self.engine("update", "--confirm-all")[2], [])

    def test_interactive_without_confirmation_records_pending(self):
        self.git("init", "-q")
        rc, out, changed = self.engine("init", "--no-managed-review-copy")
        self.assertIn("AGENTIVE_CONFIRM", out)
        self.assertNotIn(".codex/hooks.json", changed)
        self.assertNotIn(".agents/framework.yaml", changed)
        rc, out, changed = self.engine("init", "--confirm", ".codex/hooks.json", "--no-managed-review-copy")
        self.assertEqual(rc, 0, out)
        self.assertEqual([c for c in changed if c.startswith(".codex/hooks")], [".codex/hooks.json"])
        pc = json.loads(self.read(".agents/state/pending-confirmation.json"))
        self.assertNotIn(".codex/hooks.json", [f["path"] for f in pc["files"]])

    @unittest.skipUnless(has_entry("REVIEW.md"), "REVIEW.md unit (owner C) not in the payload")
    def test_review_copy_on_and_off(self):
        self.git("init", "-q")
        self.assertEqual(self.engine("init", "--confirm-all", "--managed-review-copy")[0], 0)
        self.assertEqual(self.read("REVIEW.md"), self.read("code_review.md"))
        self.assertEqual(ayaml.loads(self.read(".agents/framework.yaml")), {"review": {"managed_code_review_copy": True}})
        self.put(".agents/framework.yaml", "review:\n  managed_code_review_copy: false\n")
        rc, out, changed = self.engine("update", "--confirm-all")
        self.assertIn("REVIEW.md", changed)
        self.assertFalse(self.exists("REVIEW.md"))
        self.assertNotIn("REVIEW.md", self.manifest()["generated"])

    def test_mirrors_and_skills_lock(self):
        self.git("init", "-q")
        self.assertEqual(self.init()[0], 0)
        lock = json.loads(self.read(".agents/skills.lock"))
        skills = sorted(n for n in os.listdir(os.path.join(self.repo, ".agents", "skills")))
        self.assertEqual(sorted(lock["skills"]), skills)
        for n in skills:
            p = os.path.join(self.repo, ".claude", "skills", n)
            self.assertTrue(os.path.islink(p) or os.path.isdir(p), n)
            self.assertTrue(os.path.isfile(os.path.join(p, "SKILL.md")), n)
        agents = sorted(n[:-3] for n in os.listdir(os.path.join(self.repo, ".agents", "agents"))) if self.exists(".agents/agents") else []
        for a in agents:
            self.assertTrue(self.exists(f".codex/agents/{a}.toml"))
            if sys.version_info >= (3, 11):
                import tomllib
                t = tomllib.loads(self.read(f".codex/agents/{a}.toml"))
                self.assertEqual(sorted(t), ["description", "developer_instructions", "name"])

    def test_copy_mode_when_core_symlinks_false(self):
        self.git("init", "-q")
        self.git("config", "core.symlinks", "false")
        self.assertEqual(self.init()[0], 0)
        d = os.path.join(self.repo, ".claude", "skills", "verify")
        self.assertFalse(os.path.islink(d))
        self.assertEqual(self.read(".claude/skills/verify/SKILL.md"), self.read(".agents/skills/verify/SKILL.md"))
        self.assertEqual(self.engine("update", "--confirm-all")[2], [])

    def test_prove_with_seed_and_reprove(self):
        self.git("init", "-q")
        self.put("go.mod", "module x\n")
        ok = {"cmd": "true", "tier": "inner", "evidence": {"rank": 1, "source": "test", "files": ["go.mod"]}}
        self.write_values({"tools": {}, "verbs": {"setup": dict(ok, tier="outer"), "build": dict(ok, tier="outer"), "lint": ok,
                                                  "test": dict(ok, cmd="test ! -e seed_test.txt")},
                           "seeds": {"test": {"path": "seed_test.txt", "content": "x"}}})
        rc, out, _ = self.engine("init", "--confirm-all", "--no-managed-review-copy", "--prove")
        self.assertEqual(rc, 0, out)
        v = self.manifest()["verbs"]
        self.assertTrue(v["test"]["proof"]["pass_run_id"].startswith("proof-test-pass-"))
        self.assertTrue(v["test"]["proof"]["seeded_fail_run_id"].startswith("proof-test-seeded-"))
        self.assertFalse(self.exists("seed_test.txt"))
        self.assertTrue(v["verify"]["proof"]["pass_run_id"].startswith("proof-verify-"))
        self.assertIn("AGENTIVE_WARN verb=lint has a pass proof only", out)
        self.assert_contracts()
        # a dirty slot is re-proved by reprove; a seed that does not fail marks the verb unproved
        self.put(".agents/state/dirty.json", json.dumps({"slots": [{"slot": "verbs.lint", "file": "go.mod", "at": "2026-10-28T00:00:00Z"}]}))
        self.write_values({"tools": {}, "verbs": {"lint": ok}, "seeds": {"lint": {"path": "s.txt", "content": "x"}}})
        rc, out, changed = self.engine("reprove")
        self.assertEqual(rc, 0, out)
        self.assertIn("AGENTIVE_PROOF verb=lint result=fail", out)
        self.assertEqual(self.manifest()["verbs"]["lint"]["proof"]["pass_run_id"], "unproved")
        self.assertEqual(json.loads(self.read(".agents/state/dirty.json"))["slots"][0]["slot"], "verbs.lint")
        self.assert_contracts()

    def test_pinned_verb_survives_update(self):
        self.git("init", "-q")
        self.put("package.json", json.dumps({"scripts": {"test": "node --test"}}))
        pinned = {"cmd": "npm run test:ci", "tier": "inner", "evidence": {"rank": 1, "source": "user", "files": ["package.json"]},
                  "pinned": {"by": "user", "at": "2026-10-28T00:00:00Z", "text": "use test:ci"}}
        self.write_values({"tools": {}, "verbs": {"test": pinned}})
        self.assertEqual(self.init()[0], 0)
        self.write_values({"tools": {}})
        self.engine("update", "--confirm-all")
        self.assertEqual(self.manifest()["verbs"]["test"]["cmd"], "npm run test:ci")
        self.assert_contracts()

    def test_stale_line_check_reports_renamed_path(self):
        self.git("init", "-q")
        self.put("package.json", json.dumps({"scripts": {"test": "node --test"}}))
        os.makedirs(os.path.join(self.repo, "src"))
        self.put("src/a.js", "x\n")
        self.commit()
        self.assertEqual(self.init()[0], 0)
        self.commit("agentive")
        self.git("mv", "src", "lib")
        self.commit("rename")
        rc, out, changed = self.engine("update", "--confirm-all")
        rep = json.loads(self.read(".agents/state/drift-report.json"))
        self.assertEqual(rep["stale_check"]["status"], "done")
        stale = [s for s in rep.get("stale", []) if s["named_path"] == "src"]
        self.assertTrue(stale, rep)
        self.assertEqual(stale[0]["resolution"], "refreshed")
        self.assertIn("Source: lib", self.read("AGENTS.md"))
        self.assert_contracts()

    def test_not_a_git_repository(self):
        rc, out, changed = self.init()
        self.assertEqual(rc, 0, out)
        self.assertIsNone(self.manifest()["generated"]["AGENTS.md#context"]["verified_commit"])
        rc, out, changed = self.engine("update", "--confirm-all")
        self.assertEqual(json.loads(self.read(".agents/state/drift-report.json"))["stale_check"]["reason"], "not_a_git_repository")
        self.assertEqual(changed, [])
        self.assert_contracts()


if __name__ == "__main__":
    unittest.main()
