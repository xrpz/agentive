# agentive-unit: selftest.component
# design: docs/IMPLEMENTATION.md §5.5 (schemas validate every instance), §6 (component tests)
"""Shared helpers for tests/content: paths, schema validation, a temporary installed repo layer, a fake gh."""
import atexit
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile

import yaml
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

YAML_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)   # libyaml when PyYAML has it: the 1 MB design file loads in 0.1 s, not 1.1 s
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC = os.path.join(ROOT, "src", "repo-layer")
SKILLS = os.path.join(SRC, "dot-agents", "skills")
PAYLOAD_SKILLS = os.path.join(ROOT, "plugins", "claude-code", "agentive", "payload", "repo-layer", "dot-agents", "skills")   # run tools/generate.py first
LIB = os.path.join(SRC, "dot-agents", "lib")
SKILL_NAMES = ["chain-intent", "chain-plan", "chain-spec", "distill", "janitor", "pr", "pr-feedback", "precommit-review", "verify"]
FAKE_GH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fakegh.py")


def read(*parts):
    with open(os.path.join(*parts), encoding="utf-8") as f:
        return f.read()


_DESIGN = None


def design():
    global _DESIGN
    if _DESIGN is None:
        with open(os.path.join(ROOT, "design", "framework.yaml"), encoding="utf-8") as f:
            _DESIGN = yaml.load(f, Loader=YAML_LOADER)
    return _DESIGN


_REG = None


def registry():
    global _REG
    if _REG is None:
        reg, d = Registry(), os.path.join(ROOT, "design", "schemas")
        for name in sorted(os.listdir(d)):
            if name.endswith(".schema.json"):
                with open(os.path.join(d, name), encoding="utf-8") as f:
                    s = json.load(f)
                reg = reg.with_resource(s["$id"], Resource.from_contents(s))
        _REG = reg
    return _REG


def schema_errors(name, instance):
    with open(os.path.join(ROOT, "design", "schemas", f"{name}.schema.json"), encoding="utf-8") as f:
        schema = json.load(f)
    return [f"{'/'.join(map(str, e.absolute_path))}: {e.message}"
            for e in Draft202012Validator(schema, registry=registry()).iter_errors(instance)]


def sh(args, cwd, env=None, check=True):
    e = dict(os.environ)
    for k in ("AGENTIVE_HEADLESS", "CI", "GITHUB_ACTIONS", "CLAUDECODE"):
        e.pop(k, None)
    e.update(env or {})
    e.setdefault("GIT_AUTHOR_NAME", "t")
    e.setdefault("GIT_AUTHOR_EMAIL", "t@example.com")
    e.setdefault("GIT_COMMITTER_NAME", "t")
    e.setdefault("GIT_COMMITTER_EMAIL", "t@example.com")
    p = subprocess.run(args, cwd=cwd, env=e, capture_output=True, text=True)
    if check and p.returncode != 0:
        raise AssertionError(f"{args} failed ({p.returncode}): {p.stdout}{p.stderr}")
    return p


def git(cwd, *args):
    return sh(["git", *args], cwd).stdout.strip()


def write(root, rel, text):
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return p


_SANDBOX_TEMPLATES = {}   # json of the files dict -> directory of a finished sandbox, copied for every Sandbox with the same files


def _drop_sandbox_templates():
    for d in _SANDBOX_TEMPLATES.values():
        shutil.rmtree(d, ignore_errors=True)


atexit.register(_drop_sandbox_templates)


