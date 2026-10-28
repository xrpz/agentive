---
name: init
description: First-time agentive setup of this repository. Reads the repo evidence (code, lockfiles, package files, CI config, README), writes the agentive repo layer (AGENTS.md and CLAUDE.md regions, .agents/, hooks, permissions, CI) and proves the verb slots. Run only when the user asks for /agentive:init; an initialized repo uses /agentive:update.
disable-model-invocation: true
argument-hint: "[--managed-review-copy | --no-managed-review-copy]"
---

# /agentive:init

First-time setup only (spec.init_apply). The deterministic work is done by the agentive engine; you do the parts
that need judgment: the one question, ambiguous slots, proof seeds, the U2 confirmation and the final report.
Never choose the stack, scaffold, ask what to build, or commit. Never edit the project's source, tests, package
files or lockfiles.

ENGINE: `python3 "${CLAUDE_PLUGIN_ROOT}/payload/engine/apply.py"` (use this exact path in every command below).

## 1. Mode and flags

1. Run `sh -c 'echo "AGENTIVE_HEADLESS=${AGENTIVE_HEADLESS:-} CI=${CI:-} GITHUB_ACTIONS=${GITHUB_ACTIONS:-}"'`.
   If AGENTIVE_HEADLESS is 1, or CI or GITHUB_ACTIONS is set, this is a run with no human present: add
   `--headless` to every engine command and ask nothing (no question, no confirmation) (spec.no_human_run).
2. Read the flags from the arguments: ARGUMENTS = `$ARGUMENTS`. Pass `--managed-review-copy` or
   `--no-managed-review-copy` through unchanged. If both are given, the engine stops; report that and end.

## 2. Evidence

Run `ENGINE init --repo . --print-evidence`. It prints `AGENTIVE_EVIDENCE <json>` and writes nothing.
- `ecosystems` empty: a greenfield repo. That is a normal input; every stack verb stays pending. Go on.
- A verb the evidence does not show stays pending: never write a command without evidence, not even an
  ecosystem default for an ecosystem the evidence does not show (spec.init_apply.contract.evidence_scope).
- Only when the evidence is ambiguous (two candidate commands for one verb, for example a `test` and a `test:unit`
  script, or several ecosystems): pick by the tie-break "most used in existing code, else the ecosystem's official
  default" (D6) and put that verb in the values file (step 4) with its evidence.
- A value the user stated in this conversation ("our test command is ...") is pinned:
  `"pinned": {"by": "user", "at": "<RFC 3339 now>", "text": "<the user's words>"}` on that verb.

## 3. The only question (REVIEW.md copy)

Skip it when a flag was given, when `.agents/framework.yaml` already has `review.managed_code_review_copy`, or in
a headless run. Otherwise ask exactly this one plain yes/no question and wait for the reply:

> Do you use Claude managed Code Review (Team/Enterprise; generally available since 2026-10-15)? If yes, init also
> generates REVIEW.md as a copy of code_review.md. (default: No, only code_review.md)

Yes -> `--managed-review-copy`; anything else -> `--no-managed-review-copy`.

## 4. Values and proof seeds

Write `${TMPDIR:-/tmp}/agentive-values.json` (outside the repo) with only what you decided in step 2, plus one
seed per verb that has a command among lint, typecheck and test, so the proof can show the command fails on a
seeded violation (D11):

```json
{"verbs": {"test": {"cmd": "npm run test:unit", "tier": "inner",
                    "evidence": {"rank": 1, "source": "package.json scripts.test:unit", "files": ["package.json"]}}},
 "seeds": {"test": {"path": "tests/agentive_seed_test.py", "content": "def test_seed():\n    assert False\n"},
           "lint": {"path": "src/agentive_seed_lint.py", "content": "import os\n"}}}
```

A seed path must not exist yet and must sit where the command looks (the engine writes it, runs the verb once,
then deletes it). Use the stack's own failing form (a failing test, a lint violation, a type error).

## 5. Run the engine

`ENGINE init --repo . --prove --values ${TMPDIR:-/tmp}/agentive-values.json [--managed-review-copy|--no-managed-review-copy] [--headless]`

- exit 1 (`AGENTIVE_STOP`): the repo is already initialized. Show the `AGENTIVE_STATUS` lines and tell the user to
  run /agentive:update. Do not reproduce init steps. End.
- exit 2 (`AGENTIVE_ERROR`): show the line and its NEXT. End.
- `AGENTIVE_CONFIRM category=<c> path=<p>` lines (U2 confirm_scope: hooks, permissions, CI, .claude/settings.json,
  .codex/, review config) were NOT written. Interactive: list them grouped by category and ask the human one
  yes/no question ("write these files?"). Yes -> rerun the same command with `--confirm-all` added (or
  `--confirm <path>...` for the ones the human named). No -> leave them; they stay in
  .agents/state/pending-confirmation.json and the next session start shows them again. Headless: they are
  recorded there for the next interactive session; do nothing else.
- `AGENTIVE_PROOF_FAIL verb=<v> ... log=<path>`: read the log with the NEXT command, correct the verb in the values
  file (or give no command when the evidence does not support one), then run
  `ENGINE reprove --repo . --values ${TMPDIR:-/tmp}/agentive-values.json`. Two failed attempts: report the slot
  as unproved and move on.

## 6. Final report (to the user)

1. The changed files: every path after `AGENTIVE_CHANGED:`.
2. Every `AGENTIVE_REPORT` and `AGENTIVE_WARN` line, verbatim (Codex hook trust, managed-settings snippet,
   GitHub settings, push protection, not-expressible ask items).
3. Pending verbs (`AGENTIVE_PENDING`): "근거 없음 · 대기"; with no stack evidence, tell the user to scaffold the
   project with their own agent and then run /agentive:update (Codex: $agentive-update).
4. Drift or conflicts (`AGENTIVE_DRIFT`): existing files were kept; the proposed diffs are under
   .agents/state/conflicts/.
5. init never commits: the human commits, or you commit through the normal commit procedure when asked.
