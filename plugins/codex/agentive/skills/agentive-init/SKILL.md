---
name: agentive-init
description: First-time agentive setup of this repository in Codex. Reads the repo evidence, writes the agentive repo layer (AGENTS.md region, .agents/, .codex/ hooks, rules and config, CI) and proves the verb slots. Use only when the user explicitly runs $agentive-init; an initialized repo uses $agentive-update.
disable-model-invocation: true
---

# $agentive-init

First-time setup only (spec.init_apply). The deterministic work is done by the agentive engine bundled with this
skill; you do the judgment parts: the one question, ambiguous slots, proof seeds, the U2 confirmation and the
final report. Never choose the stack, scaffold, ask what to build, or commit. Never edit the project's source,
tests, package files or lockfiles.

ENGINE: `python3 <skill dir>/payload/engine/apply.py --tool codex`, where `<skill dir>` is the directory that holds
this SKILL.md (in the Codex-only bootstrap: `.agents/skills/agentive-init`). Use that full path in every command.

## 1. Mode and flags

1. Run `sh -c 'echo "AGENTIVE_HEADLESS=${AGENTIVE_HEADLESS:-} CI=${CI:-} GITHUB_ACTIONS=${GITHUB_ACTIONS:-}"'`.
   AGENTIVE_HEADLESS=1, or CI or GITHUB_ACTIONS set: no human is present; add `--headless` and ask nothing.
2. Flags: Codex passes no argument string to skills (UV-23), so read them from the user's message: "--no-managed-review-copy"
   or "no REVIEW.md" -> `--no-managed-review-copy`; "--managed-review-copy" or "also REVIEW.md" -> `--managed-review-copy`.

## 2. Evidence

Run `ENGINE init --repo . --print-evidence` (prints `AGENTIVE_EVIDENCE <json>`, writes nothing). No ecosystem is a
normal input: stack verbs stay pending. Never write a command the evidence does not show. Only for ambiguous
evidence choose by "most used in existing code, else the ecosystem's official default" (D6) and put the verb in the
values file. A value the user stated is pinned (`"pinned": {"by": "user", "at": "<now>", "text": "<words>"}`).

## 3. The only question (REVIEW.md copy)

Skip it when a flag was given, `.agents/framework.yaml` already has `review.managed_code_review_copy`, or the run
is headless. Otherwise ask exactly: "Do you use Claude managed Code Review (Team/Enterprise; generally available
since 2026-10-15)? If yes, init also generates REVIEW.md as a copy of code_review.md. (default: No, only
code_review.md)". Yes -> `--managed-review-copy`; anything else -> `--no-managed-review-copy`.

## 4. Values and proof seeds

Write `${TMPDIR:-/tmp}/agentive-values.json` (outside the repo):
`{"verbs": {<only the verbs you decided>}, "seeds": {"test": {"path": "<new file>", "content": "<a failing test>"}, "lint": {...}, "typecheck": {...}}}`.
A seed path must not exist; the engine writes it, runs the verb once and deletes it.

## 5. Run

`ENGINE init --repo . --prove --values ${TMPDIR:-/tmp}/agentive-values.json [flag] [--headless]`

- exit 1: already initialized; show the `AGENTIVE_STATUS` lines and tell the user to run $agentive-update. End.
- exit 2: show the `AGENTIVE_ERROR` line and its NEXT. End.
- `AGENTIVE_CONFIRM` lines: confirm_scope files (hooks, permissions, CI, .claude/settings.json, .codex/, review
  config) were not written. Ask the human one yes/no question listing them; yes -> rerun the same command with
  `--confirm-all` (or `--confirm <path>...`). Headless: they stay in .agents/state/pending-confirmation.json.
- `AGENTIVE_PROOF_FAIL`: read the log with its NEXT command, fix the values file, run the init command once more
  with `--confirm-all` only if the human already said yes.

## 6. Final report

The changed files (every path after `AGENTIVE_CHANGED:`), every `AGENTIVE_REPORT` and `AGENTIVE_WARN` line
verbatim, pending verbs ("근거 없음 · 대기"; greenfield: scaffold with your own agent, then $agentive-update), kept
drift or conflicts. Tell the human to trust the agentive hooks once in Codex `/hooks` (untrusted project hooks do
not run). init never commits.
