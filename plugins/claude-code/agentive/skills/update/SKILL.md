---
name: update
description: Re-apply the current agentive templates to an already-initialized repository (after a plugin update or a stack change). Re-reads the evidence, overwrites unmodified generated files and regions, keeps and reports user-modified ones. Run only when the user asks for /agentive:update.
disable-model-invocation: true
argument-hint: "[--force] [--status]"
---

# /agentive:update

Idempotent re-apply for a repo that has the agentive manifest (spec.init_apply.update). It asks no question
(optional outputs follow `.agents/framework.yaml`) and never commits. There is no dry-run or preview step.

ENGINE: `python3 "${CLAUDE_PLUGIN_ROOT}/payload/engine/apply.py"` (use this exact path in every command below).

## 1. Mode and flags

1. Run `sh -c 'echo "AGENTIVE_HEADLESS=${AGENTIVE_HEADLESS:-} CI=${CI:-} GITHUB_ACTIONS=${GITHUB_ACTIONS:-}"'`.
   AGENTIVE_HEADLESS=1, or CI or GITHUB_ACTIONS set: no human is present; add `--headless` and ask nothing.
2. ARGUMENTS = `$ARGUMENTS`.
   - `--status`: run `ENGINE status --repo .`, show its `AGENTIVE_STATUS` lines (modified, missing, malformed,
     unchanged count, overrides in .agents/custom/, the /doctor prompt-audit pointer) and end. It writes nothing.
   - `--force`: pass it on. It overwrites user-modified generated files and regions (drift reason customized);
     tell the user first that the overwritten content is not kept (commit or back up). It never overwrites
     files that existed before the framework (no_baseline) and never skips the U2 confirmation.

## 2. Evidence and values

Run `ENGINE update --repo . --print-evidence` (writes nothing). A slot whose evidence changed is re-derived by the
engine; a pending slot becomes real only from evidence. Pinned values stay. Only when the evidence is ambiguous
write `${TMPDIR:-/tmp}/agentive-values.json` the same way /agentive:init does (verbs with evidence, seeds for lint,
typecheck and test).

## 3. Run

`ENGINE update --repo . --prove [--values ${TMPDIR:-/tmp}/agentive-values.json] [--force] [--headless]`

- exit 1: no agentive manifest; tell the user to run /agentive:init. End.
- `AGENTIVE_CONFIRM` lines: confirm_scope files (hooks, permissions, CI, .claude/settings.json, .codex/, review
  config) were not written. Ask the human one yes/no question listing them; yes -> rerun with `--confirm-all`
  (or `--confirm <path>...`). Headless: they stay in .agents/state/pending-confirmation.json.
- `AGENTIVE_PROOF_FAIL`: as in /agentive:init (read the log, fix the values, `ENGINE reprove --repo . --values ...`).

## 4. Drift report

`AGENTIVE_DRIFT id=<id> reason=<customized|no_baseline>` items were kept, not overwritten. Read
`.agents/state/drift-report.json` and show each item: id, reason, the difference summary, and `override_path`
(a customization that should survive updates belongs there, in `.agents/custom/`). The user says in natural
language which items to accept; run `ENGINE update --repo . --accept <id> [<id>...]` (plus `--confirm-all` when an
accepted item is a confirm_scope file and the human said yes). Items not accepted stay and are listed again on the
next run. Also show `stale[]` (region lines naming deleted or renamed paths) and `errors[]` (malformed markers,
invalid overrides: the file was left untouched).

## 5. Final report

The changed files (every path after `AGENTIVE_CHANGED:`), every `AGENTIVE_REPORT` / `AGENTIVE_WARN` line verbatim,
pending verbs, kept drift items. update never commits.
