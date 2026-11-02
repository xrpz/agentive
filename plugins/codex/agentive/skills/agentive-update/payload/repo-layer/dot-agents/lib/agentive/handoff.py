# agentive-unit: lib.agentive
# design: spec.handoff, contracts[handoff], decisions.accepted[HANDOFFFILL], decisions.accepted[HANDOFFREPORT] · owner B
"""Branch handoff files .agents/state/handoff/<branch-key>.md (YAML frontmatter, schema handoff).

Writers: Stop hook (no_progress), guard hook (ask_class_denied), spec stage (open_question).
The script fills every field except failure_reason, untried and next_step (agent-written); secrets are redacted.
A writer that takes the fields as arguments refuses an empty or placeholder value (is_placeholder); a NO-PROGRESS field the agent
left as a placeholder is filled by the script with facts and starts with `(script) ` (fill_placeholders, HANDOFFFILL).
branch-key = branch lowercased, characters outside [a-z0-9.-] -> '-', + '-' + first 8 hex of sha256(branch);
detached HEAD -> detached-<sha7>. A new handoff overwrites the branch file; the overwritten one's one-line
summary goes to previous[] (last 5) [design judgment in the design]. Stale = HEAD differs or the work-tree diff hash
differs (marked, never deleted). Files older than 30 days move to handoff/done/ [design judgment in the design].
"""
import datetime
import hashlib
import os
import re
import subprocess

from . import ayaml, common

__all__ = ["branch_key", "handoff_dir", "write", "read", "listing", "worktree_diff_sha256", "redact", "expire", "is_placeholder",
           "fill_placeholders", "current_path", "report_line", "SCRIPT_PREFIX", "PLACEHOLDER_MIN"]

SCRIPT_PREFIX = "(script) "
PLACEHOLDER_MIN = 20     # [design judgment] the least length of failure_reason and next_step a writer with arguments accepts
_PLACEHOLDER_WORDS = {"tbd", "todo", "n/a", "na", "fill in", "fill me in", "unknown", "to be determined", "later"}

_SECRET = [
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"xox[abposr]-[A-Za-z0-9-]{10,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)((?:password|passwd|secret|token|api[_-]?key)\s*[=:]\s*)\S+"),
]


def redact(text):
    if not isinstance(text, str):
        return text
    for rx in _SECRET:
        text = rx.sub(lambda m: (m.group(1) if m.groups() else "") + "[REDACTED]", text)
    return text


def branch_key(branch, head):
    if not branch:
        return f"detached-{(head or '0000000')[:7]}"
    safe = re.sub(r"[^a-z0-9.-]", "-", branch.lower())
    return f"{safe}-{hashlib.sha256(branch.encode('utf-8')).hexdigest()[:8]}"


def handoff_dir(root):
    return os.path.join(root, ".agents", "state", "handoff")


