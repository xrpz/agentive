---
name: agentive-init
description: First-time agentive setup of this repository in Codex. Reads the repo evidence, writes the agentive repo layer (AGENTS.md region, .agents/, .codex/ hooks, rules and config, CI) and proves the verb slots. Use only when the user explicitly runs $agentive-init; an initialized repo uses $agentive-update.
disable-model-invocation: true
---

# $agentive-init

First-time setup only. The deterministic work is done by the agentive engine bundled with this
skill; you do the judgment parts: the one question, ambiguous slots, proof seeds and the final report. init writes
every file of the repo layer (hooks, permissions, `.codex/`, `.claude/settings.json`, CI, review config) without
asking. Never choose the stack, scaffold, ask what to build, or commit. Never edit the project's source,
tests, package files or lockfiles.

ENGINE: `python3 <skill dir>/payload/engine/apply.py --tool codex`, where `<skill dir>` is the directory that holds
this SKILL.md (in the Codex-only bootstrap: `.agents/skills/agentive-init`). Use that full path in every command.
`--tool codex` records Codex as the tool that ran init: it is the default tool of the CI AI review.
No question is asked about it; the user can change it later with
`review.pipeline.ci_tool` in `.agents/framework.yaml`.

## 1. Mode and flags

1. Run `sh -c 'echo "AGENTIVE_HEADLESS=${AGENTIVE_HEADLESS:-} CI=${CI:-} GITHUB_ACTIONS=${GITHUB_ACTIONS:-}"'`.
   AGENTIVE_HEADLESS=1, or CI or GITHUB_ACTIONS set: no human is present; add `--headless` and ask nothing. A
   headless run writes the same files as an interactive one.
2. Flags: Codex passes no argument string to skills, so read them from the user's message: "--no-managed-review-copy"
   or "no REVIEW.md" -> `--no-managed-review-copy`; "--managed-review-copy" or "also REVIEW.md" -> `--managed-review-copy`. "--no-hooks" or "no hooks" -> add `--no-hooks` (no hook scripts, no
   `.codex/hooks.json` / `.claude/settings.json` wiring; everything else is written).

## 2. Evidence

Run `ENGINE init --repo . --print-evidence` (prints `AGENTIVE_EVIDENCE <json>`, writes nothing). No ecosystem is a
normal input: stack verbs stay pending. Never write a command the evidence does not show. Only for ambiguous
evidence choose by "most used in existing code, else the ecosystem's official default" and put the verb in the
values file. A value the user stated is pinned (`"pinned": {"by": "user", "at": "<now>", "text": "<words>"}`).

## 3. The only question (REVIEW.md copy)

Skip it when a flag was given, `.agents/framework.yaml` already has `review.managed_code_review_copy`, or the run
is headless. Otherwise ask exactly: "Do you use Claude managed Code Review (Team/Enterprise; generally
available)? If yes, init also generates REVIEW.md as a copy of code_review.md. (default: No, only
code_review.md)". Yes -> `--managed-review-copy`; anything else -> `--no-managed-review-copy`.

## 4. Values and proof seeds

Write `${TMPDIR:-/tmp}/agentive-values.json` (outside the repo):
`{"verbs": {<only the verbs you decided>}, "seeds": {"test": {"path": "<new file>", "content": "<a failing test>"}, "lint": {...}, "typecheck": {...}}}`.
A `lint` verb may carry `"file_args": false` (a whole-project command: the edit hook skips it, Stop runs it once) and `"exts"`; left out, the engine derives both from the command and the ecosystem (unknown command: `file_args` false).
A lint or typecheck command that walks the whole repository must keep `.agents`, `.claude` and `.codex` out (`ruff check --extend-exclude .agents,.claude,.codex .`, `flake8 --extend-exclude .agents,.claude,.codex`, `eslint --ignore-pattern .agents/ --ignore-pattern .claude/ --ignore-pattern .codex/ .`, or the source and test directories by name); otherwise verify lints the installed layer.
A seed path must not exist; the engine writes it, runs the verb once and deletes it.

## 5. Run

`ENGINE init --repo . --prove --values ${TMPDIR:-/tmp}/agentive-values.json [flag] [--no-hooks] [--headless]`

- exit 1: already initialized; show the `AGENTIVE_STATUS` lines and tell the user to run $agentive-update. End.
- exit 2: show the `AGENTIVE_ERROR` line and its NEXT. End.
- No confirmation question. `.codex/hooks.json` and `.claude/settings.json` are merged by key (agentive owns only its
  hook entries and, in settings.json, permissions, sandbox, plansDirectory and env) and written last, after every hook
  ran once with a sample input and exited 0; a failure leaves them unwritten (`AGENTIVE_WARN hook_smoke_failed`).
  Invalid JSON is never written (`unparsable_json`). An existing `.codex/config.toml` is kept (TOML is not merged):
  report its `AGENTIVE_WARN` and the proposed diff.
- `AGENTIVE_PROOF_FAIL`: read the log with its NEXT command, fix the values file, then run
  `ENGINE reprove --repo . --values ${TMPDIR:-/tmp}/agentive-values.json`.

## 6. Final report

Read `<skill dir>/payload/engine/final-report.md` and report in the order it gives, starting with the hooks and guard status. Lines marked (init) apply to this command; skip the lines marked (update). Then tell the human to trust the agentive hooks once in Codex `/hooks` (untrusted project hooks do not run).
