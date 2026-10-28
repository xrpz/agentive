# agentive-unit: lib.agentive
# design: spec.spec_stage.chain.start_check.default_branch, spec.outer_loop.cloud, spec.work_loop.pr_feedback.reads, spec.no_human_run
"""git and `gh api` helpers for the work-loop skill scripts (owner C; python3 stdlib only).

- run(args, cwd): subprocess with captured text output; never raises on a non-zero exit
- default_branch(root): origin/HEAD via `git symbolic-ref`, else `git ls-remote --symref origin HEAD`, else None
- origin_repo(root): "owner/repo" parsed from the origin URL (no network), else None
- gh(args) / gh_json(endpoint, ...) / gh_pages(endpoint): `gh api` only (works in cloud sessions and Routines
  runs where `gh pr` is blocked); gh_pages follows --paginate and merges the concatenated JSON arrays
- no_human(): the run has no human present (AGENTIVE_HEADLESS=1, CI or GITHUB_ACTIONS); hook-only signals
  (permission_mode dontAsk) are not visible to scripts, so their absence counts as interactive
"""
import json
import os
import re
import subprocess

__all__ = ["run", "git", "default_branch", "origin_repo", "gh", "gh_json", "gh_pages", "no_human", "GhError"]


class GhError(RuntimeError):
    pass


def run(args, cwd=None, input_text=None):
    try:
        p = subprocess.run(args, cwd=cwd, input=input_text, capture_output=True, text=True)
    except OSError as e:
        return 127, "", str(e)
    return p.returncode, p.stdout, p.stderr


def git(root, *args):
    """stdout of a git command (stripped), or None on failure."""
    rc, out, _ = run(["git", "-C", root] + list(args))
    return out.strip() if rc == 0 else None


def default_branch(root):
    ref = git(root, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD")
    if ref:
        return ref[len("origin/"):] if ref.startswith("origin/") else ref
    rc, out, _ = run(["git", "-C", root, "ls-remote", "--symref", "origin", "HEAD"])
    if rc == 0:
        m = re.search(r"^ref: refs/heads/(\S+)\s+HEAD$", out, re.M)
        if m:
            return m.group(1)
    return None


_REMOTE = re.compile(r"(?:[:/])([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$")


def origin_repo(root):
    url = git(root, "remote", "get-url", "origin")
    if not url:
        return None
    m = _REMOTE.search(url)
    return f"{m.group(1)}/{m.group(2)}" if m else None


def gh(args, input_text=None, cwd=None):
    """stdout of `gh api <args>`; raises GhError with stderr on failure."""
    rc, out, err = run(["gh", "api"] + list(args), cwd=cwd, input_text=input_text)
    if rc != 0:
        raise GhError((err or out).strip().splitlines()[-1] if (err or out).strip() else f"gh api exit {rc}")
    return out


def gh_json(endpoint, *extra, cwd=None):
    out = gh([endpoint] + list(extra), cwd=cwd)
    return json.loads(out) if out.strip() else None


def gh_pages(endpoint, *extra, cwd=None):
    """All items of a paginated REST list (gh prints one JSON array per page)."""
    out = gh([endpoint, "--paginate"] + list(extra), cwd=cwd)
    items, dec, i = [], json.JSONDecoder(), 0
    while True:
        while i < len(out) and out[i].isspace():
            i += 1
        if i >= len(out):
            break
        obj, i = dec.raw_decode(out, i)
        items.extend(obj if isinstance(obj, list) else [obj])
    return items


def no_human(env=None):
    env = os.environ if env is None else env
    return env.get("AGENTIVE_HEADLESS") == "1" or bool(env.get("CI")) or bool(env.get("GITHUB_ACTIONS"))