def _git(root, *args):
    try:
        r = subprocess.run(["git", "-C", root] + list(args), capture_output=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def worktree_diff_sha256(root):
    out = _git(root, "diff", "HEAD")
    return common.sha256_bytes(out) if out is not None else None


def _split(text):
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            return text[4:end + 1], text[end + 5:]
    return None, text


def read(path):
    """Frontmatter dict of a handoff file, or None."""
    try:
        with open(path, encoding="utf-8") as f:
            fm, _ = _split(f.read())
    except OSError:
        return None
    if fm is None:
        return None
    try:
        doc = ayaml.loads(fm)
    except Exception:  # noqa: BLE001
        return None
    return doc if isinstance(doc, dict) else None


def report_line(rel, doc):
    """`HANDOFF=<path> reason=<r> blocked=<command|none> next=<step>`: the one line a run with no human present ends with
    (HANDOFFREPORT). The values come from the handoff file, which redacted them when it was written."""
    def one(value, limit):
        return " ".join(str(value or "").split())[:limit] or "none"
    return f"HANDOFF={rel} reason={doc.get('reason')} blocked={one(doc.get('blocked_command'), 160)} next={one(doc.get('next_step'), 200)}"


def is_placeholder(text, minimum=0):
    """True for an empty value, an `(agent: ...)` marker, a bare placeholder word (TBD, TODO, n/a), a single `<...>` or a text with
    no letter or digit; with `minimum` > 0 also for a text shorter than that many characters. Judges form, never meaning."""
    t = " ".join(str(text or "").split())
    if not t or t.lower().startswith("(agent:") or not any(c.isalnum() for c in t):
        return True
    if re.fullmatch(r"<[^<>]*>", t) or t.lower().strip(" .:;-_*()[]") in _PLACEHOLDER_WORDS:
        return True
    return len(t) < minimum


def _identity(root):
    head = (_git(root, "rev-parse", "HEAD") or b"").decode().strip() or "0000000"
    branch = (_git(root, "symbolic-ref", "--quiet", "--short", "HEAD") or b"").decode().strip() or None
    return branch, head


def current_path(root):
    """Absolute path of the current branch's handoff file (it may not exist)."""
    branch, head = _identity(root)
    return os.path.join(handoff_dir(root), branch_key(branch, head) + ".md")


def fill_placeholders(root, facts):
    """Replace each of failure_reason, untried and next_step of the current branch's handoff that is still a placeholder
    (is_placeholder without a length rule, so a short real value stays) with facts[field] and `(script) ` in front.
    An empty `untried` is a valid value and stays. Returns the filled field names."""
    path = current_path(root)
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return []
    fm, body = _split(text)
    if fm is None:
        return []
    try:
        doc = ayaml.loads(fm)
    except Exception:  # noqa: BLE001
        return []
    if not isinstance(doc, dict):
        return []
    filled = []
    for key in ("failure_reason", "untried", "next_step"):
        fact = " ".join(str(facts.get(key) or "").split())
        old = str(doc.get(key) or "")
        if not fact or old.startswith(SCRIPT_PREFIX) or (key == "untried" and not old.strip()) or not is_placeholder(old):
            continue
        if key == "failure_reason" and doc.get("blocked_command"):
            fact += f"; blocked command: {doc['blocked_command']}"
        doc[key] = redact(fact if fact.startswith(SCRIPT_PREFIX) else SCRIPT_PREFIX + fact)
        filled.append(key)
    if filled:
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write("---\n" + ayaml.dumps(doc) + "---\n" + body)
    return filled


def write(root, reason, failure_reason, next_step, untried="", plan=None, task=None, blocked_command=None):
    """Write (or overwrite) the handoff of the current branch. Returns its repo-relative path."""
    branch, head = _identity(root)
    key = branch_key(branch, head)
    d = handoff_dir(root)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, key + ".md")
    previous = []
    old = read(path)
    if old:
        previous = list(old.get("previous") or [])
        summary = redact(str(old.get("failure_reason") or old.get("reason") or "handoff")).splitlines()[0][:200] or "handoff"
        previous.insert(0, {"at": str(old.get("created_at") or common.utc_now()), "head": str(old.get("head") or head), "summary": summary})
        previous = previous[:5]
    doc = {"branch": branch, "branch_key": key, "head": head, "worktree_diff_sha256": worktree_diff_sha256(root),
           "created_at": common.utc_now(), "reason": reason}
    if plan:
        doc["plan"] = plan
    if task:
        doc["task"] = redact(task)
    if blocked_command:
        doc["blocked_command"] = redact(blocked_command)
    doc.update({"failure_reason": redact(failure_reason), "untried": redact(untried or ""), "next_step": redact(next_step),
                "stale": False})
    if previous:
        doc["previous"] = previous
    body = ["# Handoff (" + reason + ")", "",
            "Fill the three agent-written fields in the frontmatter above (failure_reason, untried, next_step) and keep",
            "them short and factual. Resolve: verify passes in the resumed session, or the human discards it",
            "(move this file to .agents/state/handoff/done/)."]
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("---\n" + ayaml.dumps(doc) + "---\n" + "\n".join(body) + "\n")
    return os.path.relpath(path, root).replace(os.sep, "/")


