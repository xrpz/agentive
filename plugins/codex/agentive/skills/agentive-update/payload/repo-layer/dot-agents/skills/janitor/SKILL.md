---
name: janitor
description: Small automated cleanup - for each kind a human listed in .agents/manifest.yaml janitor.kinds, fix at most batch_max items found by its deterministic sensor and open one PR labeled agentive-janitor:<kind>. Off by default (no kinds). Use in a headless or Routines run set up for janitor work, or when a human asks for a janitor pass.
disable-model-invocation: false
---

# /janitor

1. Read `janitor.kinds` in `.agents/manifest.yaml`. Empty or absent: print `janitor: no kinds configured (off by default); NEXT=none` and stop. Kinds, batch_max and frequency are widened only by a human editing that list.
2. For each kind `{id, sensor, batch_max}`:
   1. `sh scripts/gate.sh <id>`. `SKIP <id>: #N open` (exit 1): make that your final line for this kind and go to the next kind. Exit 2: report its NEXT and stop.
   2. Run the sensor command exactly as written (through `.agents/bin/verb` when it is a verb). Take at most batch_max items from its output; nothing to fix -> next kind.
   3. Branch `janitor/<id>-<YYYYMMDD>` from the default branch; fix only those items, one kind per PR; run /verify; commit through the normal commit procedure.
   4. Open the PR with /pr, then label it: `gh api -X POST repos/{owner}/{repo}/issues/<n>/labels -f "labels[]=agentive-janitor:<id>"`. It is always tier 2: a human reads and merges it.
3. Report one line per kind: `PR #<n>`, `SKIP ...` or `nothing to fix`.

Run headless with the agentive standard command (`AGENTIVE_HEADLESS=1 claude -p "/janitor" --permission-mode dontAsk ...`); in a Routines run the routine's cloud environment sets `AGENTIVE_HEADLESS=1`. Never merge, never push to the default branch, and never touch hooks, permissions, CI or review config in a janitor PR.
