---
name: reprove
description: Re-derive and re-prove the agentive verb slots whose evidence changed (the dirty slots the FileChanged hook recorded in .agents/state/dirty.json, or unproved ones). Use it when the Stop hook or session start reports DIRTY=... or AGENTIVE_UNPROVED, or after a package file, lockfile or build config changed.
disable-model-invocation: false
argument-hint: "[<verb>...]"
---

# /agentive:reprove

Repeats what the Stop hook reports for dirty slots (spec.init_apply.reprove_on_change). It writes only
`.agents/manifest.yaml` (verbs and paths, with new proofs) and `.agents/state/dirty.json`; the AGENTS.md region and
the settings catch up at the next /agentive:update.

ENGINE: `python3 "${CLAUDE_PLUGIN_ROOT}/payload/engine/apply.py"` (use this exact path below).

1. ARGUMENTS = `$ARGUMENTS`. Verb names given there are re-derived and re-proved even when they are not dirty
   (write them into the values file as below, or leave them to the evidence).
2. Run `ENGINE reprove --repo . --print-evidence` (writes nothing) and read the dirty slots in
   `.agents/state/dirty.json`. Only when the evidence for a dirty verb is ambiguous, write
   `${TMPDIR:-/tmp}/agentive-values.json` with that verb (cmd, tier, evidence) and a seed for lint, typecheck or
   test (same format as /agentive:init). Never invent a command without evidence: such a verb stays pending.
3. Run `ENGINE reprove --repo . [--values ${TMPDIR:-/tmp}/agentive-values.json]`.
   - exit 1: no agentive manifest; tell the user to run /agentive:init.
   - `AGENTIVE_PROOF verb=<v> result=pass|fail` per verb; on `AGENTIVE_PROOF_FAIL` read the log with its NEXT
     command, correct the values file and run step 3 once more; still failing -> report the slot as unproved.
4. Report the proved and failed verbs and that `.agents/manifest.yaml` changed; it is committed through the normal
   commit procedure (reprove never commits).
