# B: plugin layer + enforcement (status, contracts, decisions)

Owner B (docs/IMPLEMENTATION.md §7). Status: DONE for v1 (2026-10-28); open items in §6.
Units: `src/units/plugin.yaml`. Tests: `tests/engine/` (32), `tests/hooks/` (17), `tests/plugin/` (7). Sections 1 and 2 are seams other owners rely on;
they are kept stable, and a change is listed under "Changes" with its date.

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
| `render.orientation` | block | 1-3 lines: `Stack (from evidence): <ecosystems> (<evidence files>).` + `Source: <paths.src> · tests: <paths.tests>` ; greenfield: the pending notice `Stack: 근거 없음 · 대기 (no stack evidence yet). Scaffold the project with your own agent, then run /agentive:update (Codex: $agentive-update).` plus one `TODO: <stack> named in <file>; no command until evidence exists.` line per stated stack |
| `render.verbs` | block | one markdown list line per verb in setup, build, lint, typecheck, test, verify, run order: ``- `<verb>`: `.agents/bin/verb <verb>` runs `<cmd>` (evidence: <source>)`` ; pending: ``- `<verb>`: pending (근거 없음 · 대기)`` (+ ` TODO: <stack> (<file>)` when stated) |
| `render.verb_list` | inline | space-separated verbs that have a command (`lint test verify`), empty when none |
| `render.pending_verbs` | inline | space-separated pending verbs, or `none` |
| `render.path_rules` | block | merged rule packs: for every installed `.agents/rules/<name>.md`, its body (frontmatter removed) under `### <name> (paths: <globs>)` while AGENTS.md stays <= 200 lines and 32 KiB; otherwise one line per rule `- <name>: read .agents/rules/<name>.md (paths: <globs>)` (spec.repo_layer.files[f-rules]); no rule pack -> `No path rules for this stack yet.` |
| `render.boundary_line` | block | `Module boundaries: <config>; check: <cmd>` when the boundary rule pack is on (spec.one_way.boundary_lint), else empty (line removed). v1: always empty (no boundary evidence reader yet) |
| `render.service_facts` | block | 0-3 lines from `.agents/framework.yaml` key `service_facts` when the user wrote it there, else empty [설계 판단: v1 always empty; project-config schema has no such key, so nothing is read] |
| `render.stack` | inline | primary ecosystem id (`node`, `python`, `go`, `java`, `rust`, ...) or `none` |
| `render.ecosystems` | inline | comma-separated detected ecosystems or `none` |
| `render.default_branch` | inline | default branch read from `origin/HEAD`, else `main` |
| `render.framework_version` | inline | installed plugin version (payload index.json `version`) |
| `render.read_limit_bytes` | inline | context gate limit (manifest `context.read_limit_bytes`, default 65536) |
| `render.code_review_copy` | block | the exact bytes of `code_review.md` as written by this run (C's REVIEW.md template is only this placeholder); units that use it are rendered after the shared files are written |

B-only blocks (used by B's own templates; other owners may use them too): `render.permissions_allow`,
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
    [--managed-review-copy | --no-managed-review-copy] [--force] [--headless]
    [--confirm-all | --confirm <path>...] [--values <json file>]
    additions (B): [--accept <id>...] [--tool claude|codex] [--prove] [--today YYYY-MM-DD] [--print-evidence]
```

- Deterministic, no LLM, no network. Never commits. Reads only the payload, the repo and `--values`.
- Exit 0 = done; the output ends with `AGENTIVE_CHANGED:` followed by one changed path per line (repo-relative,
  sorted; empty list = nothing changed). Exit 1 = entry check stop (`init` on a repo with `.agents/manifest.yaml`,
  `update`/`status`/`reprove` without it), last line `AGENTIVE_STOP ...; NEXT=<command>`. Exit 2 = error, last
  line `AGENTIVE_ERROR ...; NEXT=...`.
- Other agent-facing lines before `AGENTIVE_CHANGED:`: `AGENTIVE_CONFIRM category=<c> path=<p> sha256=<h>`
  (confirm_scope file not written: no confirmation given, or headless), `AGENTIVE_PENDING verb=<v>`,
  `AGENTIVE_UNPROVED verb=<v>`, `AGENTIVE_DRIFT id=<id> reason=<r>`, `AGENTIVE_WARN <text>`, `AGENTIVE_REPORT <text>`
  (the report lines of spec.init_apply.report).
- U2 confirm_scope: without `--confirm-all`/`--confirm <path>` the engine writes everything else, skips the
  confirm_scope files, records them in `.agents/state/pending-confirmation.json` and prints `AGENTIVE_CONFIRM`
  lines. The skill asks the human, then re-runs the same subcommand with `--confirm-all` (or the confirmed
  paths); a re-run of `init` while `pending-confirmation.json` holds `init` entries is a continuation and passes
  the entry check. In a headless run (`--headless`, `AGENTIVE_HEADLESS=1`, `CI`, `GITHUB_ACTIONS`) confirm flags are
  ignored (no human could have confirmed) [설계 판단: two-call flow].
- Deterministic tests (D): `init --repo <fixture> --confirm-all --no-managed-review-copy` writes the full layer in
  one call; a second `update --repo <fixture> --confirm-all` must print an empty `AGENTIVE_CHANGED:` list. Files
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
- `--today`: fixed date for provenance lines (tests); default today (UTC).
- `--print-evidence`: prints `AGENTIVE_EVIDENCE <json>` (evidence.scan) and an empty changed list; writes nothing
  (the skills read it before deciding ambiguous slots).
- `--accept <id>`: overwrite these drift items (customized or no_baseline) with the rendered content.

## 3. What B ships (install path <- source, unit)

| install path / output | source | unit |
|---|---|---|
| engine `payload/engine/{apply,evidence,render,regions,prove}.py` | `src/engine/` | eng.apply |
| `.agents/bin/verb` (sh -> `agentive.verb`) | `src/repo-layer/dot-agents/bin/verb` | rl.bin.verb |
| `.agents/hooks/{session-start-notice,pre-tool-use-guard,pre-tool-use-context-gate,pre-tool-use-test-rewrite,post-tool-use-lint,stop-verify,file-changed-dirty,session-end-harvest}.py` (confirm_scope hooks) | `src/repo-layer/dot-agents/hooks/` | rl.hooks |
| `.agents/guard.yaml` (schema guard-rules, confirm_scope permissions) | `src/repo-layer/dot-agents/guard.yaml` | rl.guard |
| `.agents/manifest.yaml` seed (defaults only; never installed as a generated file) | `src/repo-layer/dot-agents/manifest.yaml` | rl.manifest |
| `.claude/settings.json` (hooks, permissions deny/ask incl. DENYEDIT, sandbox, env, plansDirectory) | `src/repo-layer/dot-claude/settings.json` | rl.claude.settings |
| `.codex/hooks.json`, `.codex/config.toml`, `.codex/rules/agentive.rules`, `.codex/rules/agentive-execpolicy-check.txt` | `src/repo-layer/dot-codex/` | rl.codex.* |
| engine-made: `.claude/skills/<name>`, `.claude/rules/<name>.md`, `.claude/agents/<id>.md` (T1 symlink or copy), `.codex/agents/<id>.toml`, `.agents/skills.lock`, `.agents/framework.yaml` (init flag only) | engine | eng.apply |
| Claude plugin: `skills/{init,update,reprove}/SKILL.md`, `hooks/hooks.json` + `hooks/session-start-uninitialized.py` | `src/plugin/claude-code/` | pc.* |
| Codex plugin: `skills/agentive-{init,update}/SKILL.md` + `agents/openai.yaml` (payload copied in by the generator) | `src/plugin/codex/` | px.* |

Run-time library modules owned by B (inside A's unit `lib.agentive`): `nohuman` (the one no-human rule),
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
2. U2 two-call flow: without confirm flags, confirm_scope files are skipped, recorded in pending-confirmation.json
   (interactive too) and printed as `AGENTIVE_CONFIRM`; the skill asks once and reruns with `--confirm-all`. An
   `init` rerun is a continuation while pending-confirmation.json holds an init entry. `.agents/framework.yaml`
   (the REVIEW.md answer) is gated as review_config like the other confirm_scope files.
3. Evidence: root-level package files only; primary ecosystem = most source files (D6); Makefile targets are
   rank 2 and replace rank-4 defaults; CI workflow parsing is not done in v1; node_modules and .venv are not
   vendor paths (no Read/Edit deny); tiers: lint/typecheck/test/verify inner, setup/build/run outer.
4. guard.yaml value forms: argv_prefix = list of Claude-syntax patterns (`*` = any characters); extra ids where one
   match kind cannot hold a design id: `publish-dry-run` (unless -> hook only), `infra-destroy-sql` (special);
   specials `default_branch_push`, `tag_push`, `unresolvable_rm_var`, `sql_without_where`, `sql_destructive`,
   `no_verify` (token-aware so `-m "fix -n"` is not a hit); SQL checks apply only when a SQL client is in the
   command. Default branch unknown (no origin/HEAD) -> main and master.
5. Region provenance is refreshed only when the body or the HEAD commit changed (an unchanged re-run writes
   nothing); a text override applies to the first region of the file (AGENTS.md#context, CLAUDE.md#imports,
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

## 5. Requests to A

1. `tests/generator/test_generate.py` fails after B and C added sources: `test_source_errors` expects
   `src/engine/stray.py` to be an orphan, but `src/engine/` is now the directory unit `eng.apply` (the stray file
   gets a header error instead); `test_shared_unit_regions` creates `src/repo-layer/AGENTS.md.regions`, which C now
   ships (FileExistsError). Suggested fix: use `src/engine-stray.py`-style paths outside existing units (for
   example a new file under `src/plugin/claude-code/zz-stray.md` or `src/repo-layer/zz-stray.txt`) and `exist_ok`
   / a different region file in the shared-unit test.
2. `claude plugin validate` warns ICON_MISSING for the Claude plugin (no `.claude-plugin/icon.png`); the
   validator also advises an install line in README.md. Both are A's files.

## 6. Cross-owner notes and open items

- C (answers to docs/impl/C.md §5): (1) `render.code_review_copy` is implemented (byte copy; REVIEW.md rendered
  after code_review.md is written). (2) `rule_packs.boundary`: no run sets it in v1. project-config has no such key
  (additionalProperties false) and the manifest has none either, so the pack stays off until a design change adds
  the key; `render.boundary_line` stays empty. (3) handoff CLI added (`python3 -m agentive.handoff write ...`).
  The review-rules hooks globs include `.agents/hooks/**` (checked).
- D: run the engine with `CI`, `GITHUB_ACTIONS` and `AGENTIVE_HEADLESS` removed from the subprocess environment;
  CI sets them, and a headless engine run ignores `--confirm-all` (confirm_scope files are then skipped). Pass
  `--values <file>` with `{"tools": {}}` to skip the `claude --version` / `codex --version` probe (faster, same
  manifest on every machine). `.agents/state/` is excluded from "re-run changes nothing" comparisons.
- D: verb tiers for the CI outer-verbs job: inner = lint, typecheck, test, verify; outer = setup, build, run.
- Open: Codex hook `systemMessage` support, `commandWindows`, execpolicy check against a real Codex (UV-31) and
  T1/T3 probes are not run (미검증); FileChanged input field name read as `file_path` (fallback `path`) 미검증.

## Changes

- 2026-10-28: sections 1 and 2 published; `render.code_review_copy` added (C request); CLI additions `--accept`,
  `--tool`, `--prove`, `--today`, `--print-evidence`.
