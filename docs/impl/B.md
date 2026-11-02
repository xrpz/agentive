# Plugin layer and enforcement, area B (contracts and decisions)

Area B of docs/IMPLEMENTATION.md §7. Units: `src/units/plugin.yaml`. Tests: `tests/engine/`, `tests/hooks/`, `tests/plugin/`
(`python3 tools/selftest.py component_tests` runs them). Sections 1 and 2 are seams other areas rely on; keep them stable and list a
change under "Changes" with its date. Open items are in §5.

## 1. Install-time placeholders the engine renders (seam B -> C, D)

Syntax `%%agentive:<key>%%` (docs/IMPLEMENTATION.md §3). The engine (`src/engine/render.py`) resolves:

Placement rules [설계 판단]:
- A placeholder that is the only non-blank text on its line is a block placeholder: the line is replaced by the
  value's lines, each prefixed with the line's leading whitespace; an empty value removes the whole line.
- A placeholder inside other text is inline: the value must be one line (a multi-line value is a render error).
- In a `.json` template a JSON string that is exactly one placeholder is replaced by the JSON value of the key
  (list, object, string, number); placeholders inside longer JSON strings are inline text.
- An unknown key, or a dotted key that does not resolve (for example `verbs.test.cmd` of a pending verb), stops
  that unit with `AGENTIVE_ERROR template=<path> key=<key>` and exit 2. Use the `render.*` blocks for anything
  that may be pending.

### `render.*` blocks (computed per run from the evidence, the agentive manifest and .agents/framework.yaml)

