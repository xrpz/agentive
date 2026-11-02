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

    def test_e2e_21_lockfiles_are_not_check_verb_evidence(self):
        """E2E-21 (LOCKFILEDEPS): uv.lock / pnpm-lock.yaml stay setup and test evidence, never lint or typecheck evidence."""
        self.put("pyproject.toml", "[project]\nname = \"x\"\n[tool.ruff]\n[tool.mypy]\n[tool.pytest.ini_options]\n")
        self.put("uv.lock", "version = 1\n")
        self.put("requirements.txt", "requests\n")
        v = evidence.scan(self.repo)["verbs"]
        for name in ("lint", "typecheck"):
            self.assertNotIn("uv.lock", v[name]["evidence"]["files"], name)
            self.assertIn("pyproject.toml", v[name]["evidence"]["files"], name)
        self.assertIn("uv.lock", v["setup"]["evidence"]["files"])
        self.assertIn("uv.lock", v["test"]["evidence"]["files"])

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
        self.assertEqual(v["lint"]["cmd"], "uv run ruff check --extend-exclude .agents,.claude,.codex .")
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

    def test_lint_scope_per_stack(self):
        """Lint scope at init [설계 판단]: file_args true only for a tool that takes file paths; exts from the primary ecosystem.
        One case per fixture stack (ts, py, go, java) plus the whole-project commands the brief names."""
        # ts: eslint config -> `npx eslint .` takes files; a script that chains tools or wraps tsc does not
        self.put("package.json", json.dumps({"devDependencies": {"eslint": "9"}}))
        self.put("package-lock.json", "{}")
        self.put("eslint.config.js", "export default [];\n")
        lint = evidence.scan(self.repo)["verbs"]["lint"]
        self.assertEqual((lint["cmd"], lint["file_args"]),
                         ("npx eslint --ignore-pattern .agents/ --ignore-pattern .claude/ --ignore-pattern .codex/ .", True))
        self.assertEqual(lint["exts"], [".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"])
        self.put("package.json", json.dumps({"scripts": {"lint": "eslint . && tsc --noEmit"}}))
        self.assertIs(evidence.scan(self.repo)["verbs"]["lint"]["file_args"], False)
        self.put("package.json", json.dumps({"scripts": {"lint": "prettier --check ."}}))
        self.assertIs(evidence.scan(self.repo)["verbs"]["lint"]["file_args"], False)   # E2E-04: `npm run lint -- f` keeps the `.`
        self.put("package.json", json.dumps({"scripts": {"lint": "prettier --check"}}))
        self.assertIs(evidence.scan(self.repo)["verbs"]["lint"]["file_args"], True)
        self.put("package.json", json.dumps({"scripts": {"lint": "tsc --noEmit"}}))
        self.assertIs(evidence.scan(self.repo)["verbs"]["lint"]["file_args"], False)

    def test_lint_scope_python(self):
        self.put("pyproject.toml", "[project]\nname='x'\n[tool.ruff]\n[tool.uv]\n")
        self.put("uv.lock", "version = 1\n")
        lint = evidence.scan(self.repo)["verbs"]["lint"]
        self.assertEqual((lint["cmd"], lint["file_args"], lint["exts"]),
                         ("uv run ruff check --extend-exclude .agents,.claude,.codex .", True, [".py"]))

    def test_lint_scope_go(self):
        self.put("go.mod", "module x\n")
        lint = evidence.scan(self.repo)["verbs"]["lint"]
        self.assertEqual((lint["cmd"], lint["file_args"], lint["exts"]), ("go vet ./...", False, [".go"]))
        self.put(".golangci.yml", "linters: {}\n")
        lint = evidence.scan(self.repo)["verbs"]["lint"]
        self.assertEqual((lint["cmd"], lint["file_args"]), ("golangci-lint run", False))

    def test_lint_scope_java_and_rust(self):
        self.put("pom.xml", "<project><build><plugins><plugin><artifactId>maven-checkstyle-plugin</artifactId></plugin></plugins></build></project>")
        lint = evidence.scan(self.repo)["verbs"]["lint"]
        self.assertEqual((lint["file_args"], lint["exts"]), (False, [".java", ".kt", ".kts"]))
        os.unlink(os.path.join(self.repo, "pom.xml"))
        self.put("build.gradle.kts", "plugins { id(\"com.diffplug.spotless\") }\n")
        self.assertIs(evidence.scan(self.repo)["verbs"]["lint"]["file_args"], False)
        os.unlink(os.path.join(self.repo, "build.gradle.kts"))
        self.put("Cargo.toml", "[package]\nname='x'\n")
        lint = evidence.scan(self.repo)["verbs"]["lint"]
        self.assertEqual((lint["cmd"], lint["file_args"], lint["exts"]), ("cargo clippy -- -D warnings", False, [".rs"]))

    def test_lint_scope_command_table(self):
        yes = ["eslint .", "npx eslint src", "pnpm exec eslint .", "uv run ruff check .", "ruff format --check .", "black --check .",
               "prettier --check .", "gofmt -l .", "flake8", "biome check .", "mypy src"]
        no = ["tsc --noEmit", "go vet ./...", "golangci-lint run", "mypy", "mvn -B -q checkstyle:check", "./gradlew spotlessCheck",
              "cargo clippy -- -D warnings", "make lint", "npm run lint", "eslint . && tsc --noEmit", "some-unknown-linter", ""]
        for c in yes:
            self.assertTrue(evidence.file_args_for(c), c)
        for c in no:
            self.assertFalse(evidence.file_args_for(c), c)

    def test_repo_wide_commands_keep_the_installed_layer_out(self):
        """E2E-01 [설계 판단]: every derived command that walks the repository leaves .agents, .claude and .codex alone: the
        tool's additive exclude option (ruff, flake8, eslint), the manifest src/tests paths (biome: no CLI exclude), or the
        tool's own dot-directory default (mypy, pyright, go ./..., golangci-lint), so verify is green right after init."""
        def lint():
            return evidence.scan(self.repo)["verbs"].get("lint", {}).get("cmd")
        # python: ruff (plain, uv, poetry), flake8; mypy and pyright stay as derived
        self.put("pyproject.toml", "[project]\nname='x'\n[tool.ruff]\n[tool.mypy]\n")
        self.assertEqual(lint(), "ruff check --extend-exclude .agents,.claude,.codex .")
        self.assertEqual(evidence.scan(self.repo)["verbs"]["typecheck"]["cmd"], "mypy .")
        self.put("poetry.lock", "")
        self.assertEqual(lint(), "poetry run ruff check --extend-exclude .agents,.claude,.codex .")
        os.unlink(os.path.join(self.repo, "poetry.lock"))
        self.put("pyproject.toml", "[project]\nname='x'\n[tool.pyright]\n")
        self.put(".flake8", "[flake8]\n")
        self.assertEqual(lint(), "flake8 --extend-exclude .agents,.claude,.codex")
        self.assertEqual(evidence.scan(self.repo)["verbs"]["typecheck"]["cmd"], "pyright")
        self.assertTrue(evidence.scan(self.repo)["verbs"]["lint"]["file_args"])
        for f in ("pyproject.toml", ".flake8"):
            os.unlink(os.path.join(self.repo, f))
        # node: eslint gets ignore patterns; biome is scoped to the manifest paths, `.` when none is known
        self.put("package.json", json.dumps({"devDependencies": {"@biomejs/biome": "2"}}))
        self.put("package-lock.json", "{}")
        self.put("biome.json", "{}")
        self.assertEqual(lint(), "npx biome check .")
        os.makedirs(os.path.join(self.repo, "src", "test"))
        os.makedirs(os.path.join(self.repo, "tests"))
        self.assertEqual(lint(), "npx biome check src tests")           # src/test is inside src
        self.assertIs(evidence.scan(self.repo)["verbs"]["lint"]["file_args"], True)
        self.put("eslint.config.js", "export default [];\n")
        self.assertEqual(lint(), "npx eslint --ignore-pattern .agents/ --ignore-pattern .claude/ --ignore-pattern .codex/ .")
        for f in ("package.json", "package-lock.json", "biome.json", "eslint.config.js"):
            os.unlink(os.path.join(self.repo, f))
        # go: the go tool ignores dot directories, so the default commands stay
        self.put("go.mod", "module x\n")
        self.assertEqual(lint(), "go vet ./...")
        self.assertEqual(evidence.owned_exclude("mypy"), None)
        self.assertEqual(evidence.OWNED_DIRS, (".agents", ".claude", ".codex"))

    def test_makefile_lint_target_is_not_file_args(self):
        self.put("go.mod", "module x\n")
        self.put("Makefile", "lint:\n\tgofmt -l .\n")
        lint = evidence.scan(self.repo)["verbs"]["lint"]
        self.assertEqual((lint["cmd"], lint["file_args"]), ("make lint", False))

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
        new = regions.splice(text, span, regions.render_region("md", "context", "new body\n", "\r\n"))
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
        b = "# AGENTIVE START state\n" + regions.provenance("hash") + "\n.agents/state/\n# AGENTIVE END state\n"
        self.assertEqual(regions.body_hash(b, regions.find(b, "hash", "state")), regions.text_hash(".agents/state/\n"))

    def test_e2e_09_notice_line_is_constant(self):
        """REGIONNOSHA (E2E-09, user 2026-10-30 'B안으로 확정'): the notice line holds no date and no sha."""
        block = regions.render_region("md", "context", "body\n")
        self.assertEqual(block, "<!-- AGENTIVE START context -->\n<!-- " + regions.NOTICE + " -->\nbody\n<!-- AGENTIVE END context -->\n")
        self.assertNotRegex(block, r"Verified|\d{4}-\d{2}-\d{2}|[a-f0-9]{40}")
        self.assertTrue(regions.notice_current(block, regions.find(block, "md", "context"), "md"))
        old = block.replace(regions.provenance("md"), "<!-- Verified 2026-10-28 against abc1234. " + regions.NOTICE + " -->")
        self.assertFalse(regions.notice_current(old, regions.find(old, "md", "context"), "md"))


