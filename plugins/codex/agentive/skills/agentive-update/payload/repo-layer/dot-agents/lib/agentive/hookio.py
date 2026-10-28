# agentive-unit: lib.agentive
# design: spec.enforcement.hooks (repo_root), spec.init_apply.session_start, spec.stop_hook · owner B
"""Shared plumbing for the installed hook scripts (.agents/hooks/*.py) and .agents/bin/verb.

- read_input(): hook input JSON from stdin ({} when empty or not JSON)
- root_from(inp): repo root from the hook input cwd (`git -C <cwd> rev-parse --show-toplevel`), never from
  CLAUDE_PROJECT_DIR (it keeps pointing at the project root inside a worktree); falls back to cwd
- load_manifest(root): the agentive manifest (.agents/manifest.yaml) as a dict, or None
- emit(obj): print one JSON object on stdout (the only stdout of a JSON-speaking hook)
- tool_arg(argv): "codex" when the script got --tool codex (Codex hooks.json passes it), else "claude"
- state_dir(root): .agents/state (created on demand)
"""
import json
import os
import sys

from . import ayaml, common

__all__ = ["read_input", "root_from", "load_manifest", "emit", "tool_arg", "state_dir", "git", "head_sha",
           "current_branch", "default_branch"]


def read_input(stream=None):
    stream = sys.stdin if stream is None else stream
    try:
        data = stream.read()
    except (OSError, ValueError):
        return {}
    if not data.strip():
        return {}
    try:
        obj = json.loads(data)
    except ValueError:
        return {}
    return obj if isinstance(obj, dict) else {}


def root_from(inp):
    cwd = inp.get("cwd") if isinstance(inp, dict) else None
    cwd = cwd if cwd and os.path.isdir(cwd) else os.getcwd()
    return common.repo_root(cwd) or os.path.abspath(cwd)


def load_manifest(root):
    path = os.path.join(root, ".agents", "manifest.yaml")
    if not os.path.isfile(path):
        return None
    try:
        doc = ayaml.load(path)
    except Exception:  # noqa: BLE001 - a broken manifest must not crash a hook; callers treat None as absent
        return None
    return doc if isinstance(doc, dict) else None


def emit(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def tool_arg(argv):
    for i, a in enumerate(argv):
        if a == "--tool" and i + 1 < len(argv):
            return argv[i + 1]
        if a.startswith("--tool="):
            return a.split("=", 1)[1]
    return "claude"


def state_dir(root):
    d = os.path.join(root, ".agents", "state")
    os.makedirs(d, exist_ok=True)
    return d


def git(root, *args):
    """stdout of a git command in root (stripped), or None on any failure."""
    import subprocess
    try:
        r = subprocess.run(["git", "-C", root] + list(args), capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def head_sha(root):
    return git(root, "rev-parse", "HEAD")


def current_branch(root):
    """Branch name, or None on a detached HEAD / outside git."""
    b = git(root, "symbolic-ref", "--quiet", "--short", "HEAD")
    return b or None


def default_branch(root):
    """Default branch from the real remote (origin/HEAD); None when unknown [설계 판단: callers then use main/master]."""
    ref = git(root, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD")
    if ref and "/" in ref:
        return ref.split("/", 1)[1]
    return None
