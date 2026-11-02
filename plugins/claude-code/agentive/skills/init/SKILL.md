---
name: init
description: First-time agentive setup of this repository. Reads the repo evidence (code, lockfiles, package files, CI config, README), writes the agentive repo layer (AGENTS.md and CLAUDE.md regions, .agents/, hooks, permissions, CI) and proves the verb slots. Run only when the user asks for /agentive:init; an initialized repo uses /agentive:update.
disable-model-invocation: true
argument-hint: "[--managed-review-copy | --no-managed-review-copy] [--no-hooks]"
---

# /agentive:init

First-time setup only. The deterministic work is done by the agentive engine; you do the parts
that need judgment: the one question, ambiguous slots, proof seeds and the final report. init writes every file of
the repo layer, hooks, permissions, `.claude/settings.json`, `.codex/` and CI included, without asking;
it asks the one question below and nothing else.
Never choose the stack, scaffold, ask what to build, or commit. Never edit the project's source, tests, package
files or lockfiles.

ENGINE: `python3 "${CLAUDE_PLUGIN_ROOT}/payload/engine/apply.py" --tool claude` (use this exact command in every command below). `--tool claude`
records Claude Code as the tool that ran init: it is the default tool of the CI AI review.
No question is asked about it; the user can change it later with `review.pipeline.ci_tool` in `.agents/framework.yaml`.

## 1. Mode and flags

1. Run `sh -c 'echo "AGENTIVE_HEADLESS=${AGENTIVE_HEADLESS:-} CI=${CI:-} GITHUB_ACTIONS=${GITHUB_ACTIONS:-}"'`.
   If AGENTIVE_HEADLESS is 1, or CI or GITHUB_ACTIONS is set, this is a run with no human present: add
   `--headless` to every engine command and ask nothing. A headless run writes
   the same files as an interactive one.
2. Read the flags from the arguments: ARGUMENTS = `$ARGUMENTS`. Pass `--managed-review-copy` or
   `--no-managed-review-copy` through unchanged. If both are given, the engine stops; report that and end.
   `--no-hooks` is the opt-out: pass it through when it is in ARGUMENTS or the user said "no hooks" (no hook scripts,
   no `.claude/settings.json` / `.codex/hooks.json` wiring; everything else is written).

## 2. Evidence

Run `ENGINE init --repo . --print-evidence`. It prints `AGENTIVE_EVIDENCE <json>` and writes nothing.
- `ecosystems` empty: a greenfield repo. That is a normal input; every stack verb stays pending. Go on.
- A verb the evidence does not show stays pending: never write a command without evidence, not even an
  ecosystem default for an ecosystem the evidence does not show.
- Only when the evidence is ambiguous (two candidate commands for one verb, for example a `test` and a `test:unit`
  script, or several ecosystems): pick by the tie-break "most used in existing code, else the ecosystem's official
  default" and put that verb in the values file (step 4) with its evidence.
- A value the user stated in this conversation ("our test command is ...") is pinned:
  `"pinned": {"by": "user", "at": "<RFC 3339 now>", "text": "<the user's words>"}` on that verb.

## 3. The only question (REVIEW.md copy)

Skip it when a flag was given, when `.agents/framework.yaml` already has `review.managed_code_review_copy`, or in
a headless run. Otherwise ask exactly this one plain yes/no question and wait for the reply:

> Do you use Claude managed Code Review (Team/Enterprise; generally available)? If yes, init also
> generates REVIEW.md as a copy of code_review.md. (default: No, only code_review.md)

Yes -> `--managed-review-copy`; anything else -> `--no-managed-review-copy`.

## 4. Values and proof seeds

Write `${TMPDIR:-/tmp}/agentive-values.json` (outside the repo) with only what you decided in step 2, plus one
seed per verb that has a command among lint, typecheck and test, so the proof can show the command fails on a
seeded violation:

```json
{"verbs": {"test": {"cmd": "npm run test:unit", "tier": "inner",
                    "evidence": {"rank": 1, "source": "package.json scripts.test:unit", "files": ["package.json"]}}},
 "seeds": {"test": {"path": "tests/agentive_seed_test.py", "content": "def test_seed():\n    assert False\n"},
           "lint": {"path": "src/agentive_seed_lint.py", "content": "import os\n"}}}
```

A seed path must not exist yet and must sit where the command looks (the engine writes it, runs the verb once,
then deletes it). Use the stack's own failing form (a failing test, a lint violation, a type error).

A `lint` verb you decide may carry `"file_args": false` (a whole-project command such as `tsc --noEmit` or `go vet ./...`:
the edit hook skips it and Stop runs it once) and `"exts": [".ts"]`; left out, the engine derives both from the command and the ecosystem (unknown command: `file_args` false).
A lint or typecheck command you give that walks the whole repository must keep the installed agentive layer out
(`.agents`, `.claude`, `.codex`): use the tool's additive exclude option (`ruff check --extend-exclude .agents,.claude,.codex .`,
`flake8 --extend-exclude .agents,.claude,.codex`, `eslint --ignore-pattern .agents/ --ignore-pattern .claude/ --ignore-pattern .codex/ .`)
or name the source and test directories (`biome check src tests`); otherwise verify lints the layer and is red right after install.

## 5. Run the engine

`ENGINE init --repo . --prove --values ${TMPDIR:-/tmp}/agentive-values.json [--managed-review-copy|--no-managed-review-copy] [--no-hooks] [--headless]`

- exit 1 (`AGENTIVE_STOP`): the repo is already initialized. Show the `AGENTIVE_STATUS` lines and tell the user to
  run /agentive:update. Do not reproduce init steps. End.
- exit 2 (`AGENTIVE_ERROR`): show the line and its NEXT. End.
- No confirmation question: hooks, permissions, `.claude/settings.json`, `.codex/`, CI and review config are
  written in the same run. `.claude/settings.json` is written last, after each installed hook ran once with a sample
  input and exited 0; if one fails, settings.json is NOT written (`AGENTIVE_WARN hook_smoke_failed ...`,
  `AGENTIVE_HOOKS ... status=off`): report it with its NEXT.
- An existing `.claude/settings.json` (for example the one `claude plugin install --scope project` just wrote with
  `enabledPlugins`) is merged by key: only hooks, permissions, sandbox, plansDirectory and env are agentive's, every
  other key stays. Invalid JSON (comments): not written, `AGENTIVE_WARN unparsable_json ...` with its NEXT.
- `AGENTIVE_PROOF_FAIL verb=<v> ... log=<path>`: read the log with the NEXT command, correct the verb in the values
  file (or give no command when the evidence does not support one), then run
  `ENGINE reprove --repo . --values ${TMPDIR:-/tmp}/agentive-values.json`. Two failed attempts: report the slot
  as unproved and move on.

## 6. Final report (to the user)

Read `${CLAUDE_PLUGIN_ROOT}/payload/engine/final-report.md` and report in the order it gives, starting with the hooks and guard status. Lines marked (init) apply to this command; skip the lines marked (update).
