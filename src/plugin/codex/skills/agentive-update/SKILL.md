---
name: agentive-update
description: Re-apply the current agentive templates to an already-initialized repository in Codex (after a plugin upgrade or a stack change). Overwrites unmodified generated files and regions, keeps and reports user-modified ones. Use only when the user explicitly runs $agentive-update.
disable-model-invocation: true
---

# $agentive-update

Idempotent re-apply for a repo with the agentive manifest (spec.init_apply.update). It asks no question and never
commits. There is no dry-run or preview step.

ENGINE: `python3 <skill dir>/payload/engine/apply.py --tool codex`, where `<skill dir>` is the directory that holds
this SKILL.md (in the Codex-only bootstrap: `.agents/skills/agentive-update`).

1. Mode: run `sh -c 'echo "AGENTIVE_HEADLESS=${AGENTIVE_HEADLESS:-} CI=${CI:-} GITHUB_ACTIONS=${GITHUB_ACTIONS:-}"'`;
   AGENTIVE_HEADLESS=1, CI or GITHUB_ACTIONS set -> add `--headless`, ask nothing.
2. Flags come from the user's message (Codex passes no argument string, UV-23): "--status" or "status only" ->
   run `ENGINE status --repo .`, show the `AGENTIVE_STATUS` lines and end (writes nothing). "--force" or "overwrite
   my edits too" -> add `--force` after telling the user the overwritten content is not kept.
3. Run `ENGINE update --repo . --print-evidence` (writes nothing); only for ambiguous evidence write
   `${TMPDIR:-/tmp}/agentive-values.json` as $agentive-init does.
4. Run `ENGINE update --repo . --prove [--values ...] [--force] [--headless]`.
   - exit 1: no agentive manifest -> tell the user to run $agentive-init.
   - `AGENTIVE_CONFIRM` lines: ask the human one yes/no question listing them; yes -> rerun with `--confirm-all`.
   - `AGENTIVE_DRIFT` items: show `.agents/state/drift-report.json` (id, reason, difference summary,
     override_path in .agents/custom/). For the items the user accepts run `ENGINE update --repo . --accept <id>...`.
5. Report the changed files (after `AGENTIVE_CHANGED:`), the `AGENTIVE_REPORT` / `AGENTIVE_WARN` lines verbatim,
   pending verbs and kept drift items. If .codex/hooks.json changed, the human trusts the new hook hash in `/hooks`.
