#!/usr/bin/env python3
# agentive-unit: rl.hooks
# design: spec.enforcement.hooks.post_tool_use
"""PostToolUse lint (matcher Edit|Write; Codex: apply_patch|Edit|Write): runs `.agents/bin/verb lint`.
A pending lint verb is skipped silently. On failure exit 2 with the excerpt and the AGENTIVE_FAIL line on stderr
(Claude Code shows PostToolUse exit-2 stderr to Claude; the tool already ran)."""
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))
from agentive import hookio  # noqa: E402


def main(argv):
    inp = hookio.read_input()
    root = hookio.root_from(inp)
    manifest = hookio.load_manifest(root)
    lint = ((manifest or {}).get("verbs") or {}).get("lint")
    if not isinstance(lint, dict) or not lint.get("cmd") or lint.get("status") == "pending":
        return 0
    fp = (inp.get("tool_input") or {}).get("file_path")
    if isinstance(fp, str) and fp:
        ap = os.path.realpath(fp if os.path.isabs(fp) else os.path.join(root, fp))
        rel = os.path.relpath(ap, os.path.realpath(root))
        if rel.startswith("..") or rel.startswith(os.path.join(".agents", "state")):
            return 0
    r = subprocess.run([os.path.join(root, ".agents", "bin", "verb"), "lint"], cwd=root, capture_output=True, text=True)
    if r.returncode == 0:
        return 0
    lines = (r.stdout + r.stderr).strip().splitlines()
    sys.stderr.write("\n".join(lines[-25:]) + "\n")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
