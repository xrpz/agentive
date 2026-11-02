# agentive-unit: selftest.sample_repo_tests
# design: spec.framework_repo.self_tests (sample_repo_tests, deterministic part), spec.init_apply.managed_regions, docs/IMPLEMENTATION.md §6, docs/impl/B.md §2 (engine CLI)
"""Deterministic init/apply on fixtures/{ts,py,go,java} through the engine CLI (no LLM, no network):
generated files present and valid, existing user files preserved, an idempotent re-run that changes nothing, init
refusing an initialized repo, and the installed review layer working on the result (agentive-lint, RV1-RV4 rules).
Precedents: OpenSpec runs init in a temporary directory and checks structure, re-run and preserved files [S133];
BMAD checks that a second setup changes nothing and leaves present paths alone [S134]."""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

import yaml  # dev-time only

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
FIXTURES = os.path.join(ROOT, "fixtures")
STACKS = ["ts", "py", "go", "java"]
PAYLOADS = {"claude": os.path.join(ROOT, "plugins", "claude-code", "agentive", "payload"),
            "codex": os.path.join(ROOT, "plugins", "codex", "agentive", "skills", "agentive-init", "payload")}
ENGINE = os.path.join(PAYLOADS["claude"], "engine", "apply.py")
TODAY = "2026-10-28"
USER_AGENTS = "# Team notes\n\nOur own line that agentive must keep.\n"
REGION = re.compile(r"\n*(<!--|#) AGENTIVE START (\S+)(?: -->)?\n.*?(<!--|#) AGENTIVE END \2(?: -->)?\n?", re.S)


def git(repo, *args):
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com", GIT_COMMITTER_NAME="t",
               GIT_COMMITTER_EMAIL="t@example.com", GIT_CONFIG_NOSYSTEM="1", HOME=repo)
    r = subprocess.run(["git", "-C", repo] + list(args), capture_output=True, text=True, env=env)
    if r.returncode != 0:
        raise AssertionError(f"git {args}: {r.stderr}")
    return r.stdout


def unescape(rel):
    return "/".join(("." + c[4:]) if c.startswith("dot-") else c for c in rel.split("/"))


def copy_fixture(stack, dest):
    src = os.path.join(FIXTURES, stack)
    for base, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if d not in ("node_modules", "__pycache__", ".gradle", "build", "dist")]
        for f in files:
            rel = os.path.relpath(os.path.join(base, f), src).replace(os.sep, "/")
            out = os.path.join(dest, unescape(rel))
            os.makedirs(os.path.dirname(out), exist_ok=True)
            shutil.copy2(os.path.join(base, f), out)


def snapshot(repo):
    out = {}
    for base, dirs, files in os.walk(repo):
        rel_base = os.path.relpath(base, repo).replace(os.sep, "/")
        dirs[:] = [d for d in dirs if not (d == ".git" and rel_base == ".") and not (rel_base == ".agents" and d == "state")]
        for f in files:
            p = os.path.join(base, f)
            rel = os.path.relpath(p, repo).replace(os.sep, "/")
            if os.path.islink(p):
                out[rel] = "link:" + os.readlink(p)
            else:
                with open(p, "rb") as fh:
                    out[rel] = hashlib.sha256(fh.read()).hexdigest()
    return out


def engine(payload, *args):
    r = subprocess.run([sys.executable, os.path.join(payload, "engine", "apply.py")] + list(args), capture_output=True, text=True,
                       env={k: v for k, v in os.environ.items() if k not in ("CI", "GITHUB_ACTIONS", "AGENTIVE_HEADLESS")})
    return r.returncode, r.stdout + r.stderr


def changed(out):
    lines = out.splitlines()
    if "AGENTIVE_CHANGED:" not in lines:
        raise AssertionError("no AGENTIVE_CHANGED: line\n" + out[-3000:])
    return [ln.strip() for ln in lines[lines.index("AGENTIVE_CHANGED:") + 1:] if ln.strip()]


