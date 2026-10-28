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

    def check_stack(self, stack, payload="claude"):
        repo = self.make(stack, user_agents=(stack == "py"))
        before = snapshot(repo)
        with open(os.path.join(repo, ".gitignore"), encoding="utf-8") as f:
            gitignore_before = f.read()
        rc, out = engine(PAYLOADS[payload], "init", "--repo", repo, "--confirm-all", "--no-managed-review-copy", "--today", TODAY)
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
        rc, out2 = engine(PAYLOADS[payload], "update", "--repo", repo, "--confirm-all", "--today", TODAY)
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
        for path, schema in ((".agents/manifest.yaml", "manifest"), (".agents/decisions.yaml", "decisions"), (".agents/rubric.yaml", "rubric"),
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
        manifest = y(".agents/manifest.yaml")
        for key in manifest.get("generated") or {}:
            self.assertTrue(os.path.lexists(os.path.join(repo, key.split("#")[0])), f"{stack}: manifest.generated {key} has no file")

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

    def test_codex_payload_matches(self):
        """The Codex skill payload installs the same repo layer (one payload copy per Codex skill)."""
        a = self.check_stack("go", payload="claude")
        b = self.check_stack("go", payload="codex")
        sa, sb = snapshot(a), snapshot(b)
        self.assertEqual(sorted(sa), sorted(sb))


if __name__ == "__main__":
    unittest.main()
