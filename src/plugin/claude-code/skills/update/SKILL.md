---
name: update
description: Re-apply the current agentive templates to an already-initialized repository (after a plugin update or a stack change). Re-reads the evidence, overwrites unmodified generated files and regions, keeps and reports user-modified ones. Run only when the user asks for /agentive:update.
disable-model-invocation: true
argument-hint: "[--force] [--no-hooks] [--status]"
---

# /agentive:update

Idempotent re-apply for a repo that has the agentive manifest. It asks no question
(optional outputs follow `.agents/framework.yaml`), writes hooks, permissions, `.claude/settings.json`, `.codex/` and
CI without asking and never commits. There is no dry-run or preview step.

ENGINE: `python3 "${CLAUDE_PLUGIN_ROOT}/payload/engine/apply.py" --tool claude` (use this exact command in every command below). The CI AI review
tool is not taken from `--tool`: update keeps the tool init recorded and follows `review.pipeline.ci_tool` when the user sets it.

## 1. Mode and flags

1. Run `sh -c 'echo "AGENTIVE_HEADLESS=${AGENTIVE_HEADLESS:-} CI=${CI:-} GITHUB_ACTIONS=${GITHUB_ACTIONS:-}"'`.
   AGENTIVE_HEADLESS=1, or CI or GITHUB_ACTIONS set: no human is present; add `--headless` and ask nothing. A headless
   run writes the same files as an interactive one.
2. ARGUMENTS = `$ARGUMENTS`.
   - `--status`: run `ENGINE status --repo .`, show its `AGENTIVE_STATUS` lines (modified, missing, malformed,
     unchanged count, overrides in .agents/custom/, the /doctor prompt-audit pointer) and end. It writes nothing.
   - `--force`: pass it on. It overwrites user-modified generated files and regions (drift reason customized);
     tell the user first that the overwritten content is not kept (commit or back up). It never overwrites
     files that existed before the framework (no_baseline).
   - `--no-hooks`: pass it on; hook scripts and the settings/hooks.json wiring are left as they are.

## 2. Evidence and values

Run `ENGINE update --repo . --print-evidence` (writes nothing). A slot whose evidence changed is re-derived by the
engine; a pending slot becomes real only from evidence. Pinned values stay. Only when the evidence is ambiguous
write `${TMPDIR:-/tmp}/agentive-values.json` the same way /agentive:init does (verbs with evidence, seeds for lint,
typecheck and test).

## 3. Run

`ENGINE update --repo . --prove [--values ${TMPDIR:-/tmp}/agentive-values.json] [--force] [--no-hooks] [--headless]`

- exit 1: no agentive manifest; tell the user to run /agentive:init. End.
- No confirmation question. `.claude/settings.json` is merged by key (only hooks, permissions, sandbox, plansDirectory
  and env are agentive's; your own hook entries, permission entries and every other key stay) and written last, after
  a hook smoke check; a failure leaves it unwritten (`AGENTIVE_WARN hook_smoke_failed ...`). Invalid JSON is never
  written (`unparsable_json`).
- `AGENTIVE_PROOF_FAIL`: as in /agentive:init (read the log, fix the values, `ENGINE reprove --repo . --values ...`).

## 4. Drift report

`AGENTIVE_DRIFT id=<id> reason=<customized|no_baseline>` items were kept, not overwritten. Read
`.agents/state/drift-report.json` and show each item: id, reason, the difference summary, and `override_path`
(a customization that should survive updates belongs there, in `.agents/custom/`). The user says in natural
language which items to accept; run `ENGINE update --repo . --accept <id> [<id>...]` (an accepted `.claude/settings.json` item replaces only the agentive hook entries). Items not accepted stay and are listed again on the
next run. Also show `stale[]` (region lines naming deleted or renamed paths, and doc pointers whose file is gone; human-written lines are never deleted) and `errors[]` (malformed markers,
invalid overrides: the file was left untouched).

## 5. Final report

Read `${CLAUDE_PLUGIN_ROOT}/payload/engine/final-report.md` and report in the order it gives, starting with the hooks and guard status. Lines marked (update) apply to this command; skip the lines marked (init).
