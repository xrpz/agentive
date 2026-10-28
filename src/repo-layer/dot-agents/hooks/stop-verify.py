#!/usr/bin/env python3
# agentive-unit: rl.hooks
# design: spec.stop_hook, spec.stop_conditions, spec.handoff, contracts[stop-state], contracts[verify-result]
"""Stop hook (Claude Code and Codex): run inner verify and decide DONE / PENDING / NO-PROGRESS / CAP / block.

1. stop.json handoff_requested (this Stop follows the one NO-PROGRESS continuation): clear it, exit 0, no rerun
2. `.agents/bin/verb verify` -> verify.json (pending verbs skipped, not failures)
3. no verb has a command: exit 0, PENDING notice (never DONE)
4. verify green: reset window and counter, exit 0, DONE
5. total_failures decreased: reset (window restarts, counter 0)
6. last N (manifest stop_conditions.no_progress_rounds) totals not decreasing: NO-PROGRESS -> branch handoff,
   handoff_requested, window emptied, continue once (Claude: Stop additionalContext; Codex: decision block)
7. consecutive_blocks >= manifest stop_conditions.max_blocks (default 8): exit 0, CAP
8. else consecutive_blocks += 1, exit 2 with the AGENTIVE_FAIL lines on stderr
stop_hook_active is informational only. UserPromptSubmit never resets the counter.
Dirty slots (FileChanged) are reported with NEXT=/agentive:reprove [설계 판단: the engine is in the plugin].
"""
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))
from agentive import handoff, hookio, state  # noqa: E402

PENDING_MSG = "PENDING: no stack evidence yet; scaffold the project, then run /agentive:update"


def _finish(tool, msg):
    if tool == "claude":
        hookio.emit({"systemMessage": msg})
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
        s["handoff_requested"] = False
        state.save_stop(root, s)
        return 0
    sc = manifest.get("stop_conditions") or {}
    max_blocks = int(sc.get("max_blocks") or 8)
    rounds = int(sc.get("no_progress_rounds") or 2)
    r = subprocess.run([os.path.join(root, ".agents", "bin", "verb"), "verify"], cwd=root, capture_output=True, text=True)
    out = (r.stdout + r.stderr).strip().splitlines()
    v = state.load_verify(root)
    dirty = sorted({e.get("slot") for e in state.load_dirty(root)["slots"] if e.get("slot")})
    dirty_note = f"; DIRTY={','.join(dirty)} (evidence changed); NEXT=/agentive:reprove" if dirty else ""
    if not (v.get("verbs") or {}):
        s.update(consecutive_blocks=0, failure_window=[], last_fail_count=None, last_reason="PENDING")
        state.save_stop(root, s)
        return _finish(tool, PENDING_MSG + dirty_note)
    total = int(v.get("total_failures") or 0)
    if r.returncode == 0 and total == 0:
        s.update(consecutive_blocks=0, failure_window=[], last_fail_count=0, last_reason="DONE", last_reset="verify_green")
        state.save_stop(root, s)
        return _finish(tool, "DONE" + dirty_note)
    total = max(total, 1)
    window = list(s.get("failure_window") or [])
    last = s.get("last_fail_count")
    if last is not None and total < last:
        window, s["consecutive_blocks"], s["last_reset"] = [], 0, "decreased"
    window.append(total)
    s["last_fail_count"] = total
    fails = [ln for ln in out if ln.startswith("AGENTIVE_FAIL")]
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
                "Fill its three fields (failure_reason, untried, next_step) with what you know now, then stop. "
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
        return _finish(tool, f"CAP: {max_blocks} consecutive Stop blocks; verify still fails (total_failures={total})")
    s["consecutive_blocks"] = int(s.get("consecutive_blocks") or 0) + 1
    s["total_blocks"] = int(s.get("total_blocks") or 0) + 1
    s["last_reason"] = "BLOCK"
    state.save_stop(root, s)
    tail = fails or out[-20:]
    sys.stderr.write(f"verify failed (total_failures={total}); fix it before stopping:\n" + "\n".join(tail) + "\n")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
