#!/usr/bin/env python3
# agentive-unit: rl.hooks
# design: spec.init_apply.session_start, spec.compat, spec.maintenance.supply_chain, spec.handoff.reader, spec.context_management.compact_reinject, spec.init_apply.headless_init_update, decisions.accepted[CTXREINJECT], decisions.accepted[OPENWORK]
"""SessionStart (repo layer; Claude Code and Codex). Notify only: command hooks cannot run commands for Claude.

startup | resume : resets the Stop counter (a new start), then
  `AGENTIVE_STATE=ready; VERSION=<x>; COMPAT=<ok|fail:...>; SUPPLY=<ok|mismatch:names>; PENDING=<verbs|none>`,
  `HANDOFF=<path> [stale]; NEXT=ask_user:resume|discard|later; DO_NOT=...` (current branch), other branches one line
  each, `OPEN_WORK=<plan path>; NEXT=continue with the first open checkbox` + the open plan.md checkboxes of the branch's task
  (<= 1,500 chars, with or without a handoff file), `PENDING_CONFIRMATION=<n files> ...; NEXT=tell_user:<next>` (files written but not yet effective),
  `DIRTY=<slots> ...; NEXT=ask_user_to_run:/agentive:reprove` (evidence changed, design judgment V1-07),
  `HARVEST=<n>; NEXT=ask_user_to_run:/distill; DO_NOT=run_distill_unasked`
  when .agents/state/harvest-queue.jsonl has >= HARVEST_MIN (2) entries (`NEXT=none` with no human present) and
  watchPaths for evidence files below the repo root (Claude Code).
compact : the compact re-inject (HANDOFF line, failed verbs of the last verify, plan.md open checkboxes, [user] decisions of the
          task, git log <base>..HEAD; one capped section each), <= 10,000 chars.
clear   : the HANDOFF line only.
No human present (no_human_run): notices only, nothing asks. Handoffs older than 30 days move to handoff/done/.
"""
import os
import re
import subprocess
import sys

sys.dont_write_bytecode = True  # no __pycache__ in the user's repo (V1-08)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))
from agentive import ayaml, common, handoff, hookio, nohuman, state, verb  # noqa: E402

CAP = 10000
HANDOFF_CAP = 600   # [design judgment] the caps of the compact re-inject sections; their sum (7,900) stays below CAP
FAIL_CAP = 800
FAIL_LINES = 4
BOX_CAP = 3000
DECISION_CAP = 2000
LOG_CAP = 1500
OPEN_WORK_CAP = 1500
HARVEST_MIN = 2   # [design judgment] a single queued session is noise; two or more is a repetition worth proposing /distill


def _ver(s):
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", s or "")
    return tuple(int(x) for x in m.groups()) if m else None


def _tool_version(exe):
    try:
        r = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return _ver(r.stdout + r.stderr) if r.returncode == 0 else None


def compat(root, tool):
    p = os.path.join(root, ".agents", "compat.yaml")
    try:
        c = ayaml.load(p)
    except Exception:  # noqa: BLE001
        return "unknown"
    key = "codex" if tool == "codex" else "claude_code"
    spec = c.get(key) or {}
    have = _tool_version("codex" if tool == "codex" else "claude")
    if have is None:
        return "unknown"
    fails = []
    mn = _ver(str(spec.get("min")))
    if mn and have < mn:
        fails.append("min")
    for name, v in sorted((spec.get("features") or {}).items()):
        need = _ver(str(v))
        if need and have < need:
            fails.append(name)
    return "fail:" + ",".join(fails) if fails else "ok"


def supply(root):
    p = os.path.join(root, ".agents", "skills.lock")
    lock = common.read_json(p, None) if os.path.isfile(p) else None
    if not lock:
        return "ok"
    bad = []
    for name, e in sorted((lock.get("skills") or {}).items()):
        d = os.path.join(root, e.get("path", ""))
        if not os.path.isdir(d) or common.tree_sha256(d) != e.get("sha256"):
            bad.append(name)
    return "mismatch:" + ",".join(bad) if bad else "ok"