class InitUpdateTest(Base):
    def init(self, *extra, env=None):
        return self.engine("init", "--no-managed-review-copy", *extra, env=env)

    def test_greenfield_init_pending_and_idempotent(self):
        self.git("init", "-q")
        self.put("README.md", "# demo\n\nWe will use Python as our language.\n")
        rc, out, changed = self.init()
        self.assertEqual(rc, 0, out)
        m = self.manifest()
        self.assertEqual(m["verbs"]["test"], {"status": "pending", "reason": "no_evidence", "todo": {"stack": "Python", "source": "README.md"}})
        self.assertIn("AGENTIVE_PENDING verb=test", out)
        self.assertIn(".agents/manifest.yaml", changed)
        self.assertIn("pending (no evidence)", self.read("AGENTS.md"))
        self.assertIn("TODO: Python named in README.md", self.read("AGENTS.md"))
        settings = json.loads(self.read(".claude/settings.json"))
        self.assertIn("Read(.env)", settings["permissions"]["deny"])
        self.assertIn("Bash(gh pr merge)", settings["permissions"]["deny"])
        self.assertIn("Bash(git reset --hard *)", settings["permissions"]["ask"])
        self.assertIn("package.json", settings["hooks"]["FileChanged"][0]["matcher"])
        self.assertEqual(settings["hooks"]["PreToolUse"][0]["hooks"][0]["onFailure"], "block")
        self.assert_contracts()
        rc, out, changed = self.engine("update")
        self.assertEqual(rc, 0, out)
        self.assertEqual(changed, [], out)
        self.assert_contracts()

    def test_node_repo_permissions_from_paths(self):
        self.git("init", "-q")
        self.put("package.json", json.dumps({"scripts": {"test": "node --test"}}))
        self.put(".gitignore", "dist/\nnode_modules/\n")
        self.git("add", ".gitignore")                    # E2E-33: generated evidence reads tracked ignore rules only
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
        rc, out, changed = self.engine("update")
        self.assertEqual(rc, 0, out)
        self.assertIn("AGENTIVE_DRIFT id=.codex/config.toml reason=customized", out)
        self.assertNotIn(".codex/config.toml", changed)
        rep = json.loads(self.read(".agents/state/drift-report.json"))
        self.assertEqual([i["id"] for i in rep["items"]], [".codex/config.toml"])
        self.assertEqual(rep["items"][0]["override_path"], ".agents/custom/.codex/config.toml")
        self.assert_contracts()
        rc, out, changed = self.engine("update", "--force")
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
        self.put("AGENTS.md", text.replace("Worktrees: untracked files", "Worktrees (edited): untracked files"))
        rc, out, changed = self.engine("update")
        self.assertIn("AGENTIVE_DRIFT id=AGENTS.md#context reason=customized", out)
        self.assertNotIn("AGENTS.md", changed)
        rep = json.loads(self.read(".agents/state/drift-report.json"))
        self.assertEqual(rep["items"][0]["section"], "context")
        self.assert_contracts()
        rc, out, changed = self.engine("update", "--accept", "AGENTS.md#context")
        self.assertIn("AGENTS.md", changed)
        self.assertNotIn("(edited)", self.read("AGENTS.md"))
        self.assertTrue(self.read("AGENTS.md").startswith("# Mine\r\n\r\nkeep this\r\n"))
        # malformed markers: the file is not written at all
        self.put("AGENTS.md", self.read("AGENTS.md").replace("<!-- AGENTIVE END context -->", ""))
        before = self.read("AGENTS.md")
        rc, out, changed = self.engine("update")
        self.assertEqual(self.read("AGENTS.md"), before)
        rep = json.loads(self.read(".agents/state/drift-report.json"))
        self.assertEqual({e["code"] for e in rep["errors"]}, {"malformed_markers"})
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
        self.assertEqual(self.engine("update")[2], [])
        # an override that turns deny into ask is rejected; guard.yaml stays as it was
        self.put(".agents/custom/.agents/guard.yaml", "rules:\n  - {id: push-force, decision: ask}\n")
        rc, out, changed = self.engine("update")
        self.assertIn("invalid_override .agents/guard.yaml", out)
        self.assertNotIn(".agents/guard.yaml", changed)
        self.assert_contracts()

    @unittest.skipUnless(has_entry("REVIEW.md"), "REVIEW.md unit (owner C) not in the payload")
    def test_review_copy_on_and_off(self):
        self.git("init", "-q")
        self.assertEqual(self.engine("init", "--managed-review-copy")[0], 0)
        self.assertEqual(self.read("REVIEW.md"), self.read("code_review.md"))
        self.assertEqual(ayaml.loads(self.read(".agents/framework.yaml")), {"review": {"managed_code_review_copy": True}})
        self.put(".agents/framework.yaml", "review:\n  managed_code_review_copy: false\n")
        rc, out, changed = self.engine("update")
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
        self.assertEqual(self.engine("update")[2], [])

    def test_prove_with_seed_and_reprove(self):
        self.git("init", "-q")
        self.put("go.mod", "module x\n")
        ok = {"cmd": "true", "tier": "inner", "evidence": {"rank": 1, "source": "test", "files": ["go.mod"]}}
        self.write_values({"tools": {}, "verbs": {"setup": dict(ok, tier="outer"), "build": dict(ok, tier="outer"), "lint": ok,
                                                  "test": dict(ok, cmd="test ! -e seed_test.txt")},
                           "seeds": {"test": {"path": "seed_test.txt", "content": "x"}}})
        rc, out, _ = self.engine("init", "--no-managed-review-copy", "--prove")
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
        self.engine("update")
        self.assertEqual(self.manifest()["verbs"]["test"]["cmd"], "npm run test:ci")
        self.assert_contracts()

    def test_stale_line_check_reports_renamed_pointer(self):
        self.git("init", "-q")
        self.put("package.json", json.dumps({"scripts": {"test": "node --test"}}))
        self.put("docs/architecture.md", "# architecture\n")
        self.commit()
        self.assertEqual(self.init()[0], 0)
        self.assertIn("Docs (pointers to files that exist): docs/architecture.md, docs/adr/.", self.read("AGENTS.md"))
        self.commit("agentive")
        self.git("mv", "docs/architecture.md", "docs/design.md")
        self.commit("rename")
        rc, out, changed = self.engine("update")
        rep = json.loads(self.read(".agents/state/drift-report.json"))
        self.assertEqual(rep["stale_check"]["status"], "done")
        stale = [s for s in rep.get("stale", []) if s["named_path"] == "docs/architecture.md"]
        self.assertTrue(stale, rep)
        self.assertEqual(stale[0]["resolution"], "refreshed")
        self.assertNotIn("docs/architecture.md", self.read("AGENTS.md"))
        self.assert_contracts()

    def test_missing_pointer_reported_in_user_modified_region_and_human_lines_kept(self):
        self.git("init", "-q")
        self.put("ARCHITECTURE.md", "# a\n")
        self.commit()
        self.assertEqual(self.init()[0], 0)
        text = self.read("AGENTS.md").replace("Worktrees: untracked files", "Worktrees (edited): untracked files")
        self.put("AGENTS.md", text + "\nMy own line.\n")
        os.unlink(os.path.join(self.repo, "ARCHITECTURE.md"))
        rc, out, changed = self.engine("update")
        rep = json.loads(self.read(".agents/state/drift-report.json"))
        self.assertEqual([s["named_path"] for s in rep["stale"]], ["ARCHITECTURE.md"])
        self.assertEqual(rep["stale"][0]["resolution"], "reported")
        self.assertIsNone(rep["stale"][0]["commit"])
        self.assertIn("ARCHITECTURE.md", self.read("AGENTS.md"))      # kept: the region is user-modified
        self.assertIn("My own line.", self.read("AGENTS.md"))
        self.assert_contracts()

    def test_not_a_git_repository(self):
        rc, out, changed = self.init()
        self.assertEqual(rc, 0, out)
        self.assertNotIn("verified_commit", self.manifest()["generated"]["AGENTS.md#context"])
        rc, out, changed = self.engine("update")
        self.assertEqual(json.loads(self.read(".agents/state/drift-report.json"))["stale_check"]["reason"], "not_a_git_repository")
        self.assertEqual(changed, [])
        self.assert_contracts()


