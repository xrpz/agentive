---
name: agentive-update
description: Re-apply the current agentive templates to an already-initialized repository in Codex (after a plugin upgrade or a stack change). Overwrites unmodified generated files and regions, keeps and reports user-modified ones. Use only when the user explicitly runs $agentive-update.
disable-model-invocation: true
---

# $agentive-update

Idempotent re-apply for a repo with the agentive manifest. It asks no question and never
commits. It writes hooks, permissions, `.codex/`, `.claude/settings.json` and CI without asking. There is no dry-run or preview step.

ENGINE: `python3 <skill dir>/payload/engine/apply.py --tool codex`, where `<skill dir>` is the directory that holds
this SKILL.md (in the Codex-only bootstrap: `.agents/skills/agentive-update`). The CI AI review tool is not taken
from `--tool`: update keeps the tool init recorded and follows `review.pipeline.ci_tool` when the user sets it.

1. Mode: run `sh -c 'echo "AGENTIVE_HEADLESS=${AGENTIVE_HEADLESS:-} CI=${CI:-} GITHUB_ACTIONS=${GITHUB_ACTIONS:-}"'`;
   AGENTIVE_HEADLESS=1, CI or GITHUB_ACTIONS set -> add `--headless`, ask nothing (same files as an interactive run).
2. Flags come from the user's message (Codex passes no argument string): "--status" or "status only" ->
   run `ENGINE status --repo .`, show the `AGENTIVE_STATUS` lines and end (writes nothing). "--force" or "overwrite
   my edits too" -> add `--force` after telling the user the overwritten content is not kept. "--no-hooks" -> add `--no-hooks`.
3. Run `ENGINE update --repo . --print-evidence` (writes nothing); only for ambiguous evidence write
   `${TMPDIR:-/tmp}/agentive-values.json` as $agentive-init does.
4. Run `ENGINE update --repo . --prove [--values ...] [--force] [--no-hooks] [--headless]`.
   - exit 1: no agentive manifest -> tell the user to run $agentive-init.
   - `AGENTIVE_DRIFT` items: show `.agents/state/drift-report.json` (id, reason, difference summary,
     override_path in .agents/custom/). For the items the user accepts run `ENGINE update --repo . --accept <id>...`.
5. Final report: read `<skill dir>/payload/engine/final-report.md` and report in the order it gives, starting with the hooks and guard status. Lines marked (update) apply to this command; skip the lines marked (init). If .codex/hooks.json changed, the human trusts the new hook hash in `/hooks`.