def pending_verbs(manifest):
    out = [k for k, v in (manifest.get("verbs") or {}).items() if isinstance(v, dict) and v.get("status") == "pending"]
    return ",".join(out) if out else "none"


def watch_paths(root, manifest):
    paths = set()
    for v in (manifest.get("verbs") or {}).values():
        for f in ((v or {}).get("evidence") or {}).get("files") or [] if isinstance(v, dict) else []:
            if "/" in f and not f.startswith(".agents/"):
                paths.add(os.path.join(root, f))
    return sorted(paths)


def handoff_lines(root, ask, with_next=False):
    """HANDOFF lines; with_next (startup, resume) ends the current-branch line with the agent's NEXT (V1-06: the
    instruction must reach the agent's context, not only the human's systemMessage)."""
    lines, notes = [], []
    for rel, doc, stale, current in handoff.listing(root):
        if current:
            nxt = ""
            if with_next:
                nxt = ("; NEXT=ask_user:resume|discard (move to .agents/state/handoff/done/)|later; DO_NOT=start_other_work_before_the_answer"
                       if ask else "; NEXT=none")
            scripted = any(str(doc.get(k) or "").startswith(handoff.SCRIPT_PREFIX) for k in ("failure_reason", "untried", "next_step"))
            lines.insert(0, f"HANDOFF={rel}{' stale' if stale else ''}{' agent_notes=missing' if scripted else ''}{nxt}")
            notes.append(f"agentive: a handoff exists for this branch ({rel}{', stale' if stale else ''})."
                         + (" Ask the human: resume / discard (move to handoff/done/) / later, before other work." if ask else ""))
        else:
            lines.append(f"HANDOFF_OTHER={rel} branch={doc.get('branch') or 'detached'}")
    return lines, notes


def _lines_capped(lines, cap):
    """The lines joined by newlines within `cap` characters; what does not fit is replaced by one `... N more` line (counted in
    the cap), so the end of a long list says that it was cut instead of stopping in the middle of a line."""
    if sum(len(x) + 1 for x in lines) - 1 <= cap:
        return "\n".join(lines)
    out, used = [], 0
    for i, ln in enumerate(lines):
        more = f"... {len(lines) - i} more"
        if used + len(ln) + 1 + len(more) > cap:
            return "\n".join(out + [more])
        out.append(ln)
        used += len(ln) + 1
    return "\n".join(out)


def _base(root):
    d = hookio.default_branch(root)
    for cand in ([f"origin/{d}"] if d else []) + ["origin/main", "origin/master", "main", "master"]:
        base = hookio.git(root, "merge-base", "HEAD", cand)
        if base:
            return base
    return None


def current_handoff_line(root):
    """The `HANDOFF=<path>` line of the current branch, or None (HANDOFF_OTHER lines are not it)."""
    hl, _ = handoff_lines(root, ask=False)
    return next((ln for ln in hl if ln.startswith("HANDOFF=")), None)


USER_DECISION = re.compile(r"^\s*[-*]\s+\[user\](?!\s*\|)\s*\S")   # `- [user] text`; not the template line `- [user] | [doc: ...] | [inferred]`
CHAIN_FILE = re.compile(r"^(docs/plans/[^/]+)/(?:intent|spec|plan)\.md$")


def failed_verbs_text(root):
    """The verbs that failed in the last .agents/state/verify.json, one `AGENTIVE_FAIL verb=<v> failures=<n> log=<path>` line each
    (the wrapper's own fail line is not stored there), under a line that says whether the record is of the current work tree
    (verb.fresh_for, VERIFYTREE) or of an earlier one. None when the last verify was green or there is none."""
    rec = state.load_verify(root)
    bad = [(k, x) for k, x in (rec.get("verbs") or {}).items() if isinstance(x, dict) and not x.get("ok")]
    if not bad:
        return None
    order = list(verb.VERIFY_ORDER) + ["verify"]
    bad.sort(key=lambda kx: order.index(kx[0]) if kx[0] in order else len(order))
    tree = hookio.content_tree(root)
    fresh = bool(tree) and verb.fresh_for(rec, tree, hookio.load_manifest(root))
    head = "Last verify: red on the current work tree" if fresh else "Last verify: red as of an earlier work tree"
    lines = [f"AGENTIVE_FAIL verb={k} failures={x.get('failures', 1)} log={x.get('log') or 'none'}" for k, x in bad[-FAIL_LINES:]]
    return _lines_capped([head] + lines, FAIL_CAP)