class CiToolTest(Base):
    """CITOOL (decisions.accepted, user 2026-10-29): the CI AI review follows the user's agent."""
    REVIEW = ".github/workflows/agentive-review.yml"
    EVAL = ".github/workflows/reviewer-eval.yml"

    def init(self, tool=None, *extra):
        self.git("init", "-q")
        args = ["init", "--confirm-all", "--no-managed-review-copy"] + (["--tool", tool] if tool else []) + list(extra)
        return self.engine(*args)

    def wf(self, rel):
        import yaml  # dev-time only
        return yaml.safe_load(self.read(rel))

    def llm_step(self):
        steps = self.wf(self.REVIEW)["jobs"]["ai-review"]["steps"]
        found = [st for st in steps if st.get("name") == "LLM stages with the repo reviewer subagents"]
        self.assertEqual(len(found), 1)
        return found[0], [st.get("name") or st.get("uses") or st.get("id") for st in steps]

    CLAUDE_SECRETS = {"api_key": ("ANTHROPIC_API_KEY", "anthropic_api_key", "AGENTIVE_CI_API_KEY"),
                      "oauth_token": ("CLAUDE_CODE_OAUTH_TOKEN", "claude_code_oauth_token", "AGENTIVE_CI_OAUTH_TOKEN")}

    def assert_claude(self, auth="both"):
        st, _ = self.llm_step()
        self.assertEqual(st["uses"], "anthropics/claude-code-action@v1")
        kinds = list(self.CLAUDE_SECRETS) if auth == "both" else [auth]       # CIAUTH: both by default
        self.assertEqual({k: v for k, v in st["with"].items() if k in ("anthropic_api_key", "claude_code_oauth_token")},
                         {self.CLAUDE_SECRETS[k][1]: self.secret_value(k, auth) for k in kinds})
        self.assertIn(".claude/agents/<agent>.md", st["with"]["prompt"])
        self.assertNotIn("openai", json.dumps(st))
        ev = self.wf(self.EVAL)["jobs"]["reviewer-eval"]
        self.assertEqual(ev["env"]["HAS_KEY"], "${{ %s }}" % " || ".join("secrets.%s != ''" % self.CLAUDE_SECRETS[k][0] for k in kinds))
        install = [x for x in ev["steps"] if str(x.get("run", "")).startswith("npm install")][0]
        self.assertEqual((install["name"], install["run"]), ("install Claude Code", "npm install -g @anthropic-ai/claude-code"))
        run = [x for x in ev["steps"] if str(x.get("name", "")).startswith("before and after")][0]
        self.assertEqual(run["env"], {self.CLAUDE_SECRETS[k][2]: self.secret_value(k, auth) for k in kinds})

    def secret_value(self, kind, auth):
        """E2E-12 (OAuth token first): with both, the API key is passed only when the OAuth token secret is empty."""
        if auth == "both" and kind == "api_key":
            return "${{ secrets.CLAUDE_CODE_OAUTH_TOKEN == '' && secrets.ANTHROPIC_API_KEY || '' }}"
        return "${{ secrets.%s }}" % self.CLAUDE_SECRETS[kind][0]

    def assert_codex(self):
        st, _ = self.llm_step()
        self.assertEqual(st["uses"], "openai/codex-action@v1")
        self.assertEqual(st["with"]["openai-api-key"], "${{ secrets.OPENAI_API_KEY }}")
        self.assertEqual(st["with"]["sandbox"], "workspace-write")
        self.assertIn(".agents/bin/agentive-review ci", st["with"]["prompt"])
        self.assertIn(".codex/agents/<agent>.toml", st["with"]["prompt"])
        self.assertNotIn("anthropic", json.dumps(st).lower())
        ev = self.wf(self.EVAL)["jobs"]["reviewer-eval"]
        self.assertEqual(ev["env"]["HAS_KEY"], "${{ secrets.OPENAI_API_KEY != '' }}")
        install = [x for x in ev["steps"] if str(x.get("run", "")).startswith("npm install")][0]
        self.assertEqual((install["name"], install["run"]), ("install Codex", "npm install -g @openai/codex"))
        self.assertEqual([x for x in ev["steps"] if "AGENTIVE_CI_API_KEY" in (x.get("env") or {})][0]["env"]["AGENTIVE_CI_API_KEY"],
                         "${{ secrets.OPENAI_API_KEY }}")

    def test_default_is_the_tool_that_ran_init(self):
        rc, out, changed = self.init("claude")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.manifest()["framework"]["init_tool"], "claude")
        self.assert_claude()
        self.assertIn("AGENTIVE_REPORT github: the CI AI review", out)
        self.assertIn("runs on Claude Code (the tool that ran init); add one repository secret, CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY "
                      "(review.pipeline.ci_auth: both)", out)
        self.assertIn("AGENTIVE_REPORT github: Claude CI secret: CLAUDE_CODE_OAUTH_TOKEN authenticates with a Claude Pro, Max, Team or Enterprise", out)
        self.assert_contracts()
        claude_steps = self.llm_step()[1]
        shutil.rmtree(self.repo)
        os.makedirs(self.repo)
        rc, out, _ = self.init("codex")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.manifest()["framework"]["init_tool"], "codex")
        self.assert_codex()
        self.assertIn("runs on Codex (the tool that ran init); add the repository secret OPENAI_API_KEY (Settings", out)
        self.assertNotIn("Claude CI secret", out)
        self.assertEqual(self.llm_step()[1], claude_steps)          # the job structure is the same for both tools
        self.assertTrue(self.exists(".codex/agents/correctness-reviewer.toml"))   # the one reviewer source is rendered for both tools
        self.assertTrue(self.exists(".claude/agents/correctness-reviewer.md"))
        self.assertEqual(set(self.wf(self.REVIEW)["jobs"]), {"outer-verbs", "dependency-review", "ai-review", "agentive-independence", "agentive-review-gate"})
        self.assert_contracts()
        rc, out, changed = self.engine("update", "--confirm-all", "--tool", "codex")
        self.assertEqual((rc, changed), (0, []), out)               # idempotent

    def test_no_tool_flag_counts_as_claude(self):
        self.assertEqual(self.init()[0], 0)
        self.assertEqual(self.manifest()["framework"]["init_tool"], "claude")
        self.assert_claude()

    def test_config_key_overrides_and_update_rerenders(self):
        self.assertEqual(self.init("claude")[0], 0)
        self.commit("agentive")
        self.put(".agents/framework.yaml", "review:\n  pipeline:\n    ci_tool: codex\n")
        self.assert_contracts()
        rc, out, changed = self.engine("update", "--confirm-all", "--tool", "claude")
        self.assertEqual(rc, 0, out)
        self.assertIn(self.REVIEW, changed)
        self.assertIn(self.EVAL, changed)
        self.assert_codex()
        self.assertEqual(self.manifest()["framework"]["init_tool"], "claude")      # the key does not rewrite the init tool
        self.assertIn("runs on Codex (review.pipeline.ci_tool)", out)
        self.assertIn("ci_tool", self.read(".agents/framework.yaml"))              # the user-owned config is never written by update
        rc, out, changed = self.engine("update", "--confirm-all")
        self.assertEqual(changed, [], out)
        self.put(".agents/framework.yaml", "review:\n  pipeline:\n    ci_tool: claude\n")
        rc, out, changed = self.engine("update", "--confirm-all")
        self.assertIn(self.REVIEW, changed)
        self.assert_claude()
        self.put(".agents/framework.yaml", "review:\n  managed_code_review_copy: false\n")        # key removed: back to the init tool
        self.assertEqual(self.engine("update", "--confirm-all")[0], 0)
        self.assert_claude()

    def test_update_from_the_other_tool_keeps_the_init_tool(self):
        self.assertEqual(self.init("codex")[0], 0)
        rc, out, changed = self.engine("update", "--confirm-all", "--tool", "claude")
        self.assertEqual((rc, changed), (0, []), out)
        self.assertEqual(self.manifest()["framework"]["init_tool"], "codex")
        self.assert_codex()

    def test_manifest_without_init_tool_counts_as_claude(self):
        self.assertEqual(self.init("claude")[0], 0)
        m = self.read(".agents/manifest.yaml")
        self.assertIn("init_tool: claude", m)
        self.put(".agents/manifest.yaml", m.replace("  init_tool: claude\n", "").replace(", init_tool: claude", ""))
        self.assertNotIn("init_tool", self.read(".agents/manifest.yaml"))
        rc, out, changed = self.engine("update", "--confirm-all", "--tool", "codex")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.manifest()["framework"]["init_tool"], "claude")      # written before CITOOL: CI was claude
        self.assert_claude()
        self.assert_contracts()

    def test_invalid_key_warns_and_uses_the_init_tool(self):
        self.assertEqual(self.init("codex")[0], 0)
        self.put(".agents/framework.yaml", "review:\n  pipeline:\n    ci_tool: gemini\n")
        rc, out, changed = self.engine("update", "--confirm-all")
        self.assertEqual(rc, 0, out)
        self.assertIn("AGENTIVE_WARN .agents/framework.yaml review.pipeline.ci_tool='gemini' is not claude or codex", out)
        self.assert_codex()

    def test_e2e_12_ci_auth_key_selects_the_claude_credential(self):
        """E2E-12 / CIAUTH (user 2026-10-30, "둘 다 지원하게 해줘"): both Claude credentials by default; review.pipeline.ci_auth
        renders only the chosen one at the next update; the report names both secrets and when to use which."""
        rc, out, _ = self.init("claude")
        self.assertEqual(rc, 0, out)
        self.assert_claude("both")
        for words in ("`claude setup-token`", "runs use the subscription, not API billing",
                      "for a secret shared across repositories (an organization secret) use the API key",
                      "OAuth token first: with both secrets set the CI passes only CLAUDE_CODE_OAUTH_TOKEN",
                      "review.pipeline.ci_auth: api_key|oauth_token"):
            self.assertIn(words, out)
        self.commit("agentive")
        for auth, secret in (("oauth_token", "CLAUDE_CODE_OAUTH_TOKEN"), ("api_key", "ANTHROPIC_API_KEY")):
            self.put(".agents/framework.yaml", f"review:\n  pipeline:\n    ci_auth: {auth}\n")
            self.assertEqual(schema_errors("project-config", {"review": {"pipeline": {"ci_auth": auth}}}), [])
            rc, out, changed = self.engine("update", "--confirm-all")
            self.assertEqual(rc, 0, out)
            self.assertIn(self.REVIEW, changed)
            self.assertIn(self.EVAL, changed)
            self.assert_claude(auth)
            self.assertIn(f"add the repository secret {secret} (review.pipeline.ci_auth: {auth})", out)
            self.assertEqual(self.engine("update", "--confirm-all")[2], [])                  # idempotent
            self.assert_contracts()
        self.put(".agents/framework.yaml", "review:\n  pipeline:\n    ci_auth: token\n")
        rc, out, _ = self.engine("update", "--confirm-all")
        self.assertIn("AGENTIVE_WARN .agents/framework.yaml review.pipeline.ci_auth='token' is not both | api_key | oauth_token; using both", out)
        self.assert_claude("both")
        self.assertTrue(schema_errors("project-config", {"review": {"pipeline": {"ci_auth": "token"}}}))
        self.put(".agents/framework.yaml", "review:\n  pipeline:\n    ci_tool: codex\n    ci_auth: oauth_token\n")
        rc, out, _ = self.engine("update", "--confirm-all")
        self.assertIn("review.pipeline.ci_auth applies to the claude CI path only", out)
        self.assert_codex()

    def test_schemas_accept_and_reject(self):
        base = {"review": {"pipeline": {"ci_tool": "codex"}}}
        self.assertEqual(schema_errors("project-config", base), [])
        self.assertTrue(schema_errors("project-config", {"review": {"pipeline": {"ci_tool": "both"}}}))
        self.assertTrue(schema_errors("project-config", {"ci_tool": "codex"}))      # only inside review.pipeline
        self.assertEqual(self.init("codex")[0], 0)
        m = self.manifest()
        self.assertEqual(schema_errors("manifest", m), [])
        m["framework"]["init_tool"] = "gemini"
        self.assertTrue(schema_errors("manifest", m))


