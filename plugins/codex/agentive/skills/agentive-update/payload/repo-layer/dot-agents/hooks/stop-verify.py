#!/usr/bin/env python3
# agentive-unit: rl.hooks
# design: spec.stop_hook, spec.stop_conditions, spec.handoff, contracts[stop-state], contracts[verify-result], decisions.accepted[VERIFYTREE], decisions.accepted[HANDOFFFILL], decisions.accepted[OPENMARK], decisions.accepted[HANDOFFREPORT]
"""Stop hook (Claude Code and Codex): run inner verify and decide DONE / PENDING / NO-PROGRESS / CAP / block.

1. stop.json handoff_requested (this Stop follows the one NO-PROGRESS continuation): fill the handoff fields the agent left as a
   placeholder with `(script)` facts (HANDOFFFILL), clear it, exit 0, no rerun
1b. no human present, branch spec/<slug>, docs/plans/<slug>/spec.md has an `- [OPEN]` bullet and the branch has no handoff:
   continue once (stop.json open_question_nudged) asking for handoff.sh; no rerun, no counter change (OPENMARK)
2. the work content tree (hookio.content_tree: tracked files as on disk, untracked not ignored; no commit sha, so a commit
   alone changes nothing) equal to verify.json content_tree with the same verb commands (verb.fresh_for, VERIFYTREE):
   2a. last verify green: exit 0, no rerun ("DONE (unchanged)")
   2b. last verify red and consecutive_blocks == 0 (Q&A turn, or the tree was already red before this turn):
       exit 0, no rerun, no block, `RED-UNCHANGED` notice
   2c. last verify red and consecutive_blocks > 0: no rerun; the cached verify.json goes through steps 5-8
   different (or no tree: outside git): step 3 as before
3. `.agents/bin/verb verify` -> verify.json (pending verbs skipped, not failures)
4. no verb has a command: exit 0, PENDING notice (never DONE)
5. verify green: reset window and counter, exit 0, DONE
   total_failures decreased: reset (window restarts, counter 0)
6. last N (manifest stop_conditions.no_progress_rounds) totals not decreasing: NO-PROGRESS -> branch handoff,
   handoff_requested, window emptied, continue once (Claude: Stop additionalContext; Codex: decision block)
7. consecutive_blocks >= manifest stop_conditions.max_blocks (default 8): exit 0, CAP
8. else consecutive_blocks += 1, exit 2 with the AGENTIVE_FAIL lines on stderr
stop_hook_active is informational only. UserPromptSubmit never resets the counter.
With no human present, a Stop that ends with a message puts the line `HANDOFF=<path> reason=<r> blocked=<cmd|none> next=<step>` in front of it
when the branch handoff was written in this session (stop.json started_at; HANDOFFREPORT).
Dirty slots (FileChanged) are reported with NEXT=/agentive:reprove [design judgment: the engine is in the plugin].
"""
import os
import re
import subprocess
import sys

sys.dont_write_bytecode = True  # no __pycache__ in the user's repo (V1-08)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))
from agentive import common, handoff, hookio, nohuman, state, verb  # noqa: E402

PENDING_CHECKS = ("lint", "typecheck", "test", "build")   # the verbs a project can require (review.pipeline.stages.RV1.require_checks)

PENDING_MSG = "PENDING: no stack evidence yet; scaffold the project, then run /agentive:update"


def pending_note(manifest):
    """Some (not all) verbs have no command (PENDINGNOTICE, E2E-19 EFFECTIVECHECKS): they are named, with the way out. All verbs pending is the
    greenfield case and keeps PENDING_MSG. Empty string when nothing is pending."""
    verbs = (manifest or {}).get("verbs") or {}
    pend = sorted(n for n, v in verbs.items() if isinstance(v, dict) and v.get("status") == "pending" and n in PENDING_CHECKS)
    return f"; PENDING={common.no_command(pend, ',')}; NEXT=give the command (the agent records it, /agentive:update); until then the check is not required, unless review.pipeline.stages.RV1.require_checks in .agents/framework.yaml names it (then every PR waits for a human: give the command or remove it there)" if pend else ""