def schemas():
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource
    d = os.path.join(ROOT, "design", "schemas")
    reg, by = Registry(), {}
    for f in sorted(os.listdir(d)):
        with open(os.path.join(d, f), encoding="utf-8") as fh:
            s = json.load(fh)
        by[f[:-len(".schema.json")]] = s
        reg = reg.with_resource(s["$id"], Resource.from_contents(s))
    return lambda name, inst: [f"{'/'.join(map(str, e.absolute_path))}: {e.message}" for e in
                               Draft202012Validator(by[name], registry=reg).iter_errors(inst)]


@unittest.skipUnless(os.path.isfile(ENGINE), "pending: B's engine (payload/engine/apply.py) is not built yet")
class InitApplyTest(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        cls.validate = staticmethod(schemas())
        with open(os.path.join(PAYLOADS["claude"], "index.json"), encoding="utf-8") as f:
            cls.index = json.load(f)

    def make(self, stack, user_agents=False):
        d = tempfile.mkdtemp(prefix=f"agentive-sample-{stack}-")
        self.addCleanup(shutil.rmtree, d, True)
        copy_fixture(stack, d)
        if user_agents:
            with open(os.path.join(d, "AGENTS.md"), "w", encoding="utf-8") as f:
                f.write(USER_AGENTS)
        git(d, "init", "-q", "-b", "main")
        git(d, "add", "-A")
        git(d, "commit", "-q", "-m", "fixture")
        return d

    def check_stack(self, stack, payload="claude", tool=None):
        tl = ["--tool", tool] if tool else []
        repo = self.make(stack, user_agents=(stack == "py"))
        before = snapshot(repo)
        with open(os.path.join(repo, ".gitignore"), encoding="utf-8") as f:
            gitignore_before = f.read()
        rc, out = engine(PAYLOADS[payload], "init", "--repo", repo, "--no-managed-review-copy", "--today", TODAY, *tl)
        self.assertEqual(rc, 0, out[-3000:])
        written = changed(out)
        self.assertTrue(written, "init wrote nothing")
        for p in written:
            self.assertTrue(os.path.lexists(os.path.join(repo, p.split()[-1])) or p.startswith("D "), p)
        # every unconditional payload entry is installed
        for e in self.index["entries"]:
            if e.get("when"):
                continue
            path = os.path.join(repo, e["install"])
            self.assertTrue(os.path.lexists(path), f"{stack}: {e['install']} ({e['unit']}) missing")
            if e.get("region"):
                with open(path, encoding="utf-8") as f:
                    text = f.read()
                self.assertIn(f"AGENTIVE START {e['region']}", text, e["install"])
                self.assertIn(f"AGENTIVE END {e['region']}", text, e["install"])
        # user files preserved: unchanged bytes, and shared files unchanged outside the managed regions
        after = snapshot(repo)
        for rel, digest in before.items():
            if rel in (".gitignore", "AGENTS.md"):
                continue
            self.assertEqual(after.get(rel), digest, f"{stack}: user file {rel} changed")
        with open(os.path.join(repo, ".gitignore"), encoding="utf-8") as f:
            self.assertEqual(REGION.sub("", f.read()).rstrip("\n"), gitignore_before.rstrip("\n"))
        if stack == "py":
            with open(os.path.join(repo, "AGENTS.md"), encoding="utf-8") as f:
                self.assertEqual(REGION.sub("", f.read()).rstrip("\n"), USER_AGENTS.rstrip("\n"))
        self.check_contracts(repo, stack)
        # idempotent re-run: nothing changes
        rc, out2 = engine(PAYLOADS[payload], "update", "--repo", repo, "--today", TODAY, *tl)
        self.assertEqual(rc, 0, out2[-3000:])
        self.assertEqual(changed(out2), [], f"{stack}: update after init changed files\n{out2[-2000:]}")
        self.assertEqual(snapshot(repo), after, f"{stack}: bytes changed on the re-run")
        # init on an initialized repo stops and points to update
        rc, out3 = engine(PAYLOADS[payload], "init", "--repo", repo, "--today", TODAY)
        self.assertEqual(rc, 1, out3[-2000:])
        self.assertIn("NEXT=", out3.strip().splitlines()[-1])
        return repo

    def check_contracts(self, repo, stack):
        def y(p):
            with open(os.path.join(repo, p), encoding="utf-8") as f:
                return yaml.safe_load(f)
        for path, schema in ((".agents/manifest.yaml", "manifest"), (".agents/rubric.yaml", "rubric"),
                             (".agents/compat.yaml", "version-compat"), (".agents/guard.yaml", "guard-rules")):
            if os.path.exists(os.path.join(repo, path)):
                self.assertEqual(self.validate(schema, y(path)), [], f"{stack}: {path}")
        with open(os.path.join(repo, ".agents/skills.lock"), encoding="utf-8") as f:
            self.assertEqual(self.validate("skills-lock", json.load(f)), [], f"{stack}: skills.lock")
        with open(os.path.join(repo, "code_review.md"), encoding="utf-8") as f:
            text = f.read()
        block = re.search(r"```yaml agentive-review-rules\n(.*?)```", text, re.S)
        self.assertIsNotNone(block, "code_review.md has no rules block")
        self.assertEqual(self.validate("review-rules", yaml.safe_load(block.group(1))), [], f"{stack}: code_review.md rules block")
        # DEFAULTS (V1-18): init writes no decisions file; defaults live in the framework code
        self.assertFalse(os.path.exists(os.path.join(repo, ".agents", "decisions.yaml")), f"{stack}: init wrote decisions.yaml")
        manifest = y(".agents/manifest.yaml")
        self.check_lint_scope(manifest, stack)
        for key in manifest.get("generated") or {}:
            self.assertTrue(os.path.lexists(os.path.join(repo, key.split("#")[0])), f"{stack}: manifest.generated {key} has no file")

    def check_lint_scope(self, manifest, stack):
        """Lint scope at init (V1-13 gap): exts and file_args come from the evidence-derived lint command. The ts, py and java
        fixtures name no linter (the lint slot is pending, nothing is written for it); go.mod + .golangci.yml give
        `golangci-lint run`, a whole-project command: file_args false, exts [.go]."""
        lint = manifest["verbs"]["lint"]
        if stack == "go":
            self.assertEqual(lint["cmd"], "golangci-lint run")
            self.assertIs(lint["file_args"], False)
            self.assertEqual(lint["exts"], [".go"])
        else:
            self.assertEqual(lint.get("status"), "pending", f"{stack}: lint slot")
            self.assertNotIn("file_args", lint)
            self.assertNotIn("exts", lint)

    def check_review_layer(self, repo, stack):
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "agentive layer")
        r = subprocess.run([os.path.join(repo, ".agents", "bin", "agentive-lint"), "ci"], cwd=repo, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, f"{stack}: agentive-lint ci on the installed layer\n{r.stdout}{r.stderr}")
        base = git(repo, "rev-parse", "HEAD").strip()
        git(repo, "checkout", "-q", "-b", "docs")
        with open(os.path.join(repo, "README.md"), "a", encoding="utf-8") as f:
            f.write("\nOne more sentence.\n")
        git(repo, "commit", "-q", "-am", "docs")
        head = git(repo, "rev-parse", "HEAD").strip()
        r = subprocess.run([os.path.join(repo, ".agents", "bin", "agentive-review"), "eval", "--base", base, "--head", head,
                            "--work", os.path.join(repo, ".agents", "state", "sample")], cwd=repo, capture_output=True, text=True)
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)          # rules (from the installed block) give tier 0 -> raise-only task
        self.assertIn("agent=raise-only", r.stdout)

    def test_ts(self):
        self.check_review_layer(self.check_stack("ts"), "ts")

    def test_py(self):
        self.check_review_layer(self.check_stack("py"), "py")

    def test_go(self):
        self.check_review_layer(self.check_stack("go"), "go")

    def test_java(self):
        self.check_review_layer(self.check_stack("java"), "java")

    def test_ci_tool_follows_the_init_tool(self):
        """CITOOL: a repo initialized by Codex gets the codex-action ai-review job and a codex reviewer eval; the layer still works."""
        repo = self.check_stack("py", payload="codex", tool="codex")
        def read(rel):
            with open(os.path.join(repo, rel), encoding="utf-8") as f:
                return f.read()
        wf = yaml.safe_load(read(".github/workflows/agentive-review.yml"))
        uses = [st.get("uses", "") for st in wf["jobs"]["ai-review"]["steps"]]
        self.assertIn("openai/codex-action@v1", uses)
        self.assertFalse([u for u in uses if "claude-code-action" in u])
        ev = read(".github/workflows/reviewer-eval.yml")
        self.assertIn("npm install -g @openai/codex", ev)
        self.assertEqual(yaml.safe_load(read(".agents/manifest.yaml"))["framework"]["init_tool"], "codex")
        self.check_review_layer(repo, "py-codex")

    def test_plugin_installed_first_keeps_enabled_plugins_and_installs_hooks(self):
        """`claude plugin install agentive@<name> --scope project` writes .claude/settings.json before /agentive:init (V1-01)."""
        repo = self.make("ts")
        pre = {"extraKnownMarketplaces": {"agentive": {"source": {"source": "github", "repo": "xrpz/agentive"}}}, "enabledPlugins": {"agentive@agentive": True}}
        os.makedirs(os.path.join(repo, ".claude"), exist_ok=True)
        with open(os.path.join(repo, ".claude", "settings.json"), "w", encoding="utf-8") as f:
            json.dump(pre, f, indent=2)
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "plugin installed")
        rc, out = engine(PAYLOADS["claude"], "init", "--repo", repo, "--no-managed-review-copy", "--today", TODAY)
        self.assertEqual(rc, 0, out[-3000:])
        with open(os.path.join(repo, ".claude", "settings.json"), encoding="utf-8") as f:
            s = json.load(f)
        self.assertEqual({k: s[k] for k in pre}, pre)
        self.assertIn("Stop", s["hooks"])
        self.assertIn("deny", s["permissions"])
        self.assertIn("AGENTIVE_GUARD status=on", out)
        self.assertNotIn("AGENTIVE_DRIFT", out)
        before = snapshot(repo)
        rc, out2 = engine(PAYLOADS["claude"], "update", "--repo", repo, "--today", TODAY)
        self.assertEqual(changed(out2), [], out2[-2000:])
        self.assertEqual(snapshot(repo), before)

    def test_no_generated_map_and_go_tests_found(self):
        repo = self.check_stack("go")
        with open(os.path.join(repo, "AGENTS.md"), encoding="utf-8") as f:
            text = f.read()
        self.assertIn("Tests: calc.", text)
        self.assertNotIn("Source:", text)
        for d in ("system", "features", "guides"):
            self.assertFalse(os.path.exists(os.path.join(repo, "docs", d)), d)
        self.assertTrue(os.path.isfile(os.path.join(repo, "docs", "adr", "0000-template.md")))

    def test_codex_payload_matches(self):
        """The Codex skill payload installs the same repo layer (one payload copy per Codex skill)."""
        a = self.check_stack("go", payload="claude")
        b = self.check_stack("go", payload="codex")
        sa, sb = snapshot(a), snapshot(b)
        self.assertEqual(sorted(sa), sorted(sb))