def expire(root, days=30, now=None):
    """Move handoffs older than `days` to handoff/done/. Returns moved file names."""
    d = handoff_dir(root)
    if not os.path.isdir(d):
        return []
    now = now or datetime.datetime.now(datetime.timezone.utc)
    moved = []
    for name in sorted(os.listdir(d)):
        p = os.path.join(d, name)
        if not name.endswith(".md") or not os.path.isfile(p):
            continue
        doc = read(p) or {}
        try:
            created = datetime.datetime.strptime(str(doc.get("created_at")), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
        except ValueError:
            created = datetime.datetime.fromtimestamp(os.path.getmtime(p), datetime.timezone.utc)
        if (now - created).days > days:
            os.makedirs(os.path.join(d, "done"), exist_ok=True)
            os.replace(p, os.path.join(d, "done", name))
            moved.append(name)
    return moved


def listing(root):
    """[(relpath, doc, stale, is_current_branch)] of active handoffs; marks stale ones in the file (never deletes)."""
    d = handoff_dir(root)
    if not os.path.isdir(d):
        return []
    head = (_git(root, "rev-parse", "HEAD") or b"").decode().strip() or "0000000"
    branch = (_git(root, "symbolic-ref", "--quiet", "--short", "HEAD") or b"").decode().strip() or None
    cur_key = branch_key(branch, head)
    diff = None
    out = []
    for name in sorted(os.listdir(d)):
        p = os.path.join(d, name)
        if not name.endswith(".md") or not os.path.isfile(p):
            continue
        doc = read(p)
        if not doc:
            continue
        current = doc.get("branch_key") == cur_key
        stale = bool(doc.get("stale"))
        if current and not stale:
            if diff is None:
                diff = worktree_diff_sha256(root)
            if doc.get("head") != head or (doc.get("worktree_diff_sha256") and doc.get("worktree_diff_sha256") != diff):
                stale = True
                _mark_stale(p)
        out.append((os.path.relpath(p, root).replace(os.sep, "/"), doc, stale, current))
    return out


def _mark_stale(path):
    with open(path, encoding="utf-8") as f:
        text = f.read()
    fm, body = _split(text)
    if fm is None:
        return
    doc = ayaml.loads(fm)
    doc["stale"] = True
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("---\n" + ayaml.dumps(doc) + "---\n" + body)


def main(argv):
    """`python3 -m agentive.handoff write --reason <r> --failure-reason <t> --next-step <t> [--untried <t>] [--plan <p>] [--task <t>]`
    (run with PYTHONPATH=.agents/lib from the repo root); prints `HANDOFF=<path>`."""
    import argparse
    ap = argparse.ArgumentParser(prog="python3 -m agentive.handoff")
    ap.add_argument("action", choices=["write"])
    ap.add_argument("--reason", required=True, choices=["no_progress", "ask_class_denied", "open_question"])
    ap.add_argument("--failure-reason", required=True)
    ap.add_argument("--next-step", required=True)
    ap.add_argument("--untried", default="")
    ap.add_argument("--plan")
    ap.add_argument("--task")
    ap.add_argument("--blocked-command")
    a = ap.parse_args(argv)
    bad = [name for name, value, least in (("failure_reason", a.failure_reason, PLACEHOLDER_MIN), ("next_step", a.next_step, PLACEHOLDER_MIN))
           if is_placeholder(value, least)]
    if a.untried and is_placeholder(a.untried, 1):
        bad.append("untried")
    if bad:      # HANDOFFFILL: the agent repeats the call in the same turn, nothing is written
        print(f"AGENTIVE_HANDOFF=stop reason=placeholder fields={','.join(bad)}; NEXT=pass a concrete statement of at least "
              f"{PLACEHOLDER_MIN} characters for failure_reason and next_step (no TBD, no `(agent: ...)`, no `<...>`) and rerun the same command")
        return 2
    root = common.repo_root(os.getcwd()) or os.getcwd()
    print("HANDOFF=" + write(root, a.reason, a.failure_reason, a.next_step, a.untried, a.plan, a.task, a.blocked_command))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv[1:]))