def user_decisions_text(root, task_dirs):
    """The `- [user] ...` bullets of intent.md, spec.md and plan.md in each docs/plans/<slug>/ of the task."""
    found = []
    for d in task_dirs:
        for name in ("intent.md", "spec.md", "plan.md"):
            try:
                with open(os.path.join(root, d, name), encoding="utf-8") as f:
                    hits = [ln.strip() for ln in f if USER_DECISION.match(ln)]
            except OSError:
                continue
            if hits:
                found += [f"[user] decisions in {d}/{name}:"] + hits
    return _lines_capped(found, DECISION_CAP) if found else None


def task_files(root, base):
    """docs/plans paths the branch changed against the base, or the work tree against HEAD."""
    changed = (hookio.git(root, "diff", "--name-only", f"{base}...HEAD", "--", "docs/plans") or "").splitlines()
    changed += (hookio.git(root, "diff", "--name-only", "HEAD", "--", "docs/plans") or "").splitlines()
    return sorted(set(changed))


def open_boxes(root, plans, cap):
    """(text, paths): the open checkboxes of each plan.md under a `Open checkboxes in <path>:` line, within `cap`; paths = the
    plans that have any. (None, []) when no plan has an open checkbox."""
    boxes, paths = [], []
    for p in plans:
        try:
            with open(os.path.join(root, p), encoding="utf-8") as f:
                found = [ln.rstrip() for ln in f if re.match(r"^\s*[-*] \[ \]", ln)]
        except OSError:
            continue
        if found:
            boxes += [f"Open checkboxes in {p}:"] + found
            paths.append(p)
    return (_lines_capped(boxes, cap), paths) if boxes else (None, [])


def open_work_text(root):
    """OPENWORK: the open plan.md checkboxes of the current branch's task for startup and resume, handoff file or not. Nothing on
    the default branch, on a detached HEAD, or when no plan.md of the branch has an open checkbox."""
    base = _base(root)
    cur = hookio.current_branch(root)
    if not base or not cur or cur in (hookio.default_branch(root), "main", "master"):
        return None
    plans = sorted(p for p in task_files(root, base) if p.endswith("/plan.md") and "/archive/" not in p)
    head = "OPEN_WORK={}; NEXT=continue with the first open checkbox"
    text, paths = open_boxes(root, plans, OPEN_WORK_CAP - len(head.format(",".join(plans))) - 1)
    return head.format(",".join(paths)) + "\n" + text if text else None


def compact_text(root):
    """The compact re-inject (spec.context_management.compact_reinject, decisions.accepted[CTXREINJECT]): five sections in this
    order, each within its own cap: the HANDOFF line, the failed verbs of the last verify, the open plan.md checkboxes, the [user]
    decisions of the task, the git log. A long list ends in `... N more` and no section pushes another out (before, the sections
    were joined and cut at CAP, so a long plan.md cut the HANDOFF line, the last part, first) [design judgment: the caps]."""
    parts = []
    hl = current_handoff_line(root)
    if hl:
        parts.append(hl[:HANDOFF_CAP])
    failed = failed_verbs_text(root)
    if failed:
        parts.append(failed)
    base = _base(root)
    log = None
    if base:
        files = task_files(root, base)
        plans = sorted(p for p in files if p.endswith("/plan.md") and "/archive/" not in p)
        boxes, _ = open_boxes(root, plans, BOX_CAP)
        if boxes:
            parts.append(boxes)
        decisions = user_decisions_text(root, sorted({m.group(1) for m in map(CHAIN_FILE.match, files) if m}))
        if decisions:
            parts.append(decisions)
        log = hookio.git(root, "log", "--oneline", f"{base}..HEAD")
    if log:
        parts.append(f"Commits on this branch ({base[:7]}..HEAD):\n" + _lines_capped(log.splitlines(), LOG_CAP))
    return "\n\n".join(parts)[:CAP]