def _ruff():
    return shutil.which("ruff") or shutil.which("ruff", path=os.path.expanduser("~/.local/bin"))


@unittest.skipUnless(os.path.isfile(ENGINE), "pending: B's engine (payload/engine/apply.py) is not built yet")
@unittest.skipUnless(_ruff(), "ruff is not installed")
class InstalledLayerIsNotLintedTest(unittest.TestCase):
    """E2E-01 / E2E-04: a Python repo with [tool.ruff] gets `.agents/bin/verb verify` green right after init (the derived
    lint command keeps .agents, .claude and .codex out), and the PostToolUse lint runs ruff on the edited file only.
    The repo has no uv.lock, so the commands run without network (`ruff ...`, `python3 -m unittest`)."""

    def make(self):
        d = tempfile.mkdtemp(prefix="agentive-sample-ruff-")
        self.addCleanup(shutil.rmtree, d, True)
        files = {"pyproject.toml": "[project]\nname = \"shop\"\nversion = \"0.1.0\"\nrequires-python = \">=3.9\"\n\n"
                                   "[tool.ruff]\nline-length = 100\n",
                 "shop/__init__.py": "def total(prices):\n    return sum(prices)\n",
                 "tests/test_shop.py": "import unittest\n\nfrom shop import total\n\n\nclass T(unittest.TestCase):\n"
                                       "    def test_total(self):\n        self.assertEqual(total([1, 2]), 3)\n",
                 ".gitignore": "__pycache__/\n"}
        for rel, text in files.items():
            os.makedirs(os.path.dirname(os.path.join(d, rel)) or d, exist_ok=True)
            with open(os.path.join(d, rel), "w", encoding="utf-8") as f:
                f.write(text)
        git(d, "init", "-q", "-b", "main")
        git(d, "add", "-A")
        git(d, "commit", "-q", "-m", "app")
        return d

    def test_verify_is_green_right_after_init_and_the_edit_lint_takes_only_the_file(self):
        repo = self.make()
        rc, out = engine(PAYLOADS["claude"], "init", "--repo", repo, "--tool", "claude", "--today", TODAY)
        self.assertEqual(rc, 0, out[-3000:])
        with open(os.path.join(repo, ".agents", "manifest.yaml"), encoding="utf-8") as f:
            lint = yaml.safe_load(f)["verbs"]["lint"]
        self.assertEqual((lint["cmd"], lint["file_args"]), ("ruff check --extend-exclude .agents,.claude,.codex .", True))
        env = dict(os.environ, PATH=os.path.dirname(_ruff()) + os.pathsep + os.environ.get("PATH", ""))
        # the control: the old derivation lints the installed layer and fails, so the exclusion is what makes it green
        r = subprocess.run(["ruff", "check", "."], cwd=repo, capture_output=True, text=True, env=env)
        self.assertNotEqual(r.returncode, 0, "the installed layer no longer trips ruff: this control proves nothing")
        self.assertIn(".agents/", r.stdout)
        r = subprocess.run([os.path.join(repo, ".agents", "bin", "verb"), "verify"], cwd=repo, capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr[-1000:])
        self.assertNotIn("AGENTIVE_FAIL", r.stdout)
        with open(os.path.join(repo, ".agents", "state", "verify.json"), encoding="utf-8") as f:
            self.assertTrue(json.load(f)["verbs"]["lint"]["ok"])
        # PostToolUse lint on an edited file: the trailing `.` is replaced by the file (E2E-04)
        with open(os.path.join(repo, "shop", "__init__.py"), "a", encoding="utf-8") as f:
            f.write("import os\n")
        hook = os.path.join(repo, ".agents", "hooks", "post-tool-use-lint.py")
        inp = {"hook_event_name": "PostToolUse", "tool_name": "Edit", "cwd": repo,
               "tool_input": {"file_path": os.path.join(repo, "shop", "__init__.py")}}
        r = subprocess.run([sys.executable, hook], input=json.dumps(inp), cwd=repo, capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)             # the new unused import is reported
        self.assertIn("F401", r.stderr)
        self.assertNotIn(".agents/lib", r.stderr)
        logs = sorted(n for n in os.listdir(os.path.join(repo, ".agents", "state", "logs")) if n.startswith("lint-"))
        with open(os.path.join(repo, ".agents", "state", "logs", logs[-1]), encoding="utf-8") as f:
            self.assertEqual(f.readline().strip(), "$ ruff check --extend-exclude .agents,.claude,.codex shop/__init__.py")
        # an edit inside the installed layer is never handed to the project's linter
        inp["tool_input"]["file_path"] = os.path.join(repo, ".agents", "lib", "agentive", "verb.py")
        r = subprocess.run([sys.executable, hook], input=json.dumps(inp), cwd=repo, capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