class Sandbox:
    """A bare origin (default branch main) and a clone with the C skills and the run-time library installed.

    The first Sandbox for a given `files` builds it (git init, clone, commit, push, install); the next ones with the same `files` copy
    that finished tree and point the clone's origin at their own copy of origin.git [설계 판단]. Every test still gets its own directory."""

    def __init__(self, files=None):
        files = files or {"README.md": "# demo\n"}
        key = json.dumps(files, sort_keys=True)
        if key not in _SANDBOX_TEMPLATES:
            self._build(files)
            tpl = tempfile.mkdtemp(prefix="agentive-c-tpl-")
            shutil.rmtree(tpl)
            shutil.copytree(self.tmp, tpl, symlinks=True)
            shutil.rmtree(self.tmp)
            _SANDBOX_TEMPLATES[key] = tpl
        self.tmp = tempfile.mkdtemp(prefix="agentive-c-")
        shutil.copytree(_SANDBOX_TEMPLATES[key], self.tmp, symlinks=True, dirs_exist_ok=True)
        self.origin = os.path.join(self.tmp, "origin.git")
        self.repo = os.path.join(self.tmp, "repo")
        git(self.repo, "remote", "set-url", "origin", self.origin)
        self.bin = os.path.join(self.tmp, "bin")
        os.makedirs(self.bin)

    def _build(self, files):
        self.tmp = tempfile.mkdtemp(prefix="agentive-c-")
        self.origin = os.path.join(self.tmp, "origin.git")
        self.repo = os.path.join(self.tmp, "repo")
        sh(["git", "init", "-q", "--bare", "-b", "main", self.origin], self.tmp)
        sh(["git", "clone", "-q", self.origin, self.repo], self.tmp)
        git(self.repo, "symbolic-ref", "HEAD", "refs/heads/main")
        for rel, text in files.items():
            write(self.repo, rel, text)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "init")
        git(self.repo, "push", "-q", "-u", "origin", "main")
        git(self.repo, "remote", "set-head", "origin", "main")
        self.install()

    def install(self):
        """Install like the engine would: skills at .agents/skills/<name>/ (stage-check.sh once per stage skill), the lib."""
        dst = os.path.join(self.repo, ".agents")
        shutil.copytree(LIB, os.path.join(dst, "lib"), ignore=shutil.ignore_patterns("__pycache__"))
        for name in SKILL_NAMES:      # from the generated payload: generate-time values (stage, headless command) are filled in
            shutil.copytree(os.path.join(PAYLOAD_SKILLS, name), os.path.join(dst, "skills", name))
        write(self.repo, ".git/info/exclude", ".agents/\n")

    def script(self, skill, name):
        return os.path.join(self.repo, ".agents", "skills", skill, "scripts", name)

    def fake_gh(self, routes):
        """routes: [(regex over the joined gh args, {"stdout": str|obj, "rc": int})]; calls are logged."""
        mp = os.path.join(self.tmp, "gh-routes.json")
        with open(mp, "w", encoding="utf-8") as f:
            json.dump([[r, (v if isinstance(v.get("stdout", ""), str) else dict(v, stdout=json.dumps(v["stdout"])))]
                       for r, v in routes], f)
        gh = os.path.join(self.bin, "gh")
        with open(gh, "w", encoding="utf-8") as f:
            f.write(f"#!/bin/sh\nexec {sys.executable} {FAKE_GH} \"$@\"\n")
        os.chmod(gh, os.stat(gh).st_mode | stat.S_IXUSR)
        self.gh_log = os.path.join(self.tmp, "gh-log.jsonl")
        return {"FAKE_GH_ROUTES": mp, "FAKE_GH_LOG": self.gh_log, "PATH": self.bin + os.pathsep + os.environ["PATH"]}

    def gh_calls(self):
        try:
            with open(self.gh_log, encoding="utf-8") as f:
                return [json.loads(x) for x in f if x.strip()]
        except FileNotFoundError:
            return []

    def run(self, args, env=None, check=False):
        return sh(args, self.repo, env=env, check=check)

    def close(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class DepthSandbox(Sandbox):
    """E2E-06: origin starts empty and the clone is `git clone --depth 1 file://...`: git writes no remote.origin.fetch,
    so `git push -u` and `git fetch` never create refs/remotes/origin/main (and there is no origin/HEAD)."""

    def __init__(self, files=None):
        self.tmp = tempfile.mkdtemp(prefix="agentive-c-")
        self.origin = os.path.join(self.tmp, "origin.git")
        self.repo = os.path.join(self.tmp, "repo")
        sh(["git", "init", "-q", "--bare", "-b", "main", self.origin], self.tmp)
        sh(["git", "clone", "-q", "--depth", "1", "file://" + self.origin, self.repo], self.tmp)
        git(self.repo, "symbolic-ref", "HEAD", "refs/heads/main")
        for rel, text in (files or {"README.md": "# demo\n"}).items():
            write(self.repo, rel, text)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "init")
        git(self.repo, "push", "-q", "-u", "origin", "main")
        self.install()
        self.bin = os.path.join(self.tmp, "bin")
        os.makedirs(self.bin)