| key | kind | value |
|---|---|---|
| `render.orientation` | block | 1-3 lines: `Stack (from evidence): <ecosystems> (<evidence files>).` + `Tests: <paths.tests>` (only when evidence found a test location; NOMAP: no source-directory list) ; greenfield: the pending notice `Stack: pending (no evidence): no stack evidence yet. Scaffold the project with your own agent, then run /agentive:update (Codex: $agentive-update).` plus one `TODO: <stack> named in <file>; no command until evidence exists.` line per stated stack |
| `render.doc_pointers` | block | one line `Docs (pointers to files that exist): <list>. Read the one that matches your task before guessing.` over README.md, ARCHITECTURE.md, CONTRIBUTING.md, docs/architecture.md and docs/adr/ (docs/adr/ also counts when the payload installs it); empty (line removed) when none exists (decisions.accepted[NOMAP]) |
| `render.verbs` | block | one markdown list line per verb in setup, build, lint, typecheck, test, verify, run order: ``- `<verb>`: `.agents/bin/verb <verb>` runs `<cmd>` (evidence: <source>)`` ; pending: ``- `<verb>`: pending (no evidence)`` (+ ` TODO: <stack> (<file>)` when stated) |
| `render.verb_list` | inline | space-separated verbs that have a command (`lint test verify`), empty when none |
| `render.pending_verbs` | inline | space-separated pending verbs, or `none` |
| `render.path_rules` | block | from `agentive.rulesync.plan` (RULESNEUTRAL, the renderer `agentive-lint rules --write|--check` also uses): every `.agents/rules/<name>.md` (bundled packs as they will be on disk, template + `.agents/custom/` override, and the project's own rule files): rules without `paths:` under `### All files` (`#### <name>`), each path rule under `### <name> (paths: <globs>)`, body headings moved below the rule heading, while AGENTS.md outside the managed regions + 40 lines + the rules stay <= 200 lines and the bytes + 8 KiB <= 32 KiB; otherwise pointer lines (`### All files` / `### By path`, `- <name>: read .agents/rules/<name>.md ...`) and a `.claude/rules/<name>.md` copy per rule (T1); no rule -> `No rules yet.` (spec.repo_layer.files[f-rules]) |
| `render.stack` | inline | primary ecosystem id (`node`, `python`, `go`, `java`, `rust`, ...) or `none` |
| `render.ecosystems` | inline | comma-separated detected ecosystems or `none` |
| `render.default_branch` | inline | default branch read from `origin/HEAD`, else `main` |
| `render.framework_version` | inline | installed plugin version (payload index.json `version`) |
| `render.read_limit_bytes` | inline | context gate limit (manifest `context.read_limit_bytes`, default 65536) |
| `render.code_review_copy` | block | the exact bytes of `code_review.md` as written by this run (C's REVIEW.md template is only this placeholder); units that use it are rendered after the shared files are written |

CI tool blocks (CITOOL, decisions.accepted, user 2026-10-29; used by D's two workflows): the effective CI tool is
`review.pipeline.ci_tool` of `.agents/framework.yaml`, else the agentive manifest `framework.init_tool`, else `claude`
(`agentive.review.config.ci_tool`, the same function the review scripts use at run time).

| key | kind | value |
|---|---|---|
| `render.ci_tool` | inline | `claude` or `codex` |
| `render.ci_llm_step` | block | the ai-review LLM step of `agentive-review.yml` for that tool: the YAML list item of `engine/ci/llm-step-<tool>.yml` without its header comments, indented to the placeholder line |
| `render.ci_cli_name` | inline | `Claude Code` or `Codex` (step name of the reviewer-eval install) |
| `render.ci_cli_package` | inline | npm package of the CLI: `@anthropic-ai/claude-code` or `@openai/codex` |
| `render.ci_setup_steps` | block | CISETUP: `actions/setup-*` steps of the `outer-verbs` job per evidence ecosystem (`src/engine/cisetup.py`, `toolchain-<name>` step ids; a commented placeholder with no evidence) |
| `render.ci_id_token` | inline | CISPLIT: `id-token: write` for the claude path, empty for codex (the line is removed) |
| `render.ci_secret` | inline | repository secret: `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` |
| `render.rules_managed` | block | SECPATHS: the `- {glob, reason, source}` lines the engine owns inside `trust_boundary.never_ai_approve` of the `code_review.md#rules` block: the CODEOWNERS snapshot (`source: "codeowners:<line>"`) and the path entries of the stack presets (`preset:nextjs`); empty = the line is removed (`src/engine/secscan.py`) |
| `render.rules_content` | block | SECPATHS: `content_rules:` and its `- {path, contains, reason, source}` lines for the presets `spring` and `django`; empty when none |

B-only blocks (used by B's own templates; other areas may use them too): `render.permissions_allow`,
`render.permissions_deny`, `render.permissions_ask` (JSON lists for .claude/settings.json),
`render.filechanged_matcher` (FileChanged matcher string), `render.codex_rules` (execpolicy rules text),
`render.codex_rules_check` (execpolicy fixture lines).

### Dotted keys

`<a.b.c>` resolves first in the agentive manifest, then in `.agents/framework.yaml` (project config). Example:
`%%agentive:stop_conditions.max_blocks%%`. Values must be scalars (inline) or strings (block).

### `when` conditions on units (seam B -> C, D)

`when` is evaluated per run; the entry is installed only when it is true [설계 판단 grammar]:
- `<dotted key>`: truthy value in `.agents/framework.yaml` (then the agentive manifest); absent = false
  (`review.managed_code_review_copy`).
- `stack:<eco>`: ecosystem `<eco>` is among the detected ecosystems (`stack:node`, `stack:python`, `stack:go`,
  `stack:java`, `stack:rust`).
- `evidence`: at least one ecosystem was detected; `verb:<v>`: verb `<v>` has a command.
- `not <cond>`; `<cond> and <cond>`; `<cond> or <cond>` (no parentheses, `and` binds tighter).
A whole file whose `when` turns false is removed on update when it is unmodified (hash equals the baseline);
a modified one is kept and its baseline record is dropped (spec.init_apply.gaps.optional_outputs.update).

## 2. Engine CLI (seam B -> D, stable)

```
python3 <payload>/engine/apply.py {init|update|status|reprove} --repo <dir>
    [--managed-review-copy | --no-managed-review-copy] [--force] [--headless] [--no-hooks]
    [--values <json file>]      (--confirm-all and --confirm <path>... are still parsed and ignored: INITWRITE)
    additions (B): [--accept <id>...] [--tool claude|codex] [--prove] [--today YYYY-MM-DD] [--print-evidence]
```

- Deterministic, no LLM, no network. Never commits. Reads only the payload, the repo and `--values`.
- Exit 0 = done; the output ends with `AGENTIVE_CHANGED:` followed by one changed path per line (repo-relative,
  sorted; empty list = nothing changed). Exit 1 = entry check stop (`init` on a repo with `.agents/manifest.yaml`,
  `update`/`status`/`reprove` without it), last line `AGENTIVE_STOP ...; NEXT=<command>`. Exit 2 = error, last
  line `AGENTIVE_ERROR ...; NEXT=...`.
- Other agent-facing lines before `AGENTIVE_CHANGED:`: `AGENTIVE_PENDING verb=<v>`, `AGENTIVE_UNPROVED verb=<v>`,
  `AGENTIVE_DRIFT id=<id> reason=<r>`, `AGENTIVE_WARN <text>`, `AGENTIVE_REPORT <text>` (the report lines of
  spec.init_apply.report) and, since INITWRITE/JSONMERGE (2026-10-29):
  `AGENTIVE_HOOKS tool=<claude|codex> file=<path> status=<installed|installed_modified|off> [reason=<r>; NEXT=<n>]`,
  `AGENTIVE_HOOK tool=<t> event=<e> matcher=<m|-> runs=<command>` (one per agentive handler: what now runs
  automatically), `AGENTIVE_GUARD status=<on|off>[; NEXT=...]`, `AGENTIVE_REVERT git checkout -- <paths>` /
  `AGENTIVE_REVERT git clean -fd -- <paths>`, `AGENTIVE_SKIPPED path=<p> reason=--no-hooks`,
  `AGENTIVE_CONFLICT path=<p> key=<k> kept=<json> agentive=<json>; NEXT=...`,
  `AGENTIVE_NOT_EFFECTIVE path=<p> reason=<untrusted_folder|codex_hooks_untrusted|write_failed> NEXT=<action>`.
- INITWRITE: the engine never asks. Every file, the U2 confirm_scope files included, is written in the same run, in
  interactive and in headless runs alike. `--no-hooks` skips the units whose confirm_scope has `hooks` (hook scripts,
  `.claude/settings.json`, `.codex/hooks.json`); earlier manifest records of skipped files are kept.
  `.claude/settings.json` and `.codex/hooks.json` are written LAST, key-merged (src/engine/jsonmerge.py), after
  `smoke_check`: every agentive hook handler runs once with a sample input in a scratch copy of `.agents/{hooks,lib,
  bin}` whose manifest has all verbs pending; a non-zero exit blocks that one file (`hook_smoke_failed`).
  `.agents/state/pending-confirmation.json` is recomputed by every init and update run (items: written but not yet
  effective) and removed when empty. A `.codex/` write that fails with OSError is recorded (`write_failed`) and does
  not stop the run.
- Deterministic tests (D): `init --repo <fixture> --no-managed-review-copy` writes the full layer in
  one call; a second `update --repo <fixture>` must print an empty `AGENTIVE_CHANGED:` list. Files
  under `.agents/state/` are local state (drift-report, pending-confirmation, logs) and are excluded from the
  "nothing changed" comparison; the engine rewrites drift-report.json only when its content (minus
  `generated_at`) changes.
- `--values <json>`: `{"verbs": {"<verb>": {"cmd": "...", "tier": "inner|outer", "evidence": {...}, "pinned":
  {...}, "proof": {...}, "fail_count": "..."} | {"status": "pending", "reason": "no_evidence", "todo": {...}}},
  "paths": {"src": [...], ...}, "tools": {"claude_code": {"version": "x.y.z"}}, "seeds": {"<verb>": {"path":
  "<file>", "content": "<text>"}}}`. Values replace derived slots one key at a time; pinned verbs in the existing
  manifest are never re-derived.
- `--prove`: run slot proofs (pass on the current tree; seeded fail when `seeds.<verb>` is given) with the
  commands in the repo; without it verbs keep their recorded proof or get `proof.pass_run_id: unproved`
  (`AGENTIVE_UNPROVED`). `reprove` always proves (dirty slots from `.agents/state/dirty.json`, else unproved ones).
- `--today`: accepted and unused since REGIONNOSHA (E2E-09, 2026-10-30): no tracked file holds a date.
- `--print-evidence`: prints `AGENTIVE_EVIDENCE <json>` (evidence.scan) and an empty changed list; writes nothing
  (the skills read it before deciding ambiguous slots).
- `--accept <id>`: overwrite these drift items (customized or no_baseline) with the rendered content.

- `--tool claude|codex` (default claude) also records the tool that ran init: `init` writes it to the agentive manifest
  as `framework.init_tool`; `update` keeps the recorded value and never takes it from its own `--tool` (a manifest
  without the key counts as claude and gets `init_tool: claude` written at the next update). The Claude Code skills
  pass `--tool claude`, the Codex skills `--tool codex`. The report adds one `AGENTIVE_REPORT github: the CI AI review
  ... runs on <tool> (<review.pipeline.ci_tool | the tool that ran init>); add the repository secret <NAME> ...` line.
  An invalid `review.pipeline.ci_tool` prints `AGENTIVE_WARN` and falls back to the init tool.

## 3. What B ships (install path <- source, unit)

| install path / output | source | unit |
|---|---|---|
| engine `payload/engine/{apply,evidence,render,regions,prove}.py` and `payload/engine/final-report.md` (the final report order that the four init/update skills point to, one source: items marked (init) or (update) apply to that command) | `src/engine/` | eng.apply |
| `.agents/bin/verb` (sh -> `agentive.verb`) | `src/repo-layer/dot-agents/bin/verb` | rl.bin.verb |
| `.agents/hooks/{session-start-notice,pre-tool-use-guard,pre-tool-use-context-gate,pre-tool-use-test-rewrite,post-tool-use-lint,stop-verify,file-changed-dirty,session-end-harvest}.py` (confirm_scope hooks) | `src/repo-layer/dot-agents/hooks/` | rl.hooks |
| `.agents/guard.yaml` (schema guard-rules, confirm_scope permissions) | `src/repo-layer/dot-agents/guard.yaml` | rl.guard |
| `.agents/manifest.yaml` seed (defaults only; never installed as a generated file) | `src/repo-layer/dot-agents/manifest.yaml` | rl.manifest |
| `.claude/settings.json` (hooks, permissions deny/ask incl. DENYEDIT, sandbox, env, plansDirectory) | `src/repo-layer/dot-claude/settings.json` | rl.claude.settings |
| `.codex/hooks.json`, `.codex/config.toml`, `.codex/rules/agentive.rules`, `.codex/rules/agentive-execpolicy-check.txt` | `src/repo-layer/dot-codex/` | rl.codex.* |
| engine-made: `.claude/skills/<name>`, `.claude/rules/<name>.md`, `.claude/agents/<id>.md` (T1 symlink or copy), `.codex/agents/<id>.toml`, `.agents/skills.lock`, `.agents/framework.yaml` (init flag only) | engine | eng.apply |
| Claude plugin: `skills/{init,update,reprove}/SKILL.md`, `hooks/hooks.json` + `hooks/session-start-uninitialized.py` | `src/plugin/claude-code/` | pc.* |
| Codex plugin: `skills/agentive-{init,update}/SKILL.md` + `agents/openai.yaml` (payload copied in by the generator) | `src/plugin/codex/` | px.* |

Run-time library modules of area B (inside the foundation unit `lib.agentive`): `nohuman` (the one no-human rule),
`hookio`, `guard` (normalize/match/render), `gates` (context gate, test rewrite), `handoff` (+ CLI
`PYTHONPATH=.agents/lib python3 -m agentive.handoff write --reason ... --failure-reason ... --next-step ...`),
`state` (stop/dirty/verify/harvest files), `verb` (verb wrapper).

Agent-facing lines (exact): `AGENTIVE_FAIL verb=<v> exit=<n> log=<path>; NEXT=...` (verb), `PENDING verb=<v> reason=no_evidence`,
`BLOCKED <id>: <reason>. NEXT: <next>` (guard, context gate), `AGENTIVE_STATE=...` (SessionStart), Stop
`DONE` / `PENDING: ...` / `CAP: ...` systemMessage, `NO-PROGRESS: ...` continuation, `DIRTY=<slots> ...; NEXT=/agentive:reprove`.

## 4. Design judgments [설계 판단] (also in the file headers)

1. Proofs: the engine runs them only with `--prove` (the skills always pass it) so D's deterministic tests need no
   toolchain; without it a verb keeps its recorded proof or gets `proof.pass_run_id: unproved` (schema requires
   a proof object). Seeds for the seeded-fail proof come from the skill (`--values seeds`); `run` is never proved.
2. [Replaced by INITWRITE 2026-10-29] The earlier two-call confirm flow is gone. Choices made for INITWRITE and
   JSONMERGE: (a) hook ownership marker = a script under `.agents/hooks/` in the handler's command or args, no extra
   field; (b) drift on a key-merged JSON file covers the agentive hook handlers only (permissions, sandbox, env and
   plansDirectory are merged idempotently on every run and need no baseline); (c) a permission entry that agentive
   stops rendering stays (it cannot be told from a user entry); (d) `.codex/config.toml` is not merged (no TOML
   writer in the standard library): an existing file stays a D10 conflict with an explicit `AGENTIVE_WARN`;
   (e) `.codex/hooks.json` uses the same merge with `hooks` as its only owned key; (f) the smoke check uses the
   engine's own interpreter, a scratch copy of the hook layer and an all-pending manifest, so it never runs the
   project's verbs; (g) the untrusted-folder item is recorded for every headless run that changed
   `.claude/settings.json`, because whether deny and ask rules apply before trust is 미검증; (h) the revert line
   compresses new files to a top-level directory only when every untracked file in it was created by the run;
   (i) `--confirm-all` and `--confirm` stay parsed and ignored so older skills do not break.
3. Evidence: root-level package files only; primary ecosystem = most source files (D6); Makefile targets are
   rank 2 and replace rank-4 defaults; CI workflow parsing is not done in v1; node_modules and .venv are not
   vendor paths (no Read/Edit deny); tiers: lint/typecheck/test/verify inner, setup/build/run outer.
4. guard.yaml value forms: argv_prefix = list of Claude-syntax patterns (`*` = any characters); extra ids where one
   match kind cannot hold a design id: `publish-dry-run` (unless -> hook only), `infra-destroy-sql` (special);
   specials `default_branch_push`, `tag_push`, `unresolvable_rm_var`, `sql_without_where`, `sql_destructive`,
   `no_verify` (token-aware so `-m "fix -n"` is not a hit); SQL checks apply only when a SQL client is in the
   command. Default branch unknown (no origin/HEAD) -> main and master.
5. A region is written only when its body changes (REGIONNOSHA, E2E-09: the notice line is constant, no date or
   sha; an older dated line is replaced once; an update after an unrelated commit writes nothing); a text override applies to the first region of the file (AGENTS.md#context, CLAUDE.md#imports,
   code_review.md#rules, .gitignore#state); a fenced `yaml <info>` block in it merges into the same block.
6. Symlink baselines hash `symlink:<target>`; T1 = copy when `core.symlinks=false` or Windows, else symlink
   (recorded as probe result `skipped` with `measured.mode`); copies mirror the source as it is on disk.
7. D10 proposals go to `.agents/state/conflicts/<path with / as __>.diff`; manifest.conflicts lists them.
8. Drift accept: `--accept <id>` (natural-language acceptance applied by the skill); a deleted whole generated
   file with a baseline is a drift item (removed difference), recreated only by --force or --accept.
9. skills.lock hashes every `.agents/skills/<name>/` with a SKILL.md as it is on disk after the run (also the
   Codex bootstrap copies); `verified_at` changes only with the hash.
10. Stop hook: dirty slots are reported (`DIRTY=...; NEXT=/agentive:reprove`), not re-derived (the engine lives in
    the plugin); Codex Stop exit 0 prints nothing (plain stdout is invalid there; Codex systemMessage 미검증).
11. Test rewrite only for the manifest test command (+ extra args, passed as `verb test -- <args>`) or an exact
    known runner invocation; `verb <v> -- <args>` appends shell-quoted args.
12. Hook commands: Claude exec form `python3` + `${CLAUDE_PROJECT_DIR}/.agents/hooks/<x>.py` (the scripts find the
    repo root from the input cwd); Codex `python3 "$(git rev-parse --show-toplevel)/.agents/hooks/<x>.py" --tool codex`
    (`commandWindows` relative form 미검증). Context gate has no onFailure (only the guard blocks on failure).
13. verify.json head_sha is 40 zeros when the repo has no commit (schema needs a sha); SessionEnd harvest
    detects repetition within the session only (cross-session repetition is left to /distill over the queue).
14. Codex SKILL.md also writes `disable-model-invocation: true` (docs/IMPLEMENTATION.md §5.6 "every skill");
    if the T3 probe shows Codex rejects the key, drop it there (UV-3).
15. CITOOL (2026-10-29): the ai-review LLM step differs structurally between claude-code-action and openai/codex-action,
    so `agentive-review.yml` holds one block placeholder (`render.ci_llm_step`) and the two step sources live in the
    engine directory `src/engine/ci/` (not installed as files). Consequence: that one template is not valid YAML
    before rendering; tests/ci render it for both tools (the engine's own functions) before parsing. The claude
    rendering is byte-identical to the pre-CITOOL step. `reviewer-eval.yml` uses inline placeholders only and stays
    valid YAML. Manifest key `framework.init_tool` (not `tools.*`: that object is "tool versions only"). The update
    re-render goes through the normal whole-file path, so a changed key is a `ci` confirm_scope write and a user-edited
    workflow is reported as drift, not overwritten.

16. CISETUP (2026-10-29): `render.ci_setup_steps` is built by the new module `src/engine/cisetup.py` (apply.py gets one
    import and two block lines) [설계 판단: kept out of apply.py, which another change edits]. Versions come from the
    repo's version files through the `*-version-file` inputs [설계 판단: generators mostly pin fixed versions]. UV-24:
    the id-based list merge of `render.merge` gains `before: <id>` / `after: <id>` on an override item (placement keys,
    not merged into the item; an unknown id raises InvalidOverride naming the generated ids). The apply report prints
    one extra `AGENTIVE_REPORT github:` line for Dependabot/fork PRs (no Actions secrets, the LLM stage cannot run,
    RV8 asks a human), bot PRs and human PRs without a block (BOTPR). `.github/pull_request_template.md` is installed
    from `src/repo-layer/dot-github/` (unit `rl.github.pr-template`, confirm_scope ci). Runner images: ubuntu-latest
    becomes Ubuntu 26.04 in 2026-11; uv, pnpm and bun are not preinstalled.
17. INDEPCI hook (2026-10-29): `.agents/hooks/subagent-observe.py` (SubagentStart, SubagentStop; command python3, timeout
    5, no onFailure) is a new repo-layer hook in `dot-claude/settings.json`; no Codex counterpart [설계 판단: the Codex
    event input is 미검증]. It appends to `.agents/state/subagents.jsonl` (gitignored state) and trims it to 200 lines
    past 128 KB. Hooks and `settings.json` are U3 confirm paths.

### V1-13 / V1-16 hook choices (2026-10-29)

- Revised 2026-10-30 (VERIFYTREE, E2E-08): the key is the work content tree (`hookio.content_tree`: tracked files as on
  disk + untracked files that are not ignored, `.agents/state/**` excluded; no HEAD) plus the verb commands
  (`verb.fresh_for`). The verb wrapper stores it in `verify.json` `content_tree` before the verbs run; it is absent outside
  git, then Stop always reruns. A commit alone no longer reruns verify. (The former sha256 fingerprint over HEAD + diffs is gone.)
- [설계 판단] Stop with an unchanged red tree and `consecutive_blocks > 0` prints the `AGENTIVE_FAIL` lines kept in
  `stop.json` `last_fail_lines` (at most 4) instead of rerunning; with no kept lines it rebuilds one per failed verb from `verify.json`.
- [설계 판단] `RED-UNCHANGED` is a `systemMessage` (the human line) and `stop.json` `last_reason`. A Stop hook that exits 0
  cannot hand context to the agent without continuing the turn, so no `additionalContext` is sent; Claude Code behavior of
  `additionalContext` on a Stop exit 0 is 미검증. Codex gets no stdout (plain text is invalid for Stop).
- [설계 판단] PostToolUse lint scope: `verbs.lint.exts` (manifest, optional) else the ecosystem named by the manifest's evidence
  files (`agentive.lintscope`, small built-in map, docs and data extensions never derived). `verbs.lint.file_args: false`
  (default true) = the command cannot take file arguments: skipped in PostToolUse, run once at Stop. The Claude Code
  settings template keeps matcher `Edit|Write` (extensions are per project, so no static `if:` filter is generated).
  /agentive:init and /agentive:update derive both fields (src/engine/evidence.py `lint_scope`, `file_args_for`; apply.py
  `lint_scope_into`): `exts` = the source extensions of the primary ecosystem, `file_args` true only for a tool known to take
  file paths (eslint, ruff check/format, prettier, black, isort, flake8, pylint, gofmt, biome, stylelint, rubocop; mypy/pyright
  only with a path already in the command), false for whole-project commands (tsc --noEmit, go vet ./..., golangci-lint run,
  mvn/gradle tasks, cargo clippy, make targets) and for anything unknown (safe side: Stop runs it once). A package.json
  `scripts.lint` is judged by its body. A value already in the manifest for the same command, or given through `--values`, is kept.
  [설계 판단] golangci-lint is false although it accepts file paths: it analyses packages, and a file list that spans packages
  is 미검증, so the whole verb runs at Stop. Tests: tests/engine EvidenceTest (one case per stack) and DefaultsTest.
- Bug fix E2E-01 / E2E-04 (2026-10-29, docs/impl/decisions-log.md §15): derived repo-wide lint commands keep `.agents`, `.claude`,
  `.codex` out (`evidence.owned_exclude`: ruff/flake8 `--extend-exclude`, eslint `--ignore-pattern`; biome scoped to the
  manifest src/tests paths); `verb lint -- <file>` replaces a trailing whole-repo target with the files
  (`lintscope.file_command`) instead of appending; `lintscope.select_files` skips the three directories; a package.json
  `scripts.lint` body naming `.` is `file_args` false.
- 미검증: Codex `apply_patch` header lines (`*** Add File: `, `*** Update File: `, `*** Delete File: `, `*** Move to: `) are
  not checked against an official page in this change; a patch that yields no path is skipped and Stop covers it.
- [설계 판단] SessionStart `HARVEST=<n>` line at `>= 2` queue entries (`HARVEST_MIN`), startup and resume only; no human present:
  `HARVEST=<n>; NEXT=none`. The one-line rule for the agent sits in the `AGENTS.md#context` region.

### Bugfix batch (2026-10-29, implementation verification V1-02 ... V2-21)

- V1-05 [설계 판단]: `.agents/rules/<name>.md` files that no payload entry installs and no manifest record names are the
  project's rule files (`Engine.find_user_rules`): read, never written; merged into `AGENTS.md ## Path rules` with the bundled
  packs (sorted by name) and mirrored to `.claude/rules/<name>.md` (T1). Deleting the source removes the unmodified mirror.
- V1-06 / V1-02 / V1-07 SessionStart lines for the agent (startup and resume) end with a NEXT [설계 판단 for the words]:
  `HANDOFF=<path> [stale]; NEXT=ask_user:resume|discard (move to .agents/state/handoff/done/)|later; DO_NOT=start_other_work_before_the_answer`,
  `PENDING_CONFIRMATION=<n> ...; NEXT=tell_user:<the first item's next>`, and the new `DIRTY=<slots> (stack evidence changed);
  NEXT=ask_user_to_run:/agentive:reprove` (Codex `$agentive-update`); no human present: `NEXT=none`. Compact and clear keep the
  bare HANDOFF line. Reason: `systemMessage` is shown to the user only (code.claude.com/docs/en/hooks, JSON output:
  "Warning message shown to the user"); SessionStart `additionalContext` and plain stdout reach Claude (same page). The
  `AGENTS.md#context` region gets one line: follow the `NEXT=` of each SessionStart line.
- V1-08: hooks, `.agents/bin/agentive-*`, the engine and the plugin hook set `sys.dont_write_bytecode = True` before importing
  `agentive`; `.agents/bin/verb` and the skill shims run `python3 -B`. Commands the verbs start are not affected (the flag is
  not inherited). The design `.gitignore#state` list stays as it is.
- V1-09: the design's push-delete form `git push <remote> :ref` cannot be a permission rule (a trailing `:*` is the legacy
  prefix syntax, code.claude.com/docs/en/permissions), so it is the hook-only rule `push-delete-refspec` (special
  `push_delete_refspec`, `+:ref` too), `decision: deny`, `expressible: false`: deny plus the init warning, as
  `guard.decisions.not_expressible` says. `git push <remote> --delete <ref>` still asks.
- V1-17: a missing or unparsable `.agents/guard.yaml` makes the guard deny every Bash command with `BLOCKED guard-config: ...
  NEXT: run git checkout -- .agents/guard.yaml ...`; that exact restore command (and `git restore`) is let through.
- V1-14: the stated-stack line also counts with a build or implementation verb (build, implement, develop, write, use,
  powered by, based on) next to the stack name. Boundary line and service facts stay empty in v1 (no input; user decision).
- V1-19 [설계 판단]: a generated path that is gitignored is recorded with `proof: gitignored` even when it exists, so a build
  proof creating `dist/` does not change the manifest at the next update. A region is no longer rewritten after a new commit
  (REGIONNOSHA, E2E-09).
- V2-11: `.codex/agents/<id>.toml` developer_instructions start with a read-only line for a role whose Claude Code `tools`
  have no Edit/Write (all seven roles), plus a read-commands-only line when `tools` has no Bash. A Codex role file has no tool
  list or sandbox key (openai/codex official GitHub, codex-rs/core/src/agent/role.rs at 23934d31).
- V2-21: `status` prints `AGENTIVE_STATUS not-effective <path> reason=<r>; NEXT=<next>` per pending-confirmation item and
  `not_effective=<n>` in the summary.

### Final triage decisions (2026-10-29: SKILLPATHS, HEADLESSBINS, NOBOUNDARYLINE, RULESNEUTRAL)

1. `render.boundary_line` and `render.service_facts` are gone (NOBOUNDARYLINE): the AGENTS.md#context template no longer
   names them, and a template that still did would fail with an unknown-key RenderError.
2. `.claude/rules/<name>.md` mirrors are produced only in pointer mode (`self.rules_plan["mirrors"]`); a mirror recorded by an
   earlier version for a rule that is now merged is removed by `remove_unproduced` when unchanged [설계 판단].
3. `agentive-lint rules --write` edits only AGENTS.md#path-rules (keeping the provenance line) and the `.claude/rules` copies;
   it never writes the agentive manifest. The next /agentive:update therefore rewrites only the manifest record of the region
   hash (and drops the record of a removed copy); AGENTS.md is not rewritten and nothing drifts [설계 판단].
4. The guard rule `agentive-path-traversal` (deny, argv_prefix with a mid-text `*`) renders into `permissions.deny`; Codex gets
   it only through the PreToolUse hook because `codex_rules` skips patterns with `*` (unchanged rule of guard.py).

### End-to-end decisions batch A (2026-10-30)

1. E2E-12: `ci_llm_step_text` and `ci_secret_blocks` write every kept credential value from `reviewcfg.ci_input_expr` (OAuth token
   first with ci_auth both); the source `src/engine/ci/llm-step-claude.yml` shows the default expression.
2. E2E-13: `migrate_rubric` (update only) reads the preset of `.agents/rubric.yaml` before and after the run; when it moved, an
   override whose `extends` names the old preset gets that one line rewritten (quotes and a trailing comment kept, nothing else
   touched; an edit that cannot be done safely is an AGENTIVE_WARN and leaves the override pinned) and `AGENTIVE_REPORT rubric:`
   lines say what changed and how to stay on the old preset [설계 판단]. `.agents/rubric.yaml` is excluded from the install-time
   `.agents/custom/` overlay, like `.agents/guard.yaml`: the rubric override is applied at run time only (bug fix).
3. E2E-11: one report line `AGENTIVE_REPORT github: install PR:` on the claude CI path.

## 5. Notes for the other areas and open items

- Content area (the needs listed in docs/impl/C.md §4): (1) `render.code_review_copy` is implemented (byte copy; REVIEW.md rendered
  after code_review.md is written). (2) `rule_packs.boundary`: no run sets it in v1. project-config has no such key
  (additionalProperties false) and the manifest has none either, so the pack stays off until a design change adds
  the key; `render.boundary_line` stays empty. (3) handoff CLI added (`python3 -m agentive.handoff write ...`).
  The review-rules hooks globs include `.agents/hooks/**` (checked).
- D: run the engine with `CI`, `GITHUB_ACTIONS` and `AGENTIVE_HEADLESS` removed from the subprocess environment;
  CI sets them, and a headless engine run now writes the same files as an interactive one (INITWRITE; `--confirm-all` is ignored). Pass
  `--values <file>` with `{"tools": {}}` to skip the `claude --version` / `codex --version` probe (faster, same
  manifest on every machine). `.agents/state/` is excluded from "re-run changes nothing" comparisons.
- D: verb tiers for the CI outer-verbs job: inner = lint, typecheck, test, verify; outer = setup, build, run.
- Open: execpolicy check against a real Codex (UV-31) and the T1 probe are not run (미검증); FileChanged input field name read
  as `file_path` (fallback `path`) 미검증 (the hooks page section was not reached). Checked 2026-10-29: Codex parses a hook
  `systemMessage` and shows it as a warning, and `commandWindows` is a handler field (openai/codex official GitHub source at
  23934d31; its relative-path form is still 미검증); Codex ignores SKILL.md frontmatter keys other than name, description and
  metadata.short-description (codex-rs/skills/src/parser.rs: T3/UV-3 answered from source, no probe run); the Claude Code
  `args` exec form applies to settings.json hooks (code.claude.com/docs/en/hooks, command hook fields).

## Changes

- 2026-10-28: sections 1 and 2 published; `render.code_review_copy` added (C request); CLI additions `--accept`,
  `--tool`, `--prove`, `--today`, `--print-evidence`.
- 2026-10-29: `render.ci_setup_steps`, `render.ci_id_token`, `before:`/`after:` in the id list merge (CISETUP, CISPLIT); see section 4 items 16-17.
- 2026-10-29: SECPATHS: `render.rules_managed`, `render.rules_content`, `src/engine/secscan.py` (CODEOWNERS snapshot, stack presets, security tools -> manifest `security_checks`, `AGENTIVE_REPORT security paths: ...` lines); see docs/impl/decisions-log.md §12.
- 2026-10-29: V1-13 Stop fingerprint skip, per-file PostToolUse lint, V1-16 HARVEST notice (see "V1-13 / V1-16 hook choices").
- 2026-10-29: E2E bug fixes E2E-01/02/04/05: installed layer excluded from derived lint commands, per-file lint replaces the
  whole-repo target, has/have agreement (`common.no_command`), guard reason keeps a literal `..` (docs/impl/decisions-log.md §15).
- 2026-10-29: bugfix batch (section 4 "Bugfix batch"): user rule files, SessionStart NEXT lines and DIRTY, no bytecode, push-delete-refspec, guard-config block, gitignored label, Codex role limit line, status not-effective lines.
- 2026-11-01: HEADLESSHINT and E2E-33/36 (docs/impl/decisions-log.md §23): `agentive/headless.py` + the context-gate hint (never a
  permissionDecision), verb scratch-cache fallback (`verb.cache_env`, `verb.blocked_write_next`), `gates.headless_push_gate` in the guard
  hook, generated/vendor evidence from the tracked state (`evidence._git_ignored`, `_tracked_dir`).
