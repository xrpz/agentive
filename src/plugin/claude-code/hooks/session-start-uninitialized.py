#!/usr/bin/env python3
# agentive-unit: pc.hooks
# design: spec.init_apply.session_start (stdout_uninitialized, non_interactive), spec.distribution.claude_code.install_sequence (step 4), spec.no_human_run
"""Plugin SessionStart hook: in a repo without .agents/manifest.yaml print the uninitialized state line.
An initialized repo prints nothing here (its own .agents/hooks/session-start-notice.py reports the ready state).
No human present (AGENTIVE_HEADLESS=1, CI or GITHUB_ACTIONS; SessionStart input has no permission_mode): NEXT=none."""
import json
import os
import subprocess
import sys


def main():
    try:
        inp = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        inp = {}
    cwd = inp.get("cwd") if isinstance(inp, dict) and os.path.isdir(inp.get("cwd") or "") else os.getcwd()
    try:
        r = subprocess.run(["git", "-C", cwd, "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=10)
        root = r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else cwd
    except (OSError, subprocess.SubprocessError):
        root = cwd
    if os.path.isfile(os.path.join(root, ".agents", "manifest.yaml")):
        return 0
    env = os.environ
    no_human = env.get("AGENTIVE_HEADLESS", "").strip() == "1" or any(
        env.get(k, "").strip().lower() in ("1", "true", "yes") for k in ("CI", "GITHUB_ACTIONS"))
    if no_human:
        print("AGENTIVE_STATE=uninitialized; NEXT=none")
    else:
        print("AGENTIVE_STATE=uninitialized; NEXT=ask_user_to_run:/agentive:init; DO_NOT=reproduce init steps")
    return 0


if __name__ == "__main__":
    sys.exit(main())