if __name__ == "__main__":
    unittest.main()


class E2EBatchATest(Base):
    """E2E-11 (WFCHANGE) report line and E2E-13 (U6 revised to agentive:rubric@2) preset migration, user decisions 2026-10-30."""
    RUBRIC = ".agents/rubric.yaml"
    OVERRIDE = ".agents/custom/.agents/rubric.yaml"
    init = CiToolTest.init

    def test_e2e_11_report_names_the_install_pr_on_the_claude_path(self):
        rc, out, _ = self.init("claude")
        self.assertEqual(rc, 0, out)
        line = [ln for ln in out.splitlines() if ln.startswith("AGENTIVE_REPORT github: install PR:")]
        self.assertEqual(len(line), 1, out)
        for words in ("the first install PR and every PR that changes .github/workflows/agentive-review.yml run without the CI AI review",
                      "identical to the default branch's copy", "a human reviews and merges such a PR", "merge the install PR first"):
            self.assertIn(words, line[0])
        self.put(".agents/framework.yaml", "review:\n  pipeline:\n    ci_tool: codex\n")
        rc, out, _ = self.engine("update", "--confirm-all")
        self.assertEqual(rc, 0, out)
        self.assertNotIn("install PR:", out)                         # Claude path only

    def test_e2e_27_next_names_not_effective_lines_only_when_there_are_some(self):
        """E2E-27 (wording): the AGENTIVE_NOT_EFFECTIVE lines are printed right above AGENTIVE_NEXT, and NEXT names them (count and
        paths) only when there is at least one; an update that writes nothing new prints neither."""
        rc, out, _ = self.init("claude")
        self.assertEqual(rc, 0, out)
        lines = out.split("AGENTIVE_CHANGED:\n", 1)[0].splitlines()
        nxt = [i for i, ln in enumerate(lines) if ln.startswith("AGENTIVE_NEXT ")]
        ne = [i for i, ln in enumerate(lines) if ln.startswith("AGENTIVE_NOT_EFFECTIVE ")]
        self.assertEqual(len(nxt), 1, out)
        self.assertTrue(ne, out)
        self.assertEqual(ne, list(range(nxt[0] - len(ne), nxt[0])), out)          # a block right above NEXT
        paths = [ln.split("path=", 1)[1].split()[0] for ln in (lines[i] for i in ne)]
        self.assertIn("tell the human about the AGENTIVE_NOT_EFFECTIVE line" + ("s" if len(ne) > 1 else "") + " just above (", lines[nxt[0]])
        for p in paths:
            self.assertIn(p + ": ", lines[nxt[0]])
        self.assertTrue(self.exists(".agents/state/pending-confirmation.json"))
        rc, out, _ = self.engine("update", "--confirm-all")
        self.assertEqual(rc, 0, out)
        self.assertNotIn("AGENTIVE_NOT_EFFECTIVE", out)                             # neither the lines nor the NEXT part
        self.assertFalse(self.exists(".agents/state/pending-confirmation.json"))

    def test_e2e_14_init_next_says_a_human_merges_the_install_pr(self):
        """E2E-14 (HUMANMERGE): init's AGENTIVE_NEXT names the work branch, /pr and the human merge of the install PR (both CI
        tools); update keeps the plain commit line."""
        for tool in ("claude", "codex"):
            rc, out, _ = self.init(tool)
            self.assertEqual(rc, 0, out)
            nxt = [ln for ln in out.splitlines() if ln.startswith("AGENTIVE_NEXT ")]
            self.assertEqual(len(nxt), 1, out)
            for words in ("on a work branch", "open the install PR with /pr", "a human reviews and merges the install PR first",
                          "agents never merge it (MERGE=human)"):
                self.assertIn(words, nxt[0])
            self.tearDown()
            self.setUp()
        rc, out, _ = self.init("claude")
        rc, out, _ = self.engine("update", "--confirm-all")
        self.assertEqual(rc, 0, out)
        self.assertNotIn("install PR with /pr", "\n".join(ln for ln in out.splitlines() if ln.startswith("AGENTIVE_NEXT ")))

    def _old_install(self):
        """An install made before rubric@2: .agents/rubric.yaml holds agentive:rubric@1 and the manifest records that file."""
        import hashlib
        sys.path.insert(0, os.path.join(PAYLOAD, "repo-layer", "dot-agents", "lib"))
        from agentive.review import config as cfg
        rc, out, _ = self.init("claude")
        self.assertEqual(rc, 0, out)
        cur = self.read(self.RUBRIC)
        man = self.read(".agents/manifest.yaml")
        self.assertIn(hashlib.sha256(cur.encode("utf-8")).hexdigest(), man)
        old = cur.split("preset:", 1)[0] + ayaml.dumps({k: v for k, v in cfg.RUBRIC_PRESETS["agentive:rubric@1"].items()})
        self.put(self.RUBRIC, old)
        self.put(".agents/manifest.yaml", man.replace(hashlib.sha256(cur.encode("utf-8")).hexdigest(), hashlib.sha256(old.encode("utf-8")).hexdigest()))
        self.commit("agentive with rubric@1")
        return cfg

    def test_e2e_13_update_moves_the_preset_and_the_override_extends_together(self):
        cfg = self._old_install()
        self.put(self.OVERRIDE, "# team rubric\nextends: \"agentive:rubric@1\"   # kept comment\nitems:\n  - {id: size, weight: 20}\n")
        rc, out, changed = self.engine("update", "--confirm-all")
        self.assertEqual(rc, 0, out)
        self.assertIn(self.RUBRIC, changed)
        self.assertIn(self.OVERRIDE, changed)
        self.assertEqual(ayaml.loads(self.read(self.RUBRIC))["preset"], "agentive:rubric@2")
        self.assertEqual(self.read(self.OVERRIDE), "# team rubric\nextends: \"agentive:rubric@2\"   # kept comment\nitems:\n  - {id: size, weight: 20}\n")
        rep = [ln for ln in out.splitlines() if ln.startswith("AGENTIVE_REPORT rubric:")]
        self.assertEqual(len(rep), 2, out)
        self.assertIn(".agents/rubric.yaml moved from agentive:rubric@1 to agentive:rubric@2", rep[0])
        self.assertIn("extends moved from agentive:rubric@1 to agentive:rubric@2 together with the preset", rep[1])
        self.assertIn('to stay on agentive:rubric@1, set extends: "agentive:rubric@1"', rep[1])
        self.assertNotIn("extends", ayaml.loads(self.read(self.RUBRIC)))           # the override is applied at run time only
        src = lambda rel: self.read(rel) if self.exists(rel) else None       # noqa: E731
        eff = cfg.effective_rubric(src)
        self.assertTrue(eff.valid, eff.problems)
        self.assertEqual((eff.value["preset"], {i["id"]: i["weight"] for i in eff.value["items"]}["size"]), ("agentive:rubric@2", 20))
        self.assertEqual(self.engine("update", "--confirm-all")[2], [])           # idempotent
        self.commit("rubric@2")                                                   # E2E-09: an unrelated commit, then update writes nothing
        self.put("notes/today.txt", "a note\n")                               # not evidence: nothing to re-derive
        self.commit("unrelated")
        self.assertEqual(self.engine("update", "--confirm-all")[2], [])
        self.assertEqual(subprocess.run(["git", "-C", self.repo, "status", "--porcelain"], capture_output=True, text=True).stdout, "")
        # a deliberate pin to @1 is kept by later updates and scores with @1
        self.put(self.OVERRIDE, "extends: \"agentive:rubric@1\"\n")
        rc, out, changed = self.engine("update", "--confirm-all")
        self.assertEqual((rc, changed), (0, []), out)
        self.assertEqual(self.read(self.OVERRIDE), "extends: \"agentive:rubric@1\"\n")
        self.assertNotIn("AGENTIVE_REPORT rubric:", out)
        eff = cfg.effective_rubric(src)
        self.assertTrue(eff.valid, eff.problems)
        self.assertEqual(eff.value["preset"], "agentive:rubric@1")
        self.assert_contracts()

    def test_e2e_13_update_without_override_reports_how_to_stay_on_rubric1(self):
        self._old_install()
        rc, out, changed = self.engine("update", "--confirm-all")
        self.assertEqual(rc, 0, out)
        self.assertNotIn(self.OVERRIDE, changed)
        self.assertFalse(self.exists(self.OVERRIDE))
        rep = [ln for ln in out.splitlines() if ln.startswith("AGENTIVE_REPORT rubric:")]
        self.assertEqual(len(rep), 2, out)
        self.assertIn('to stay on agentive:rubric@1, set extends: "agentive:rubric@1" in .agents/custom/.agents/rubric.yaml', rep[1])


