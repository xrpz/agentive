# agentive-unit: selftest.component
# design: docs/IMPLEMENTATION.md §6 (component_tests, owner D)
"""Shared test helpers for tests/review and tests/ci: throwaway git repos with the review layer installed, a
stand-in for the LLM side of the task protocol, and schema validation with the design schemas."""
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC = os.path.join(ROOT, "src", "repo-layer")
LIB = os.path.join(SRC, "dot-agents", "lib")
if LIB not in sys.path:
    sys.path.insert(0, LIB)


def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def schema_validator(name):
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource
    d = os.path.join(ROOT, "design", "schemas")
    reg, schemas = Registry(), {}
    for f in sorted(os.listdir(d)):
        if f.endswith(".json"):
            s = _load(os.path.join(d, f))
            schemas[f[:-len(".schema.json")]] = s
            reg = reg.with_resource(s["$id"], Resource.from_contents(s))
    return lambda inst: [f"{'/'.join(map(str, e.absolute_path))}: {e.message}" for e in
                         Draft202012Validator(schemas[name], registry=reg).iter_errors(inst)]


def validate_ref(ref, inst):
    """Errors of inst against a $ref into a design schema (for example a $defs entry)."""
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource
    d = os.path.join(ROOT, "design", "schemas")
    reg = Registry()
    for f in sorted(os.listdir(d)):
        if f.endswith(".json"):
            s = _load(os.path.join(d, f))
            reg = reg.with_resource(s["$id"], Resource.from_contents(s))
    return [e.message for e in Draft202012Validator({"$ref": ref}, registry=reg).iter_errors(inst)]


def git(repo, *args, env=None):
    e = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com", GIT_COMMITTER_NAME="t",
             GIT_COMMITTER_EMAIL="t@example.com", GIT_CONFIG_NOSYSTEM="1", HOME=repo)
    if env:
        e.update(env)
    r = subprocess.run(["git", "-C", repo] + list(args), capture_output=True, text=True, env=e)
    if r.returncode != 0:
        raise RuntimeError(f"git {args}: {r.stderr}")
    return r.stdout.strip()


class Repo:
    """A temporary git repository with .agents/bin/agentive-* and .agents/lib copied from src/."""

    def __init__(self, files=None, install=True):
        self.dir = tempfile.mkdtemp(prefix="agentive-test-")
        git(self.dir, "init", "-q", "-b", "main")
        if install:
            os.makedirs(os.path.join(self.dir, ".agents", "bin"))
            shutil.copytree(os.path.join(LIB, "agentive"), os.path.join(self.dir, ".agents", "lib", "agentive"),
                            ignore=shutil.ignore_patterns("__pycache__"))
            for b in ("agentive-review", "agentive-lint", "agentive-reviewer-eval"):
                shutil.copy2(os.path.join(SRC, "dot-agents", "bin", b), os.path.join(self.dir, ".agents", "bin", b))
            self.write({".gitignore": ".agents/state/\n"})
        if files:
            self.write(files)

    def path(self, p):
        return os.path.join(self.dir, p)

    def write(self, files):
        for p, content in files.items():
            full = self.path(p)
            if content is None:
                if os.path.exists(full):
                    os.unlink(full)
                continue
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w", encoding="utf-8", newline="\n") as f:
                f.write(content)

    def commit(self, msg="c", date=None):
        git(self.dir, "add", "-A")
        env = {"GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date} if date else None
        git(self.dir, "commit", "-q", "--allow-empty", "-m", msg, env=env)
        return git(self.dir, "rev-parse", "HEAD")

    def run(self, *args, env=None):
        e = dict(os.environ)
        for k in ("CI", "GITHUB_ACTIONS", "AGENTIVE_HEADLESS", "GITHUB_OUTPUT", "GITHUB_STEP_SUMMARY", "GITHUB_REPOSITORY", "GITHUB_EVENT_NAME"):
            e.pop(k, None)
        if env:
            e.update(env)
        r = subprocess.run([sys.executable] + list(args), cwd=self.dir, capture_output=True, text=True, env=e)
        return r.returncode, r.stdout + r.stderr

    def cleanup(self):
        shutil.rmtree(self.dir, ignore_errors=True)


def task_lines(out):
    res = []
    for ln in out.splitlines():
        if ln.startswith("AGENTIVE_REVIEW_TASK "):
            kv = dict(x.split("=", 1) for x in ln.split()[1:])
            res.append(kv)
    return res


def answer_tasks(out, responder):
    """Write responder(agent, input dict) -> dict for every task line in out; returns the agents answered."""
    done = []
    for t in task_lines(out):
        inp = _load(t["input"])
        reply = responder(t["agent"], inp)
        with open(t["output"], "w", encoding="utf-8") as f:
            f.write(reply if isinstance(reply, str) else json.dumps(reply))
        done.append(t["agent"])
    return done


def drive(repo, args, responder, max_rounds=8):
    """Run an agentive-review command until it stops asking for tasks; returns (rc, output, agents answered)."""
    agents = []
    for _ in range(max_rounds):
        rc, out = repo.run(*args)
        if rc != 3:
            return rc, out, agents
        agents += answer_tasks(out, responder)
    raise AssertionError("task loop did not finish:\n" + out)


def default_responder(findings=None, raise_needed=False, verdict="confirmed", classes=("feature",)):
    findings = findings or {}

    def r(agent, inp):
        if agent == "classifier":
            return {"classes": list(classes), "why": "test"}
        if agent == "raise-only":
            return {"human_needed": raise_needed, "criteria": ["a"] if raise_needed else [], "why": "test"}
        if agent.endswith("-reviewer"):
            return {"findings": list(findings.get(agent, [])), "open_findings": []}
        if agent == "verifier":
            v = verdict if not callable(verdict) else None
            return {"verdicts": [{"id": f["id"], "verdict": v or verdict(f), "method": "adversarial_fresh_context", "note": "t"}
                                 for f in inp["findings"]]}
        if agent == "coordinator":
            return {"findings": [{"id": f["id"], "severity": f["severity"]} for f in inp["findings"]]}
        raise AssertionError(agent)
    return r