def main(argv):
    tool = hookio.tool_arg(argv)
    inp = hookio.read_input()
    root = hookio.root_from(inp)
    source = inp.get("source") or "startup"
    nh = nohuman.no_human(hook_input=inp if tool == "codex" else None)
    manifest = hookio.load_manifest(root)
    if manifest is None:
        cmd = "$agentive-init" if tool == "codex" else "/agentive:init"
        line = "AGENTIVE_STATE=uninitialized; NEXT=none" if nh else f"AGENTIVE_STATE=uninitialized; NEXT=ask_user_to_run:{cmd}; DO_NOT=reproduce init steps"
        print(line)
        return 0
    ctx, notes, out = [], [], {}
    if source == "compact":
        ctx.append(compact_text(root))
    elif source == "clear":
        hl = current_handoff_line(root)
        ctx += [hl] if hl else []
    else:
        state.reset_stop(root, inp.get("session_id") or "", "new_start")
        handoff.expire(root)
        ver = (manifest.get("framework") or {}).get("version", "unknown")
        ctx.append(f"AGENTIVE_STATE=ready; VERSION={ver}; COMPAT={compat(root, tool)}; SUPPLY={supply(root)}; PENDING={pending_verbs(manifest)}")
        if sys.version_info < (3, 13):      # E2E-46: the floor check, the same line as the engine (src/engine/apply.py)
            ctx.append("AGENTIVE_ERROR python3 < 3.13; NEXT=install python3 >= 3.13")
        hl, hn = handoff_lines(root, ask=not nh, with_next=True)
        ctx += hl
        notes += hn
        ow = open_work_text(root)
        if ow:
            ctx.append(ow)
        pc = common.read_json(state.path(root, "pending-confirmation.json"), None)
        if isinstance(pc, dict) and pc.get("files"):
            n = len(pc["files"])
            why = ",".join(sorted({str(f.get("reason", "pending")) for f in pc["files"]}))
            nx = next((f["next"] for f in pc["files"] if f.get("next")), "")
            agent_next = "none" if nh else "tell_user:" + (" ".join(nx.split()) or "see .agents/state/pending-confirmation.json")
            ctx.append(f"PENDING_CONFIRMATION={n} files written but not yet effective: {why} (.agents/state/pending-confirmation.json); NEXT={agent_next}")
            notes.append(f"agentive: {n} file(s) written by {pc.get('command', 'init/update')} are not yet effective ({why})." + (f" {nx}" if nx else ""))
        dirty = sorted({e.get("slot") for e in state.load_dirty(root)["slots"] if e.get("slot")})
        if dirty:      # V1-07: the FileChanged hook marked slots whose evidence changed; the reprove is the human's command
            cmd = "$agentive-update" if tool == "codex" else "/agentive:reprove"
            ctx.append(f"DIRTY={','.join(dirty)} (stack evidence changed); NEXT=" + ("none" if nh else f"ask_user_to_run:{cmd}; DO_NOT=edit_the_manifest_by_hand"))
        n_harvest = state.harvest_count(root)
        if n_harvest >= HARVEST_MIN:
            cmd = "$distill" if tool == "codex" else "/distill"
            if nh:
                ctx.append(f"HARVEST={n_harvest}; NEXT=none")
            else:
                ctx.append(f"HARVEST={n_harvest}; NEXT=ask_user_to_run:{cmd}; DO_NOT=run_distill_unasked")
                notes.append(f"agentive: {n_harvest} sessions are queued for {cmd} (repeated mistakes). "
                             f"At the end of the task the agent offers it; it runs only after you agree.")
        if tool == "claude":
            wp = watch_paths(root, manifest)
            if wp:
                out["watchPaths"] = wp
    text = "\n".join(x for x in ctx if x)
    hso = {"hookEventName": "SessionStart", "additionalContext": text}
    hso.update(out)
    obj = {"hookSpecificOutput": hso}
    if notes and tool == "claude":
        obj["systemMessage"] = " ".join(notes)
    hookio.emit(obj)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