def handoff_facts(root, s):
    """What the script knows about a failing verify, for a handoff field the agent left as a placeholder (HANDOFFFILL)."""
    verbs = state.load_verify(root).get("verbs") or {}
    failing = ", ".join(sorted(k for k, x in verbs.items() if isinstance(x, dict) and not x.get("ok"))) or "verify"
    lines = [ln for ln in (s.get("last_fail_lines") or []) if ln.startswith("AGENTIVE_FAIL")]
    last = lines[-1] if lines else None
    nxt = last.split("; NEXT=", 1)[1] if last and "; NEXT=" in last else None
    return {"failure_reason": f"verify failing in {failing}" + (f"; last line: {last}" if last else ""),
            "untried": "not recorded by the agent",
            "next_step": ("the agent left no next step; the last failing line says NEXT=" + nxt) if nxt else
                         "the agent left no next step; read the log of the failing verb in .agents/state/verify.json, fix it, rerun .agents/bin/verb verify"}


def open_question_text(root, s, inp):
    """OPENMARK: the continuation text when a spec stage run with no human left an [OPEN] question and wrote no handoff; else None."""
    if s.get("open_question_nudged") or not nohuman.no_human(hook_input=inp):
        return None
    m = re.match(r"^spec/([a-z0-9]+(?:-[a-z0-9]+)*)$", hookio.current_branch(root) or "")
    if not m:
        return None
    spec = f"docs/plans/{m.group(1)}/spec.md"
    try:
        with open(os.path.join(root, spec), encoding="utf-8") as f:
            opened = common.open_questions(f.read())
    except OSError:
        return None
    if not opened or os.path.isfile(handoff.current_path(root)):
        return None
    first = re.sub(r"^[-*]\s+\[OPEN\]\s*", "", opened[0])[:200]
    return (f"OPEN_QUESTION: {spec} has {len(opened)} [OPEN] question(s) and this branch has no handoff. No human is present, so write it: "
            "sh .agents/skills/chain-spec/scripts/handoff.sh \"<the first [OPEN] question in your words>\" \"<what the human must decide>\" "
            f"(each at least {handoff.PLACEHOLDER_MIN} characters; it prints HANDOFF=<path>), then finish with /pr: the spec PR lists the [OPEN] lines. "
            f"First question: {first}; NEXT=run the handoff.sh command, then continue")


def handoff_report(root, s, inp):
    """HANDOFFREPORT: the HANDOFF line of the current branch's handoff when no human is present and it was written in this session."""
    started = s.get("started_at")
    if not started or not nohuman.no_human(hook_input=inp):
        return None
    path = handoff.current_path(root)
    doc = handoff.read(path)
    if not doc or str(doc.get("created_at") or "") < started:
        return None
    return handoff.report_line(os.path.relpath(path, root).replace(os.sep, "/"), doc)


def _finish(tool, msg, line=None):
    if tool == "claude":
        hookio.emit({"systemMessage": (line + "\n" + msg) if line else msg})
    return 0