class E2E09RegionTest(Base):
    """REGIONNOSHA (E2E-09, user 2026-10-30 'B안으로 확정'): managed regions keep only the constant notice line, the manifest holds
    no verified_at / verified_commit, an update with nothing to change writes nothing, and the stale-line check stores no sha."""
    SHARED = ("AGENTS.md", "CLAUDE.md", "code_review.md", ".gitignore")

    def status(self):
        return subprocess.run(["git", "-C", self.repo, "status", "--porcelain"], capture_output=True, text=True).stdout

    def installed(self):
        self.git("init", "-q")
        self.put("package.json", json.dumps({"scripts": {"test": "node --test"}}))
        self.put("docs/architecture.md", "# architecture\n")
        rc, out, _ = self.engine("init", "--no-managed-review-copy")
        self.assertEqual(rc, 0, out)
        self.commit("agentive")

    def test_e2e_09_unrelated_commit_then_update_leaves_the_tree_clean(self):
        self.installed()
        for rel in self.SHARED:
            text = self.read(rel)
            self.assertIn(regions.NOTICE, text, rel)
            self.assertNotIn("Verified ", text, rel)
        for key, rec in self.manifest()["generated"].items():
            self.assertFalse({"verified_at", "verified_commit"} & set(rec), key)
        self.put("notes/today.txt", "a note\n")                    # an unrelated commit (no evidence file)
        self.commit("unrelated")
        rc, out, changed = self.engine("update")
        self.assertEqual((rc, changed), (0, []), out)
        self.assertEqual(self.status(), "")
        head = subprocess.run(["git", "-C", self.repo, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        sc = json.loads(self.read(".agents/state/drift-report.json"))["stale_check"]
        self.assertEqual((sc["status"], sc["until"]), ("done", head))     # the last check lives in the untracked report only
        self.put("notes/today.txt", "another note\n")
        self.commit("unrelated 2")
        self.assertEqual(self.engine("update")[2], [])
        self.assertEqual(json.loads(self.read(".agents/state/drift-report.json"))["stale_check"]["since"], head)
        self.assertEqual(self.status(), "")
        self.assert_contracts()

    def test_e2e_09_dated_line_of_an_older_version_is_replaced_once(self):
        self.installed()
        notice = "<!-- " + regions.NOTICE + " -->"
        agents = self.read("AGENTS.md")
        self.put("AGENTS.md", agents.replace(notice, "<!-- Verified 2026-10-28 against 0123abc. " + regions.NOTICE + " -->", 1))
        man = self.read(".agents/manifest.yaml")
        self.put(".agents/manifest.yaml", man.replace("    kind: region\n", "    kind: region\n    verified_at: 2026-10-28\n    verified_commit: 0123abc\n"))
        self.commit("an install of an older version")
        rc, out, changed = self.engine("update")
        self.assertEqual(rc, 0, out)
        self.assertEqual(sorted(changed), [".agents/manifest.yaml", "AGENTS.md"], out)
        self.assertEqual(self.read("AGENTS.md"), agents)              # only the notice line changed; not a user edit
        self.assertNotIn("AGENTIVE_DRIFT", out)
        self.assertNotIn("verified_", self.read(".agents/manifest.yaml"))
        self.commit("migrated")
        self.assertEqual(self.engine("update")[2], [])
        self.assertEqual(self.status(), "")
        self.assert_contracts()

    def test_e2e_09_stale_check_reads_the_path_history_not_a_stored_sha(self):
        self.installed()
        self.put("docs/old.md", "# old\n")
        self.put("docs/gone.md", "# gone\n")
        self.commit("docs")
        text = self.read("AGENTS.md").replace("Worktrees: untracked files",
                                              "See docs/old.md, docs/gone.md and `docs/architecture.md`.\nWorktrees: untracked files", 1)
        self.put("AGENTS.md", text)                                    # a user line inside the region: the region is kept
        self.commit("my line")
        self.git("rm", "-q", "docs/architecture.md")
        self.commit("delete architecture")
        self.put("docs/architecture.md", "# architecture again\n")    # deleted and added again: not stale
        self.commit("re-add architecture")
        self.git("mv", "docs/old.md", "docs/new.md")
        self.commit("rename old")
        rename = subprocess.run(["git", "-C", self.repo, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        self.git("rm", "-q", "docs/gone.md")
        self.commit("delete gone")
        gone = subprocess.run(["git", "-C", self.repo, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        rc, out, changed = self.engine("update")
        self.assertEqual(rc, 0, out)
        rep = json.loads(self.read(".agents/state/drift-report.json"))
        self.assertEqual(rep["stale_check"]["status"], "done")
        stale = {s["named_path"]: s for s in rep.get("stale", [])}
        self.assertEqual(sorted(stale), ["docs/gone.md", "docs/old.md"], rep)
        self.assertEqual((stale["docs/old.md"]["change"], stale["docs/old.md"]["renamed_to"], stale["docs/old.md"]["commit"]),
                         ("renamed", "docs/new.md", rename))
        self.assertEqual((stale["docs/gone.md"]["change"], stale["docs/gone.md"]["commit"]), ("deleted", gone))
        self.assertTrue(all(s["resolution"] == "reported" for s in stale.values()))
        self.assertIn("See docs/old.md", self.read("AGENTS.md"))         # never deletes a human-written line
        self.assert_contracts()


class SettingsMergeTest(Base):
    """decisions.accepted[JSONMERGE] (key-level merge of .claude/settings.json) and [INITWRITE] (no confirmation, hooks smoke
    check, --no-hooks, hooks status report, written-but-not-effective items) and [NOMAP]."""

    PLUGIN_FIRST = {"extraKnownMarketplaces": {"agentive": {"source": {"source": "github", "repo": "xrpz/agentive"}}},
                    "enabledPlugins": {"agentive@agentive": True}}

    def init(self, *extra, env=None):
        return self.engine("init", "--no-managed-review-copy", *extra, env=env)

    def settings(self):
        return json.loads(self.read(".claude/settings.json"))

    def agentive_groups(self, doc, event):
        return [g for g in doc["hooks"][event] if any(".agents/hooks/" in " ".join(h.get("args", [])) for h in g["hooks"])]

    def test_plugin_installed_first_then_init_idempotent_and_non_owned_edit_is_not_drift(self):
        self.git("init", "-q")
        self.put(".claude/settings.json", json.dumps(self.PLUGIN_FIRST, indent=2) + "\n")
        rc, out, changed = self.init()
        self.assertEqual(rc, 0, out)
        s = self.settings()
        self.assertEqual(s["enabledPlugins"], self.PLUGIN_FIRST["enabledPlugins"])
        self.assertEqual(s["extraKnownMarketplaces"], self.PLUGIN_FIRST["extraKnownMarketplaces"])
        for k in ("hooks", "permissions", "sandbox", "plansDirectory"):
            self.assertIn(k, s)
        self.assertEqual(s["hooks"]["PreToolUse"][0]["hooks"][0]["onFailure"], "block")
        self.assertNotIn("AGENTIVE_DRIFT", out)
        self.assertIn("AGENTIVE_HOOKS tool=claude file=.claude/settings.json status=installed", out)
        self.assertIn("AGENTIVE_GUARD status=on", out)
        self.assertIn("event=Stop matcher=- runs=python3 ${CLAUDE_PROJECT_DIR}/.agents/hooks/stop-verify.py", out)
        rec = self.manifest()["generated"][".claude/settings.json"]
        self.assertEqual(rec["owned"], ["hooks", "permissions", "sandbox", "plansDirectory", "env"])
        self.assertNotIn("conflicts", self.manifest())
        self.assert_contracts()
        before = self.read(".claude/settings.json")
        rc, out, changed = self.engine("update")
        self.assertEqual((rc, changed), (0, []), out)
        self.assertNotIn("no longer generated", out)
        self.assertEqual(self.read(".claude/settings.json"), before)
        # a user edit to a key agentive does not own (and a new plugin entry) is not drift
        s["model"] = "opus"
        s["enabledPlugins"]["other@mkt"] = True
        self.put(".claude/settings.json", json.dumps(s, indent=2) + "\n")
        rc, out, changed = self.engine("update")
        self.assertEqual((rc, changed), (0, []), out)
        self.assertNotIn("AGENTIVE_DRIFT", out)
        rc, out, _ = self.engine("status")
        self.assertIn("AGENTIVE_STATUS summary modified=0", out)
        self.assertEqual(self.settings()["model"], "opus")

    def test_user_hook_and_permissions_kept_order_stable(self):
        self.git("init", "-q")
        mine = {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo mine"}]}
        self.put(".claude/settings.json", json.dumps({
            "hooks": {"PreToolUse": [mine], "Notification": [{"hooks": [{"type": "command", "command": "notify-send hi"}]}]},
            "permissions": {"allow": ["Bash(ls)"], "deny": ["Read(.env)", "Bash(rm -rf /tmp/x)"], "defaultMode": "acceptEdits"}}, indent=2))
        self.assertEqual(self.init()[0], 0)
        s = self.settings()
        self.assertEqual(s["hooks"]["PreToolUse"][0], mine)
        self.assertEqual(len(self.agentive_groups(s, "PreToolUse")), 3)
        self.assertEqual(s["hooks"]["Notification"][0]["hooks"][0]["command"], "notify-send hi")
        self.assertEqual(s["permissions"]["allow"], ["Bash(ls)"])
        self.assertEqual(s["permissions"]["defaultMode"], "acceptEdits")
        self.assertEqual(s["permissions"]["deny"][:2], ["Read(.env)", "Bash(rm -rf /tmp/x)"])
        self.assertEqual(s["permissions"]["deny"].count("Read(.env)"), 1)
        self.assertEqual(len(s["permissions"]["deny"]), len(set(s["permissions"]["deny"])))
        before = self.read(".claude/settings.json")
        self.assertEqual(self.engine("update")[2], [])
        self.assertEqual(self.read(".claude/settings.json"), before)
        # the user adds a group after ours and a handler inside one of ours; the next run keeps both, replaces only agentive handlers
        s["hooks"]["PreToolUse"].append({"matcher": "Write", "hooks": [{"type": "command", "command": "echo later"}]})
        s["hooks"]["Stop"].insert(0, {"hooks": [{"type": "command", "command": "echo stopping"}]})
        self.put(".claude/settings.json", json.dumps(s, indent=2) + "\n")
        self.assertEqual(self.engine("update")[0], 0)
        s2 = self.settings()
        self.assertEqual(s2["hooks"]["PreToolUse"][-1]["hooks"][0]["command"], "echo later")
        self.assertEqual(s2["hooks"]["Stop"][0]["hooks"][0]["command"], "echo stopping")
        self.assertEqual(len(self.agentive_groups(s2, "Stop")), 1)
        self.assertEqual(len(self.agentive_groups(s2, "PreToolUse")), 3)

    def test_scalar_conflict_keeps_existing_value_and_is_recorded(self):
        self.git("init", "-q")
        self.put(".claude/settings.json", json.dumps({"sandbox": {"enabled": False}, "plansDirectory": "plans"}))
        rc, out, _ = self.init()
        self.assertEqual(rc, 0, out)
        s = self.settings()
        self.assertEqual((s["sandbox"]["enabled"], s["plansDirectory"]), (False, "plans"))
        self.assertIn("credentials", s["sandbox"])          # absent sub-keys are still added
        conf = {c["key"]: c for c in self.manifest()["conflicts"] if c["strategy"] == "keep_existing_value"}
        self.assertEqual(set(conf), {"sandbox.enabled", "plansDirectory"})
        self.assertEqual((conf["plansDirectory"]["existing"], conf["plansDirectory"]["proposed"]), ("plans", "docs/plans"))
        self.assertIn("AGENTIVE_CONFLICT path=.claude/settings.json key=sandbox.enabled kept=false agentive=true", out)
        self.assert_contracts()

    def test_json_with_comments_is_not_written_and_reported(self):
        self.git("init", "-q")
        text = '// my notes\n{"enabledPlugins": {"a@b": true}}\n'
        self.put(".claude/settings.json", text)
        rc, out, changed = self.init()
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.read(".claude/settings.json"), text)
        self.assertNotIn(".claude/settings.json", changed)
        self.assertIn("AGENTIVE_WARN unparsable_json .claude/settings.json", out)
        self.assertRegex(out, r"AGENTIVE_HOOKS tool=claude file=\.claude/settings\.json status=off reason=unparsable_json; NEXT=fix the JSON syntax")
        self.assertIn("AGENTIVE_GUARD status=on", out)       # the Codex hooks.json still carries the guard
        self.assertTrue(self.exists(".agents/hooks/stop-verify.py"))
        self.assertNotIn(".claude/settings.json", self.manifest()["generated"])
        self.assert_contracts()
        self.put(".claude/settings.json", '{"enabledPlugins": {"a@b": true}}\n')       # the user fixes it
        rc, out, changed = self.engine("update")
        self.assertIn(".claude/settings.json", changed)
        self.assertEqual(self.settings()["enabledPlugins"], {"a@b": True})
        rep = json.loads(self.read(".agents/state/drift-report.json"))
        self.assertNotIn("errors", rep)

    def test_unparsable_error_in_drift_report_on_update(self):
        self.git("init", "-q")
        self.assertEqual(self.init()[0], 0)
        self.put(".claude/settings.json", self.read(".claude/settings.json").replace("{", "{ // c", 1))
        rc, out, changed = self.engine("update")
        self.assertNotIn(".claude/settings.json", changed)
        rep = json.loads(self.read(".agents/state/drift-report.json"))
        self.assertEqual([e["code"] for e in rep["errors"]], ["unparsable_json"])
        self.assert_contracts()

    def test_edited_agentive_handler_is_drift_and_accept_restores_it(self):
        self.git("init", "-q")
        self.assertEqual(self.init()[0], 0)
        s = self.settings()
        s["hooks"]["Stop"][0]["hooks"][0]["timeout"] = 5
        s["model"] = "opus"
        self.put(".claude/settings.json", json.dumps(s, indent=2) + "\n")
        rc, out, changed = self.engine("update")
        self.assertIn("AGENTIVE_DRIFT id=.claude/settings.json reason=customized", out)
        self.assertEqual(self.settings()["hooks"]["Stop"][0]["hooks"][0]["timeout"], 5)
        self.assertIn("AGENTIVE_HOOKS tool=claude file=.claude/settings.json status=installed_modified", out)
        self.assert_contracts()
        rc, out, changed = self.engine("update", "--accept", ".claude/settings.json")
        s = self.settings()
        self.assertEqual(s["hooks"]["Stop"][0]["hooks"][0]["timeout"], 1800)
        self.assertEqual(s["model"], "opus")
        self.assertEqual(self.engine("update")[2], [])

    def test_whole_file_record_of_version_0_1_0_is_migrated_not_reported_as_drift(self):
        self.git("init", "-q")
        self.assertEqual(self.init()[0], 0)
        s = self.settings()
        s["hooks"]["Stop"][0]["hooks"][0]["timeout"] = 5          # differs from the template, but the old record covers the whole file
        self.put(".claude/settings.json", json.dumps(s, indent=2) + "\n")
        m = self.manifest()
        from agentive import common
        m["generated"][".claude/settings.json"] = {"kind": "file", "framework_version": "0.1.0",
                                                    "sha256": common.sha256_file(os.path.join(self.repo, ".claude", "settings.json"))}
        self.put(".agents/manifest.yaml", "# agentive manifest\n" + ayaml.dumps(m))
        rc, out, changed = self.engine("update")
        self.assertNotIn("AGENTIVE_DRIFT", out)
        self.assertEqual(self.settings()["hooks"]["Stop"][0]["hooks"][0]["timeout"], 1800)
        self.assertIn("owned", self.manifest()["generated"][".claude/settings.json"])
        self.assert_contracts()

    def test_agentive_hooks_without_baseline_are_no_baseline_drift(self):
        self.git("init", "-q")
        self.assertEqual(self.init()[0], 0)
        edited = self.settings()
        edited["hooks"]["Stop"][0]["hooks"][0]["timeout"] = 5
        os.unlink(os.path.join(self.repo, ".agents", "manifest.yaml"))
        self.put(".claude/settings.json", json.dumps(edited, indent=2) + "\n")
        rc, out, _ = self.init()
        self.assertIn("AGENTIVE_DRIFT id=.claude/settings.json reason=no_baseline", out)
        self.assertTrue(any(c["strategy"] == "keep_existing_propose_diff" and c["path"] == ".claude/settings.json" for c in self.manifest()["conflicts"]))
        self.assertEqual(self.settings()["hooks"]["Stop"][0]["hooks"][0]["timeout"], 5)
        self.assert_contracts()

    def test_smoke_check_failure_blocks_settings_json_only(self):
        self.git("init", "-q")
        self.assertEqual(self.init()[0], 0)
        self.put(".agents/hooks/stop-verify.py", "import sys\nsys.exit(3)\n")
        os.unlink(os.path.join(self.repo, ".claude", "settings.json"))
        rc, out, changed = self.engine("update", "--accept", ".claude/settings.json")
        self.assertEqual(rc, 0, out)
        self.assertFalse(self.exists(".claude/settings.json"))
        self.assertIn("AGENTIVE_WARN hook_smoke_failed .claude/settings.json", out)
        self.assertIn(".agents/hooks/stop-verify.py (Stop) exited 3", out)
        self.assertRegex(out, r"AGENTIVE_HOOKS tool=claude file=\.claude/settings\.json status=off reason=hook_smoke_failed; NEXT=fix the failing hook script")
        rep = json.loads(self.read(".agents/state/drift-report.json"))
        self.assertEqual([e["code"] for e in rep["errors"]], ["hook_smoke_failed"])
        self.assert_contracts()

    def test_no_hooks_flag_skips_hooks_and_wiring_only(self):
        self.git("init", "-q")
        rc, out, changed = self.init("--no-hooks")
        self.assertEqual(rc, 0, out)
        for rel in (".claude/settings.json", ".codex/hooks.json", ".agents/hooks/stop-verify.py"):
            self.assertFalse(self.exists(rel), rel)
        for rel in (".agents/bin/verb", ".agents/guard.yaml", "AGENTS.md", ".codex/config.toml", ".github/workflows/agentive-lint.yml"):
            self.assertTrue(self.exists(rel), rel)
        self.assertIn("AGENTIVE_SKIPPED path=.claude/settings.json reason=--no-hooks", out)
        self.assertIn("AGENTIVE_HOOKS tool=claude file=.claude/settings.json status=off reason=no_hooks_flag", out)
        self.assertIn("AGENTIVE_GUARD status=off", out)
        self.assert_contracts()
        rc, out, changed = self.engine("update")
        self.assertIn(".claude/settings.json", changed)
        self.assertIn("AGENTIVE_GUARD status=on", out)
        # --no-hooks on a repo that has hooks leaves them as they are
        before = self.read(".claude/settings.json")
        self.assertEqual(self.engine("update", "--no-hooks")[2], [])
        self.assertEqual(self.read(".claude/settings.json"), before)

    def test_interactive_writes_confirm_scope_files_without_asking(self):
        self.git("init", "-q")
        rc, out, changed = self.init()
        self.assertEqual(rc, 0, out)
        self.assertNotIn("AGENTIVE_CONFIRM", out)
        for rel in (".claude/settings.json", ".codex/hooks.json", ".codex/config.toml", ".agents/hooks/stop-verify.py", ".agents/guard.yaml",
                    ".github/workflows/agentive-lint.yml", ".agents/framework.yaml"):
            self.assertIn(rel, changed + [".agents/framework.yaml"])
            self.assertTrue(self.exists(rel), rel)
        pc = json.loads(self.read(".agents/state/pending-confirmation.json"))
        self.assertEqual([f["reason"] for f in pc["files"]], ["codex_hooks_untrusted"])
        self.assertIn("AGENTIVE_NOT_EFFECTIVE path=.codex/hooks.json reason=codex_hooks_untrusted", out)
        self.assertIn("AGENTIVE_REVERT git clean -fd -- ", out)
        self.assert_contracts()
        rc, out, changed = self.engine("update")
        self.assertFalse(self.exists(".agents/state/pending-confirmation.json"))      # recomputed: nothing new was written

    def test_headless_writes_the_same_files_and_records_not_yet_effective(self):
        self.git("init", "-q")
        rc, out, changed_h = self.init(env=dict(ENV, AGENTIVE_HEADLESS="1"))
        self.assertEqual(rc, 0, out)
        self.assertNotIn("AGENTIVE_CONFIRM", out)
        self.assertTrue(self.exists(".claude/settings.json"))
        self.assertTrue(self.exists(".agents/hooks/stop-verify.py"))
        pc = json.loads(self.read(".agents/state/pending-confirmation.json"))
        self.assertEqual({f["reason"] for f in pc["files"]}, {"untrusted_folder", "codex_hooks_untrusted"})
        self.assertEqual(pc["command"], "/agentive:init")
        self.assertIn("AGENTIVE_NOT_EFFECTIVE path=.claude/settings.json reason=untrusted_folder", out)
        self.assert_contracts()
        other = tempfile.mkdtemp(prefix="agentive-engine-")
        self.addCleanup(shutil.rmtree, other, True)
        subprocess.run(["git", "-C", other, "init", "-q"], check=True)
        r = subprocess.run([sys.executable, ENGINE, "init", "--no-managed-review-copy", "--repo", other, "--today", TODAY, "--values", self.values],
                           capture_output=True, text=True, env=ENV)
        changed_i = r.stdout.split("AGENTIVE_CHANGED:\n", 1)[1].split()
        self.assertEqual(sorted(changed_h), sorted(changed_i))
        # headless update does not skip either
        self.assertEqual(self.engine("update", env=dict(ENV, AGENTIVE_HEADLESS="1"))[0], 0)

    def test_codex_dir_write_failure_is_recorded_not_fatal(self):
        self.git("init", "-q")
        self.put(".codex", "a file where the directory should be\n")      # makes every .codex/ write fail, as a read-only .codex/ does
        rc, out, changed = self.init()
        self.assertEqual(rc, 0, out)
        self.assertIn("AGENTIVE_WARN .codex/hooks.json could not be written", out)
        pc = json.loads(self.read(".agents/state/pending-confirmation.json"))
        self.assertIn(("write_failed", ".codex/hooks.json"), {(f["reason"], f["path"]) for f in pc["files"]})
        self.assertTrue(self.exists(".claude/settings.json"))
        self.assert_contracts()

    def test_codex_hooks_json_is_merged_by_key(self):
        self.git("init", "-q")
        mine = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo codex-mine"}]}]}, "extra": 1}
        self.put(".codex/hooks.json", json.dumps(mine))
        self.assertEqual(self.init()[0], 0)
        d = json.loads(self.read(".codex/hooks.json"))
        self.assertEqual(d["extra"], 1)
        self.assertEqual(d["hooks"]["Stop"][0]["hooks"][0]["command"], "echo codex-mine")
        self.assertTrue(any(".agents/hooks/stop-verify.py" in h["command"] for g in d["hooks"]["Stop"] for h in g["hooks"]))
        self.assertEqual(self.engine("update")[2], [])

    def test_existing_codex_config_toml_stays_a_d10_conflict_with_a_clear_warning(self):
        self.git("init", "-q")
        self.put(".codex/config.toml", 'model = "x"\n')
        rc, out, _ = self.init()
        self.assertEqual(self.read(".codex/config.toml"), 'model = "x"\n')
        self.assertIn("AGENTIVE_DRIFT id=.codex/config.toml reason=no_baseline", out)
        self.assertIn("AGENTIVE_WARN .codex/config.toml existed before agentive and TOML is not merged by key", out)

    def test_no_generated_structure_map_and_no_empty_docs_dirs(self):
        self.git("init", "-q")
        self.put("package.json", json.dumps({"scripts": {"test": "node --test"}}))
        self.put("README.md", "# demo\n")
        os.makedirs(os.path.join(self.repo, "src"))
        self.put("src/a.js", "x\n")
        os.makedirs(os.path.join(self.repo, "test"))
        self.put("test/a.test.js", "x\n")
        self.assertEqual(self.init()[0], 0)
        agents = self.read("AGENTS.md")
        self.assertNotIn("Source:", agents)
        self.assertIn("Tests: test.", agents)
        self.assertIn("Docs (pointers to files that exist): README.md, docs/adr/.", agents)
        for d in ("system", "features", "guides"):
            self.assertFalse(self.exists(f"docs/{d}"), d)
        self.assertTrue(self.exists("docs/adr/0000-template.md"))

    def test_go_test_files_count_as_test_locations(self):
        self.put("go.mod", "module example.com/m\n\ngo 1.22\n")
        self.put("calc/calc.go", "package calc\n")
        self.put("calc/calc_test.go", "package calc\n")
        self.put("cmd/app/main.go", "package main\n")
        self.put("vendor/x/x_test.go", "package x\n")
        tests = evidence.scan(self.repo)["paths"]["tests"]
        self.assertEqual([t["path"] for t in tests], ["calc"])
        self.git("init", "-q")
        self.assertEqual(self.init()[0], 0)
        self.assertIn("Tests: calc.", self.read("AGENTS.md"))
        self.assertEqual(self.manifest()["paths"]["tests"], [{"path": "calc", "proof": "exists"}])


class DefaultsTest(Base):
    """DEFAULTS (V1-18) and PENDINGNOTICE (V2-23): init writes no decisions.yaml, review.auto_merge defaults live in code,
    a v0.1.0 decisions.yaml is migrated by update, and a pending required verb is reported with its two exits."""
    DEC = ".agents/decisions.yaml"
    ALL_VERBS = {"tools": {}, "verbs": {v: {"cmd": "true", "tier": t, "proof": {"pass_run_id": "x", "at": "2026-10-28T00:00:00Z"},
                                           "evidence": {"rank": 1, "source": "--values", "files": []}}
                                       for v, t in (("setup", "outer"), ("build", "outer"), ("lint", "inner"), ("typecheck", "inner"), ("test", "inner"))}}

    def init(self, *extra):
        self.git("init", "-q")
        self.put("go.mod", "module x\n")
        return self.engine("init", "--no-managed-review-copy", "--confirm-all", *extra)

    def decisions(self, u1, u2=None, u6="agentive:rubric@1"):
        u2 = u2 or {"value": "B", "confirm_scope": ["hooks", "permissions", "ci", "claude_settings", "codex_dir", "review_config"],
                    "non_interactive": "pr_only_tier2", "decided_by": "user", "decided_at": "2026-10-14"}
        doc = {"U1": {"value": u1, "decided_by": "user", "decided_at": "2026-10-14"}, "U2": u2, "U6": {"value": u6, "decided_by": "user", "decided_at": "2026-10-14"}}
        return json.dumps(doc)          # JSON is YAML

    def test_init_writes_no_decisions_and_no_override(self):
        rc, out, _ = self.init()
        self.assertEqual(rc, 0, out)
        self.assertFalse(self.exists(self.DEC))
        self.assertNotIn("auto_merge", self.read(".agents/framework.yaml")) if self.exists(".agents/framework.yaml") else None
        line = next(ln for ln in out.splitlines() if ln.startswith("AGENTIVE_REPORT auto-merge:"))
        self.assertIn("review.auto_merge=low_risk, framework default", line)
        self.assertIn("tier 0 on, tier 1 shadow", line)
        self.assertIn("'Allow auto-merge'", line)
        self.assertIn("settings --effective", line)
        self.assertIn("a human confirms that commit", line)

    def test_e2e_19_init_reports_required_checks_and_not_required(self):
        """E2E-19 (EFFECTIVECHECKS): a pending verb of the default list is not required (info line), auto-merge stays available."""
        rc, out, _ = self.init()                     # go.mod gives no typecheck
        self.assertEqual(rc, 0, out)
        line = next(ln for ln in out.splitlines() if ln.startswith("AGENTIVE_REPORT required-checks:"))
        self.assertIn("Not required (no command found):", line)
        self.assertIn("typecheck", line.split("Not required")[1])
        self.assertIn("run /agentive:update to require", line)
        self.assertNotIn("auto-merge unavailable", out)
        self.assertFalse(self.exists(".agents/framework.yaml") and "require_checks" in self.read(".agents/framework.yaml"),
                         "nothing is written: the list is computed at gate time")

    def test_e2e_20_init_reports_dependencies_and_package_manager_cooldowns(self):
        """E2E-20 (DEPHUMAN): one AGENTIVE_REPORT dependencies line: dependency changes go to a human, and the cooldown settings
        (the ones of the detected ecosystem, all three when none matches); agentive writes none of them."""
        import apply as engine_apply
        rc, out, _ = self.init()                     # go.mod: no npm, pnpm or uv evidence -> all three named
        self.assertEqual(rc, 0, out)
        lines = [ln for ln in out.splitlines() if ln.startswith("AGENTIVE_REPORT dependencies:")]
        self.assertEqual(len(lines), 1, out)
        for k in ("goes to a human (tier 2, never auto-merged)", "min-release-age", "minimumReleaseAge", "exclude-newer", "agentive writes none"):
            self.assertIn(k, lines[0])
        self.assertNotIn("min-release-age", self.read("go.mod"))
        uv = engine_apply.dependency_report(["Python (uv)"])
        self.assertIn('exclude-newer = "7 days"', uv)
        self.assertNotIn("min-release-age", uv)
        self.assertIn("minimumReleaseAge", engine_apply.dependency_report(["JavaScript (Node.js, pnpm)"]))
        self.assertNotIn("dependency review needs GitHub Team with Code Security, so those gate parts", out)

    def test_report_names_pending_required_checks_and_both_exits(self):
        self.assertEqual(self.init()[0], 0)          # E2E-19: only a pending verb the user named in require_checks blocks
        self.put(".agents/framework.yaml", "review:\n  pipeline:\n    stages:\n      RV1:\n        require_checks: [lint, typecheck, test]\n")
        rc, out, _ = self.engine("update", "--confirm-all")
        self.assertEqual(rc, 0, out)
        self.assertIn("but you listed", next(ln for ln in out.splitlines() if ln.startswith("AGENTIVE_REPORT required-checks:")))
        lines = [ln for ln in out.splitlines() if ln.startswith("AGENTIVE_REPORT auto-merge unavailable:")]
        self.assertEqual(len(lines), 2, out)
        self.assertIn("typecheck has no command", lines[0])
        self.assertIn("give the command", lines[0])
        self.assertIn("remove it from review.pipeline.stages.RV1.require_checks", lines[1])
        self.assertIn("never pick a tool", lines[1])

    def test_no_unavailable_line_when_every_required_verb_has_a_command(self):
        self.write_values(self.ALL_VERBS)
        rc, out, _ = self.init()
        self.assertEqual(rc, 0, out)
        self.assertNotIn("auto-merge unavailable", out)

    def test_removing_the_verb_from_require_checks_is_the_second_exit(self):
        self.assertEqual(self.init()[0], 0)
        self.put(".agents/framework.yaml", "review:\n  pipeline:\n    stages:\n      RV1:\n        require_checks: [lint, test]\n")
        rc, out, _ = self.engine("update", "--confirm-all")
        self.assertEqual(rc, 0, out)
        self.assertNotIn("auto-merge unavailable", out)

    def track(self, rel, text, sha=None):
        """Record `rel` in manifest.generated the way a v0.1.0 init did (init no longer writes decisions.yaml)."""
        import hashlib
        sha = sha or hashlib.sha256(text.encode()).hexdigest()
        m = self.read(".agents/manifest.yaml")
        entry = f'generated:\n  {rel}:\n    kind: file\n    sha256: "{sha}"\n    framework_version: "0.1.0"\n'
        self.put(".agents/manifest.yaml", m.replace("generated:\n", entry, 1))

    def migrated(self, decisions, framework=None):
        self.assertEqual(self.init()[0], 0)
        self.commit("init")
        self.put(self.DEC, decisions)
        self.track(self.DEC, decisions)
        if framework is not None:
            self.put(".agents/framework.yaml", framework)
        return self.engine("update", "--confirm-all")

    def test_migration_u1_a_becomes_off(self):
        rc, out, changed = self.migrated(self.decisions("A"))
        self.assertEqual(rc, 0, out)
        self.assertIn("AGENTIVE_MIGRATED .agents/decisions.yaml -> .agents/framework.yaml: review.auto_merge=off", out)
        self.assertIn("auto_merge: off", self.read(".agents/framework.yaml"))
        self.assertFalse(self.exists(self.DEC), "update removes the migrated decisions.yaml")
        rc, out, changed = self.engine("update", "--confirm-all")
        self.assertEqual((rc, changed), (0, []), out)               # idempotent
        self.assertNotIn("AGENTIVE_MIGRATED", out)

    def test_migration_keeps_a_modified_decisions_file_and_says_so(self):
        self.assertEqual(self.init()[0], 0)
        self.commit("init")
        self.put(self.DEC, self.decisions("A"))
        self.track(self.DEC, "what init wrote")           # the recorded hash differs: the user edited the file
        rc, out, _ = self.engine("update", "--confirm-all")
        self.assertEqual(rc, 0, out)
        self.assertTrue(self.exists(self.DEC))
        self.assertIn(".agents/decisions.yaml is no longer generated and was modified; kept", out)
        self.assertIn("auto_merge: off", self.read(".agents/framework.yaml"))

    def test_migration_u1_c_and_b_equal_the_default_and_are_dropped(self):
        for u1 in ("C", "B"):
            shutil.rmtree(self.repo)
            os.makedirs(self.repo)
            rc, out, _ = self.migrated(self.decisions(u1))
            self.assertEqual(rc, 0, out)
            self.assertIn("review.auto_merge=unchanged (the framework default applies)", out)
            self.assertNotIn("auto_merge", self.read(".agents/framework.yaml") if self.exists(".agents/framework.yaml") else "")
            self.assertFalse(self.exists(self.DEC))

    def test_migration_c_with_tier1_on_becomes_low_and_medium_risk(self):
        rc, out, _ = self.migrated(self.decisions("C"), "review:\n  pipeline:\n    auto_merge:\n      tier1: on\n")
        self.assertEqual(rc, 0, out)
        self.assertIn("review.auto_merge=low_and_medium_risk", out)
        text = self.read(".agents/framework.yaml")
        self.assertIn("auto_merge: low_and_medium_risk", text)
        self.assertIn("review.pipeline.auto_merge.tier1 is ignored", out)

    def test_migration_does_not_overwrite_an_existing_override(self):
        rc, out, _ = self.migrated(self.decisions("A"), "review:\n  auto_merge: dry_run\n")
        self.assertEqual(rc, 0, out)
        self.assertIn("auto_merge: dry_run", self.read(".agents/framework.yaml"))
        self.assertIn("the decisions.yaml value was not applied", out)

    def test_migration_reports_a_differing_u2_and_u6(self):
        u2 = {"value": "A", "confirm_scope": ["hooks"], "non_interactive": "pr_only_tier2"}
        rc, out, _ = self.migrated(self.decisions("C", u2=u2, u6="custom:rubric@9"))
        self.assertEqual(rc, 0, out)
        self.assertIn("U2 is a framework constant now", out)
        self.assertIn("the rubric preset is the name in .agents/rubric.yaml", out)

    def test_settings_effective_shows_default_override_and_reason(self):
        self.assertEqual(self.init()[0], 0)
        lint = os.path.join(self.repo, ".agents", "bin", "agentive-lint")
        r = subprocess.run([lint, "settings", "--effective"], cwd=self.repo, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("review.auto_merge: low_risk   [framework default]", r.stdout)
        # E2E-19: a pending verb of the default list is not required (info), auto-merge stays available
        self.assertIn("in force: lint, test; not required: typecheck (no command in this repo; info, not blocking)", r.stdout)
        self.assertNotIn("unavailable:", r.stdout)
        self.put(".agents/framework.yaml", "review:\n  pipeline:\n    stages:\n      RV1:\n        require_checks: [lint, typecheck, test]\n")
        r = subprocess.run([lint, "settings", "--effective"], cwd=self.repo, capture_output=True, text=True)
        self.assertIn("unavailable: typecheck has no command", r.stdout)
        self.assertIn("remove it from review.pipeline.stages.RV1.require_checks", r.stdout)
        self.assertIn("require_checks: lint, typecheck, test   [.agents/framework.yaml]", r.stdout)
        self.put(".agents/framework.yaml", "review:\n  auto_merge: low_and_medium_risk\n")
        r = subprocess.run([lint, "settings", "--effective"], cwd=self.repo, capture_output=True, text=True)
        self.assertIn("review.auto_merge: low_and_medium_risk   [.agents/framework.yaml]", r.stdout)

    def test_lint_scope_is_kept_across_update(self):
        """A hand-set verbs.lint.file_args / exts survives update when the command is unchanged [설계 판단]."""
        self.put(".golangci.yml", "linters: {}\n")
        self.assertEqual(self.init()[0], 0)
        lint = self.manifest()["verbs"]["lint"]
        self.assertEqual((lint["cmd"], lint["file_args"], lint["exts"]), ("golangci-lint run", False, [".go"]))
        m = self.read(".agents/manifest.yaml").replace("file_args: false", "file_args: true").replace("      - .go\n    file_args", "      - .go\n      - .mod\n    file_args")
        self.put(".agents/manifest.yaml", m)
        self.assertEqual(self.engine("update", "--confirm-all")[0], 0)
        lint = self.manifest()["verbs"]["lint"]
        self.assertIs(lint["file_args"], True)
        self.assertEqual(lint["exts"], [".go", ".mod"])

    def test_lint_scope_for_a_command_the_agent_gave(self):
        """--values lint: file_args / exts derived from the command when left out; an explicit value stays [설계 판단]."""
        proof = {"pass_run_id": "x", "at": "2026-10-28T00:00:00Z"}
        ev = {"rank": 1, "source": "--values", "files": []}
        self.git("init", "-q")
        self.put("package.json", "{}")
        for cmd, extra, want in (("npx eslint src", {}, True), ("tsc --noEmit", {}, False), ("some-new-linter", {}, False),
                                 ("npx eslint src", {"file_args": False}, False)):
            self.write_values({"tools": {}, "verbs": {"lint": dict({"cmd": cmd, "tier": "inner", "proof": proof, "evidence": ev}, **extra)}})
            rc, out, _ = self.engine("update" if self.exists(".agents/manifest.yaml") else "init", "--no-managed-review-copy", "--confirm-all")
            self.assertEqual(rc, 0, out)
            lint = self.manifest()["verbs"]["lint"]
            self.assertEqual((lint["cmd"], lint["file_args"]), (cmd, want), out[-800:])
            self.assertEqual(lint["exts"], [".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"])