def main(argv):
    tool = hookio.tool_arg(argv)
    inp = hookio.read_input()
    root = hookio.root_from(inp)
    manifest = hookio.load_manifest(root)
    if manifest is None:
        return 0
    sid = inp.get("session_id") or ""
    s = state.load_stop(root, sid)
    if s.get("handoff_requested"):
        handoff.fill_placeholders(root, handoff_facts(root, s))
        s["handoff_requested"] = False
        state.save_stop(root, s)
        line = handoff_report(root, s, inp)
        if line and tool == "claude":
            hookio.emit({"systemMessage": line})
        return 0
    nudge = open_question_text(root, s, inp)
    if nudge:
        s["open_question_nudged"] = True
        state.save_stop(root, s)
        if tool == "codex":
            hookio.emit({"decision": "block", "reason": nudge})
        else:
            hookio.emit({"hookSpecificOutput": {"hookEventName": "Stop", "additionalContext": nudge}})
        return 0
    sc = manifest.get("stop_conditions") or {}
    max_blocks = int(sc.get("max_blocks") or 8)
    rounds = int(sc.get("no_progress_rounds") or 2)
    hline = handoff_report(root, s, inp)
    dirty = sorted({e.get("slot") for e in state.load_dirty(root)["slots"] if e.get("slot")})
    dirty_note = f"; DIRTY={','.join(dirty)} (evidence changed); NEXT=/agentive:reprove" if dirty else ""
    some_pending = pending_note(manifest) if any(isinstance(v, dict) and v.get("cmd") and v.get("status") != "pending"
                                                 for v in (manifest.get("verbs") or {}).values()) else ""
    dirty_note += some_pending
    tree = hookio.content_tree(root)
    cached = state.load_verify(root)
    cached_verbs = cached.get("verbs") or {}
    if tree and cached_verbs and verb.fresh_for(cached, tree, manifest):
        ctotal = int(cached.get("total_failures") or 0)
        if ctotal == 0 and all(x.get("ok") for x in cached_verbs.values()):
            s.update(consecutive_blocks=0, failure_window=[], last_fail_count=0, last_reason="DONE", last_reset="verify_green")
            state.save_stop(root, s)
            return _finish(tool, "DONE (unchanged)" + dirty_note, hline)
        if int(s.get("consecutive_blocks") or 0) == 0:
            s["last_reason"] = "RED-UNCHANGED"
            state.save_stop(root, s)
            return _finish(tool, f"RED-UNCHANGED total_failures={max(ctotal, 1)}: no file changed since the last verify, which failed; "
                                 "NEXT=fix the failing verbs before relying on this tree (.agents/bin/verb verify reruns them)" + dirty_note, hline)
        r, v = None, cached                      # red and unchanged after a block: cached result, same NO-PROGRESS / CAP path
        out = list(s.get("last_fail_lines") or []) or [
            f"AGENTIVE_FAIL verb={k} log={x.get('log', 'none')}; NEXT=read the log" for k, x in sorted(cached_verbs.items()) if not x.get("ok")]
    else:
        r = subprocess.run([os.path.join(root, ".agents", "bin", "verb"), "verify"], cwd=root, capture_output=True, text=True)
        out = (r.stdout + r.stderr).strip().splitlines()
        v = state.load_verify(root)
    if not (v.get("verbs") or {}):
        s.update(consecutive_blocks=0, failure_window=[], last_fail_count=None, last_reason="PENDING")
        state.save_stop(root, s)
        return _finish(tool, PENDING_MSG + dirty_note, hline)
    total = int(v.get("total_failures") or 0)
    if r is not None and r.returncode == 0 and total == 0:
        s.update(consecutive_blocks=0, failure_window=[], last_fail_count=0, last_reason="DONE", last_reset="verify_green")
        state.save_stop(root, s)
        return _finish(tool, "DONE" + dirty_note, hline)
    total = max(total, 1)
    window = list(s.get("failure_window") or [])
    last = s.get("last_fail_count")
    if last is not None and total < last:
        window, s["consecutive_blocks"], s["last_reset"] = [], 0, "decreased"
    window.append(total)
    s["last_fail_count"] = total
    fails = [ln for ln in out if ln.startswith("AGENTIVE_FAIL")]
    s["last_fail_lines"] = fails[-4:]
    if len(window) >= rounds and all(window[i] >= window[i - 1] for i in range(len(window) - rounds + 1, len(window))):
        failing = ", ".join(sorted(k for k, x in (v.get("verbs") or {}).items() if not x.get("ok"))) or "verify"
        path = handoff.write(root, "no_progress",
                             failure_reason=f"(agent: fill in) verify failing in {failing}: total_failures {window[-rounds:]} not decreasing",
                             next_step="(agent: fill in) the next concrete step for whoever resumes",
                             untried="(agent: fill in)")
        s.update(handoff_requested=True, failure_window=[], last_reason="NO-PROGRESS", last_reset="after_no_progress",
                 consecutive_blocks=int(s.get("consecutive_blocks") or 0) + 1, total_blocks=int(s.get("total_blocks") or 0) + 1)
        state.save_stop(root, s)
        text = (f"NO-PROGRESS: verify total_failures did not decrease over {rounds} runs. A handoff was written to {path}. "
                "Fill its three fields (failure_reason, untried, next_step) with what you know now, then stop; a field left as '(agent: ...)' "
                "is filled by the script with facts and marked '(script)'. "
                + " ".join(fails[-4:]))
        if tool == "codex":
            hookio.emit({"decision": "block", "reason": text})
        else:
            hookio.emit({"hookSpecificOutput": {"hookEventName": "Stop", "additionalContext": text}})
        return 0
    s["failure_window"] = window
    if int(s.get("consecutive_blocks") or 0) >= max_blocks:
        s["last_reason"] = "CAP"
        state.save_stop(root, s)
        return _finish(tool, f"CAP: {max_blocks} consecutive Stop blocks; verify still fails (total_failures={total})", hline)
    s["consecutive_blocks"] = int(s.get("consecutive_blocks") or 0) + 1
    s["total_blocks"] = int(s.get("total_blocks") or 0) + 1
    s["last_reason"] = "BLOCK"
    state.save_stop(root, s)
    tail = fails or out[-20:]
    sys.stderr.write(f"verify failed (total_failures={total}); fix it before stopping:\n" + "\n".join(tail) + "\n")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
