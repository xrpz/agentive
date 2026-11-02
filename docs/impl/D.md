# Review pipeline, CI and tests, area D (contracts and decisions)

Area D of docs/IMPLEMENTATION.md §7. Units: `src/units/review.yaml`.
Run-time modules `.agents/lib/agentive/review/**` and `.agents/lib/agentive/lint/**` belong to the unit `lib.agentive`
(src/units/foundation.yaml); their headers say `area D`. Open items are in §5.

## 1. Files

| area | path (source) | installed as |
|---|---|---|
| CI | `src/repo-layer/dot-github/workflows/agentive-review.yml` | jobs outer-verbs, dependency-review, ai-review, agentive-independence, agentive-review-gate |
| CI | `.../workflows/agentive-lint.yml`, `.../workflows/reviewer-eval.yml` | jobs agentive-lint, reviewer-eval |
| scripts | `src/repo-layer/dot-agents/bin/agentive-review`, `agentive-lint`, `agentive-reviewer-eval` | `.agents/bin/` |
| library | `src/repo-layer/dot-agents/lib/agentive/review/*.py` (util, gitio, config, rv1, rv2, rv3, rv4, rv7b, rv8, findings, pr_body, independence, killswitch, tasks, pipeline, github, post, evals), `.../lint/checks.py` | `.agents/lib/agentive/` |
| prompts | `src/repo-layer/dot-agents/agents/{correctness-reviewer,security-reviewer,testing-reviewer,verifier,coordinator,classifier,raise-only}.md` | `.agents/agents/` (B renders `.claude/agents/`, `.codex/agents/*.toml`) |
| eval docs | `src/repo-layer/evals/reviewer/README.md` | `evals/reviewer/README.md` (case format; no case is generated) |
| fixtures | `fixtures/{ts,py,go,java}/` (dot paths escaped: `dot-gitignore`, `dot-golangci.yml`) | test input only |
| tests | `tests/review/` (stages, pipeline end to end, lint, prompts, eval runner), `tests/ci/` (workflows, terminology), `tests/sample_repo/` (deterministic init/apply) | — |
| optional | `.github/workflows/selftest-agent-run.yml` + `tests/sample_repo/agent_run.py` (agent-run half, needs `ANTHROPIC_API_KEY`) | framework repo only |

`REQUIRE_SAMPLE_REPO_TESTS = True` in tools/selftest.py (sample_repo_tests run against B's engine on all 4 stacks).

## 2. Contracts D offers

### `.agents/bin/agentive-review` (exit 0 done, 1 blocked, 2 error, 3 tasks pending)
- `precommit [--tool claude|codex] [--run interactive|non_interactive] [--human-confirmation obtained|not_obtained]`: profile precommit
  (RV1 from `.agents/state/verify.json` when its content_tree is the index tree, VERIFYTREE, RV2, RV5 `profiles.precommit.reviewers`) on
  `git diff --cached`; writes `.agents/state/precommit-review.json` (schema precommit-review) and
  `.agents/state/review-report.json`; last line `AGENTIVE_PRECOMMIT=commit | blocked | confirm ...; NEXT=...`.
  Work dir `.agents/state/review/precommit-<index tree>`: a rerun with `--human-confirmation obtained` reuses the review.
  `obtained` is refused when a no-human signal is set (AGENTIVE_HEADLESS=1, CI, GITHUB_ACTIONS). C's precommit-review
  skill uses exactly this.
- `rv2 --base S --head S [--rules-file F]` (SECPATHS): RV2 alone on a commit range with the working-tree rules; prints
  `AGENTIVE_RV2_HIT path= layer= glob= reason= source=` per hit, `AGENTIVE_RV2_SHADOW ...` per shadow rule hit,
  `AGENTIVE_RV2_PROBLEM <text>` for an invalid block and `AGENTIVE_RV2=done hits=<n> shadow_hits=<n>; NEXT=...`. /distill uses it as the proof of a learned rule.
- `ci <pr> [--outer-results F]` (ai-review job), `gate <pr> [--apply]` (agentive-review-gate), `independence <pr>`
  (ok | fail | exempt reason=<code>; `ok note=ci_review_skipped:<kind>` for a workflow-validation skip, E2E-11), `outer-verbs --out F`,
  `checks [--must-run NAME]` (a skipped or empty job counts as failed), `outputs-hash --work D [--expect H]`, `llm-state --work D`
  (E2E-10b: `llm-state.json` and one `::error` line with a code, never the error text), `merge-group --head-ref`,
  `eval --base --head --work` (reviewer eval runs). `--pr-json` / `--comments-json` replace gh api reads (tests). The gate reads the
  environment `AGENTIVE_DEFAULT_BRANCH`, `AGENTIVE_HEAD_REPO`, `GITHUB_SHA`, `GITHUB_REPOSITORY` (env, so an older base-commit gate
  script ignores them).
- LLM task protocol (tasks.py): `AGENTIVE_REVIEW_TASK id=<task> agent=<agent> input=<path> output=<path> [meta=<path>]` lines, then
  `AGENTIVE_REVIEW=tasks:<n> stage=<RVx> NEXT=...`. The agent runs the named subagent in a fresh context with
  `Read <input> and do the task it describes. Reply with the JSON object only.`, writes the reply verbatim to
  output, reruns. Agents: classifier (RV3), raise-only (RV4), correctness/security/testing-reviewer (RV5),
  verifier (RV7), coordinator (RV6).
- CI artifact `agentive-review-<head sha>` = the work dir (task inputs/outputs, `round.json`, `review-report.json`,
  `reviews.json`); the gate and independence jobs read it (same run: download-artifact; other runs: gh api).
- Finding comments carry `<!-- agentive-finding: <id> -->` (design) and `<!-- agentive-finding-data: {json} -->`;
  the summary comment carries `<!-- agentive-review-summary -->`; demotion issues carry
  `<!-- agentive-demotion: tier=<t> source=<login> -->`.
- PR body: the gate writes `agentive-review.pipeline`, `review_round` and the CI `reviews[]` (source ci) into the
  existing block (gh api PATCH). A body without a valid block is left alone and the summary comment carries the result.

### `.agents/bin/agentive-lint`: `ci` | `size provenance check-tags persist skills lock rules links` | `rules --write` | `rubric --effective`
One `LINT <sub> <path>[:<line>]: <what>. NEXT: <fix>` per problem, then `AGENTIVE_LINT=ok|fail:<subs>; NEXT=...`.
C's verify skill calls `agentive-lint lock`. `skills` also fails a shared skill that calls a script skill-root relative,
through `..` or into another skill (SKILLPATHS). `rules` (in `ci`) and `rules --write` run B's `agentive.rulesync`, the
renderer /agentive:update uses, so CI fails when a rule in `.agents/rules/` was committed without its AGENTS.md render
(RULESNEUTRAL).

### `.agents/bin/agentive-reviewer-eval`: `detect | run | score | comment` (see the script header).

`run` uses the effective CI tool of `--config-ref` per side (CITOOL): `review.pipeline.ci_tool`, else manifest
`framework.init_tool`, else claude (`agentive.review.config.ci_tool`). claude: the headless standard command
(`claude -p`); codex: `codex exec --sandbox workspace-write --ephemeral "<prompt>"` in the worktree. Binaries:
`AGENTIVE_CLAUDE`, `AGENTIVE_CODEX`. The workflow passes its secret as `AGENTIVE_CI_API_KEY`; the script maps it to
`ANTHROPIC_API_KEY` (claude) or `CODEX_API_KEY` (codex) for the run only. A side whose CLI is not installed (the PR
switched `ci_tool` and the workflow installs only its own tool) writes `.failed` files, which the score reports as not run.
EVALBUNDLE (2026-11-01, docs/impl/decisions-log.md §27): `run` uses a case's `base_sha`/`head_sha` when both exist; `score` writes the optional
`wrong_place` list and passes the case files and the per-run results to `evals.comment(..., detail=)` for the changed-case table.

## 3. Design judgments [설계 판단] (also in the file headers)

1. Workflow layout: one file `agentive-review.yml` for the five jobs the gate depends on (results flow through
   `needs`), separate `agentive-lint.yml` and `reviewer-eval.yml`; job ids = design job names = check names.
2. The gate job runs `.agents/bin/agentive-review` from a worktree of the base commit (policy and gate code never
   from the PR branch); first-install PRs (no script on base) use the head script and are tier 2 anyway (.agents/**).
3. The gate recomputes every deterministic stage itself and takes only LLM outputs from the ai-review artifact, which
   can only raise risk; a missing output is fail-safe (RV3 other, RV4 tier 2, RV5/RV6 human_required with reason
   llm_raise, RV7 inconclusive). Fork PRs without secrets therefore end human_required.
4. LLM task protocol (script never calls a model; the agent runs subagents and writes their JSON); one verifier task
   per round for all new findings (one fresh context), one coordinator task; task session ids are uuid4 values
   recorded as reviewer/verifier session ids.
5. Finding ids: `same_as` from the reviewer, else `F-<sha256(file, bug class or trigger)>`; finding data comment
   rebuilds open findings in the next round; in full mode a finding not reproduced is fixed (next_round) only when a
   reviewer actually ran; in incremental mode the reviewer reports `still_present|fixed` per open finding.
6. Convergence cap: from round 3 an open blocking finding turns the unanswered-finding block into human_required
   (review_rounds_exceeded); failing checks still block. A `fixed` reply on the reviewed head is a claim and blocks.
7. RV1 detection: test-file patterns, export-like removed lines for public API, package-file dependency lists, env
   lookups not present in the other tree, history relative to the base commit time (reproducible reruns).
8. RV2: evidence config files from verbs lint/typecheck/format; multi-purpose files only as `path#section`;
   REVIEW.md counts as protected_check_config; rules invalid -> defaults + always_human; CODEOWNERS not read.
9. RV3 rule order (header of rv3.py); a project path rule may give a tier-0 class only when that class's
   mechanical check holds.
10. RV7b thresholds read from the item `hit` text (`>400`, `>=2`, class list); disabled items are not listed.
11. Kill switch source = PR author login; auto-merged PR = body block `pipeline.gate.auto_merge: true`; merge method
    for `gh pr merge --auto` = first allowed of squash, merge, rebase.
12. dependency-review status: action outcome success -> pass; failure with `vulnerable-changes` -> fail; else
    unavailable (a `::notice::`). Information only (DEPHUMAN): a change of dependency entries or of a lockfile is RV2
    `dependency_change` (tier 2) whatever the status says; fail keeps its always_human line and failing check.
13. agentive-lint: SKILL.md tokens = bytes/4; Codex combined size per AGENTS.md path; check-tags needs a fix sentence
    (>= 3 words) except distill markers `noise-*`/`eval-*`, ignores backtick text and generated files; links on when
    `.agents/link-check.txt` (the ignore list) exists; side-effect detection by command patterns.
14. Reviewer eval: the reviewer config overlaid on the case head = `.agents/agents`, `.claude/agents`, `.codex/agents`,
    `.agents/bin/agentive-review`, `.agents/lib/agentive`, `code_review.md`, `REVIEW.md`, `.agents/framework.yaml`,
    `.agents/rubric.yaml`; eval runs read policy from the working tree; a failed run fails
    that case run; the library of the pipeline counts as a reviewer change with the script. The headless command adds
    `Bash(.agents/bin/agentive-review *)`, read-only git commands and `Task,Agent` to the standard tool list (the
    review run needs them).
15. Agent-run self-test: stacks ts and py; triggered by changes under src/engine, src/plugin, src/repo-layer,
    src/units, tools/generate.py, tools/bundles.yaml, fixtures, tests/sample_repo.
16. Fixtures: Go has no go.sum (no dependencies); the Java `gradle.lockfile` is hand-written (Maven Central was not
    reachable from the build sandbox) — 미검증 until a `gradle test` run.

17. CITOOL (2026-10-29): review records written by the ci job name the effective tool (`claude_code` or `codex` in
    review-report `rv5.reviewers[].tool` and `reviews.json`). Local records name the real tool (item 21). The codex step
    uses `sandbox: workspace-write` (the action's README lists it as the legacy option and recommends
    `permission-profile: ":workspace"`; which profile equals workspace-write is 미검증). `id-token: write` is rendered
    for the claude path only (item 18). The 2026-10-29 revision ("지워", user) removed the local rule `both_tools`: the
    user chooses the local review tool; the skills no longer pass `--tool` (E2E-59, decisions-log §35): the record's `tool` is detected (item 21), the flag stays accepted.
18. CISPLIT (V2-09, 2026-10-29): the ai-review job runs no PR code. `outer-verbs` (permissions `contents: read`, checkout
    `persist-credentials: false`, no secret) runs the verbs and uploads `outer-verbs.json` as the artifact
    `agentive-outer-verbs-<head sha>`; `ci --outer-results` hands the sanitized results (at most 8 entries, `log_tail` <=
    4000 chars) to the verifier as `execution {mode: read_results, results}`; locally the payload says `run_commands`.
    Both tool paths lost `Bash(.agents/bin/verb *)`; the claude step keeps only the pipeline command and read-only git.
    [설계 판단] `outputs-hash`: a sha256 over `tasks/*.output.json` is a job output of ai-review and recomputed in the
    gate job; on a mismatch the outputs are renamed `.rejected` (exit 0, RV8 then ends human_required). It is cheap and
    closes the window between the LLM step and the artifact download; a gate script older than the command (first
    install, gate script from the base commit) prints a note and goes on. Job outputs and PR text reach scripts through
    `env:` only (found while splitting: `${{ needs.*.outputs.results }}` was spliced into `run:` text).
19. BOTPR (V2-08): `independence.exemption(pr)` -> `bot_pr_exempt` (author type Bot or login ending `[bot]`) or
    `human_pr_exempt` (no agentive-review block) [설계 판단: agent-only heading check; the majority of required checks
    keep an exemption path]. The pipeline raises an exempt PR to tier >= 1 after RV4 (tier0 reason replaced), passes
    `exempt` to RV8, which answers human_required with the code; never auto-merged. The gate job passes
    `--must-run agentive-independence`: `skipped` or empty counts as failed (GitHub treats a skipped required check as
    success). Dependabot and fork PRs have no Actions secrets: the LLM stage cannot run, RV8 ends human_required
    (fail-safe); init prints one report line. `.github/pull_request_template.md` (`f-pr-template`, confirm_scope ci) has
    the three headings; it is not enforcement.
20. INDEPCI (V2-07/V1-11): "enforced in CI, attested locally". The only review that counts is one in `reviews.json` of
    the CI artifact with `source: ci`, `run.sha == PR head sha` and a run id (run_id, run_attempt, sha, and the action's
    `session_id` output when the action returns one, read from env `AGENTIVE_ACTION_SESSION_ID`, kept for the same sha
    across an edited-event rerun). A review written into the PR body never counts, whatever it says (the implementer's
    side writes the body); the session-id inequality check is deleted (always true: the CI id is a uuid we generate).
    `independence.evaluate` returns ok | fail | exempt; PR-body local records get a format check only.
21. Local record fields (`review/attest.py`) [설계 판단]: `tool` (flag `--tool`, env `AGENTIVE_TOOL`, `CLAUDECODE`, else
    `other`), `executed_by` subagent | process | self | unknown, `attestation` hook_observed | self_reported | ci_job,
    `context` isolated | shared | unverified (`fresh` dropped), `session_id`, `agent_id`, `agent_type`, `reviewed_ref`.
    `isolated` needs a SubagentStart/SubagentStop line (`.agents/hooks/subagent-observe.py` ->
    `.agents/state/subagents.jsonl`) of the task's agent type with an agent_id inside the task's time window (10 s
    slack). The orchestrator writes `{"executed_by": "subagent"|"self"}` to the `meta=` path of each task line; `self`
    (the do-it-yourself fallback) is recorded as `shared`, with `self_reason` no_agent_tool | spawn_failed when the meta's
    `reason` names one (other values dropped), and `agentive-review precommit` then prints one `NOTE independence=self
    reason=<r>` line before its last line (E2E-17, PRECOMMITINLINE). The hook is Claude Code only: Codex local reviews are
    `unknown` / `unverified` (a Codex hook is not wired; its SubagentStart input is 미검증). Optional for Codex:
    `codex exec --sandbox read-only`. `hooks` and `settings.json` are U3 confirm paths: the author confirms before commit.

Review tool spelling and reviewer rules [설계 판단]: `--tool claude` is read as `claude_code` (`attest.tool_arg`, the argparse type of
`agentive-review precommit --tool` and `pr-body.sh draft --tool`, which the skills no longer pass: E2E-59), the same two words as the engine and
`review.pipeline.ci_tool`: `claude | codex`. The rules every reviewer follows for a finding (line numbering, trigger, severity, bug_class,
same_as, open findings) are written once in `review/tasks.FINDING_RULES` and travel in every reviewer task input as `finding_rules`;
`correctness-reviewer.md`, `security-reviewer.md` and `testing-reviewer.md` keep only what is specific to their role and point to that
field (a subagent reads the task file first and sees nothing else of the conversation).

### SECPATHS (2026-10-29): security-sensitive path detection (`review/secpaths.py`, `rv2.py`, `config.py`)

22. Matching is case-sensitive. `secpaths.glob_dir_match`: a glob that does not end in `**` also matches everything below a
    directory it matches (`**/*secret*` -> `secrets/prod.yaml`); applied to `never_ai_approve`, `confirm_scope_globs`,
    `protected_check_config` and the built-in protected list, not to `classify.path_rules` or RV1 exclusions.
    `token_match`: path segments split at `_ - . ` and spaces, exact token compare, no camelCase split.
23. The floor lives in `config.DEFAULT_RULES` (13 globs incl. `**/SECURITY.md`, 6 token groups) and in the shipped block
    (`rules.md`); `load_rules` returns `effective_trust_boundary` (floor if `extend_defaults` + block + `add` - `remove`, tokens
    expanded, `content_rules`, `removed`). `remove` removes exactly one key (glob, token, or path + contains); a missing reason
    makes the block invalid (defaults + RV4 `always_human`). A shadow entry never reaches `hits` (`rv2.shadow_hits`).
24. Hit layers: `org` (CODEOWNERS snapshot, `add`, hand-written), `default` (the floor), `content` (content rules and presets),
    `protected` (built-in). A token hit is skipped when a glob already hit the same path with the same reason.
25. The CODEOWNERS file (three places) is in `PROTECTED_CHECK_CONFIG`; `prbody.py` (local tier estimate) runs RV2 itself through
    `pipeline.rules_estimate` (LOCALTIER, 2026-10-31), so it uses the same boundary, matching and content rules.
26. `rv2.security_checks`: manifest `security_checks` x the run's check results (a check matches when its name holds the tool id,
    case-insensitive); `fail` -> `always_human`, `not_run` is only recorded.
27. `rv4.raised_by: rv4_llm` + `rv4.raised {paths, reason, criteria}`; the raise-only answer has an optional `paths`.

### Bugfix batch (2026-10-29, implementation verification V2-01 ... V2-24)

28. V2-01 RV3 `format` [설계 판단]: `git diff -w` is only a first filter. A modified file is format when (a) Python
    (`.py .pyi .pyw`): both versions parse with the stdlib `ast`, `ast.dump` is equal and the `tokenize` comments are equal;
    (b) never for a whitespace-significant type (YAML, Makefile, Haskell, Nim, CoffeeScript, Pug, Haml, Slim, Sass, Stylus, F#,
    Elm, GDScript, shell, reStructuredText, Markdown, TSV, patches, snapshots, Dockerfile, Procfile); (c) otherwise only when no
    version holds a multi-line string or heredoc opener (backtick, `"""`, `'''`, `R"(`, `@"`, `r#"`, `<<EOF`, `<<<`) and the
    non-blank lines are equal after collapsing whitespace runs, a line with a quote or a trailing backslash may differ only in
    leading whitespace. Known residual: a blank line added inside a multi-line literal of a language without an opener in the
    list. `rv3.whitespace_only_texts` is shared with the local estimate in `prbody.py` (C) so both say the same.
29. V2-02 RV3 `docs` [설계 판단]: `.md .mdx .rst .adoc .asciidoc` at the repo root, under a directory named docs, doc,
    documentation, wiki, adr or adrs, or with a documentation file name (README, CHANGELOG, CONTRIBUTING, LICENSE, NOTICE,
    ARCHITECTURE, ...); `.txt` only in those places or with those names; images only in a docs directory. Package and build
    manifests come first (`requirements*.txt|in`, `constraints*.txt` and the package files: deps; `CMakeLists.txt`: config).
    Never docs (class config): the design list (CLAUDE.md, AGENTS*.md, code_review.md, REVIEW.md) plus the instruction and
    prompt files of other agents (CLAUDE.local.md, GEMINI.md, SKILL.md, `*.agent.md`, `*.prompt.md`, `*.instructions.md`,
    copilot-instructions.md, `.github/instructions/**`, `.github/prompts/**`, `.cursor/rules/**`, `.cursorrules`,
    `.windsurfrules`, `.clinerules`). Making them tier 2 (RV2 floor) is a design change: left to the user.
30. V2-03 / V2-14 precommit, revised by VERIFYTREE (E2E-08, 2026-10-30): the verify record counts only when `verify.json
    content_tree` equals the index tree (`git write-tree`) and its verb commands are the manifest's (`verb.fresh_for`);
    verify before or after `git add` both count when everything is staged; a partial commit needs `verb verify --staged`
    (the blocked NEXT names it); a record without content_tree is stale. An unborn HEAD reviews against the empty
    tree and records `head_sha` 40 zeros (like verify.json). `checks_ok` is true when no check failed and either one passed or
    every check is skipped (all verbs pending, the greenfield state); then the last line is `AGENTIVE_PRECOMMIT=commit ...;
    verification=pending (...) ...; NEXT=commit ...`. A blocked line names `verify=missing|stale|red`.
31. V2-04 reviewer eval `_overlay`: a path of mode 120000 is recreated with `os.symlink(<target from git show>)`; where no
    symlink can be made (core.symlinks=false platforms) git's own behavior applies (the target text in a file).
32. V2-15: the ai-review job checks out `github.event.pull_request.head.sha` (reviewers cite head-tree lines); `eval` records
    `rev-parse` of `--head`.
33. V2-17 kill switch [설계 판단]: after the 8-day `merged_prs` read, every `This reverts <sha>` of the window whose PR is not in
    that list is looked up with `GET /repos/{o}/{r}/commits/{sha}/pulls`, and every `hotfix_of` number with
    `GET /repos/{o}/{r}/pulls/{n}`; at most 20 lookups per gate run (API budget).
34. V2-10: `agentive.common.tree_sha256` skips `__pycache__` directories, and every installed python entry point sets
    `sys.dont_write_bytecode` (the sh shims run `python3 -B`), so no bytecode lands in `.agents/` of a user repo (V1-08).
35. E2E-07 (high, a real GitHub Actions run): `review/github.py` defined `pull` twice; the second (merged form or None)
    replaced the raw one, so `pr_context` crashed on every open PR, ai-review uploaded no artifact and the later jobs
    reported only "Artifact not found". The merged form is `merged_pull` (caller: the kill switch `hotfix_of` lookup);
    `pull` is the raw PR (callers: `pr_context`, reviewer eval `#<n>`). [설계 판단] The gate notes a missing ai-review
    report (no `review-report.json` in the work dir after the artifact download and `--fetch-prior`) as `ai_review_missing`:
    a free-text `rv8.reasons` entry plus the code in the `AGENTIVE_GATE ... reasons=` line and a NEXT naming the failed
    ai-review job. It is not added to `rv8.reason_codes`, because `$defs.gate_reason` in review-report.schema.json is the
    design's closed list (no design change here). The decision stays RV8's (no LLM outputs -> fail-safe tier 2:
    human_required, or block on explainer_missing or a failing agentive-independence check); a `pass` is turned into
    human_required with auto_merge false as a guard. [설계 판단] `tests/generator/test_source_lint.py` (stdlib `ast`; ruff is
    not a dev requirement) fails on a def or class name repeated in one scope in `src/**` and `tools/**` python sources,
    shebang scripts included; `@x.setter`-style accessors and `@overload` are allowed.

### End-to-end decisions batch A (2026-10-30: E2E-10b, E2E-11, E2E-12, E2E-13)

Summary in docs/impl/decisions-log.md §16. Choices left open by the design [설계 판단]: the per-code substring patterns of
`llmstate.PATTERNS`; `cancelled` counts as a failed LLM step; the gate turns a `pass` into human_required when the LLM step failed or
the action skipped (guard, like ai_review_missing); a fork is reported but not exempted; the independence job fetches the base
commit and the default branch with depth 1; one first-parent history call feeds every history signal (incident_history included);
production files leave out lock, generated and vendor paths besides tests and docs.

## 4. Notes for the other areas

- Content area: the default review-rules block validates (review-rules schema) and parses with ayaml; its hooks globs include
  `.agents/hooks/**`; permissions adds `.agents/guard.yaml` (the built-in fallback uses the same list).
- Plugin area: renders `.agents/agents/*.md` (symlink to `.claude/agents/`, `.codex/agents/*.toml`) as expected; frontmatter keys
  used: name, description, tools, model: inherit.

## 5. 미검증 (unverified, to check on a real repository)

- Checked 2026-10-29 in the official openai/codex GitHub source (commit 23934d31; the developers.openai.com and
  learn.chatgpt.com pages refused the fetch, PROVENANCE_REQUIRED): `.codex/hooks.json` accepts a root `description` and `hooks`
  (unknown root keys rejected: `deny_unknown_fields`), events include SubagentStart and SubagentStop, a command handler
  accepts `command`, `commandWindows` (alias `command_windows`), `timeout`, `statusMessage`, `additionalContextLimit`
  (codex-rs/config/src/hook_config.rs, codex-rs/protocol/src/hooks.rs); a hook `systemMessage` is shown as a warning entry
  (codex-rs/hooks/src/events/*.rs); a custom agent TOML overrides only `developer_instructions`, `model`, reasoning,
  verbosity, personality, service tier, can only turn features and skills off, and has no sandbox key
  (codex-rs/core/src/agent/role.rs, V2-11); SKILL.md frontmatter keeps `name`, `description`, `metadata.short-description`
  and ignores other keys (codex-rs/skills/src/parser.rs, UV-3); AGENTS.md files are concatenated as text, with no `@` import
  (codex-rs/core/src/agents_md.rs, UV-13). Source code is not documentation: a later Codex release can change any of these.
- Still 미검증 (V2-22): whether Codex runs project custom agents in parallel with separate contexts (UV-15), plugin skill
  name spaces (`$agentive-init`), which marketplace file wins when both exist, the `commandWindows` relative form.

- SECPATHS (2026-10-29): the precision of the CODEOWNERS owner-name heuristic on real files; the CODEOWNERS pattern table for an
  interior slash (anchoring is the gitignore rule, the GitHub page was read for leading and trailing slashes); GitLab path case
  rules; Kubernetes RBAC kinds and Terraform `aws_iam_*` as content signals (deliberately not shipped); the Django `DEBUG`
  substring noise; presets read only the root package files (monorepos); whether the check names of gitleaks, semgrep, codeql
  and trufflehog workflows contain the tool id.

- openai/codex-action@v1 (CITOOL): inputs `openai-api-key`, `sandbox`, `prompt` are taken from the action README
  (fetched 2026-10-29, S162); not run on a real repository. 미검증: that `codex exec` in the action loads the project
  `.codex/agents/*.toml` custom agents in a CI checkout (project trust) and starts each in a fresh context (UV-15);
  that `AGENTIVE_HEADLESS` reaches the codex process; that `git` works under the workspace-write sandbox when `.git` is
  read-only; `codex exec` needs no approval flag; `CODEX_API_KEY` (S100) for the reviewer eval; the action's behavior on a
  blocking finding (UV-6).
- claude-code-action@v1 inputs `prompt`, `claude_args` (official action docs as mirrored), `id-token: write` need
  (GitHub App OIDC path per the action FAQ; whether the `github_token` input path works without it is 미검증).
- CISPLIT / INDEPCI (2026-10-29), none run on a real repository: the codex-action sandbox keeps the ai-review job from
  executing code (the guarantee comes from the job split, not from the sandbox); the `session_id` output of
  claude-code-action and codex-action; reuse of the artifact task outputs on an `edited` rerun; the SubagentStart and
  SubagentStop input fields (`agent_id`, `agent_type`) as documented for Claude Code; `CLAUDE_CODE_SUBPROCESS_ENV_SCRUB`
  is not used (the split made it unnecessary); `python-version-file: pyproject.toml` (reads requires-python); the
  `pip install poetry|pipenv` steps; `pnpm/action-setup@v6`, `actions/setup-{node,python,go}@v7`, `setup-java@v6`,
  `astral-sh/setup-uv@v7` inputs as read from their action.yml on 2026-10-29.
- dependency-review-action output `vulnerable-changes`; the check run of a pull_request_review_comment run attaches
  to the PR head commit; `gh pr merge --auto` with GITHUB_TOKEN (UV-16); REST reply bodies with author_association
  (UV-37); upload-artifact `include-hidden-files`.
- Subagent tool names `Task` / `Agent` in `--allowedTools`; `claude --plugin-dir` for the agent-run self-test.
- E2E-10b / E2E-11 (2026-10-30), not run on a real repository after the change: the SDK result fields (`subtype`, `is_error`,
  `result`) of the execution file (read from the action's source); that codex exec prints the API error into the job log; the
  claude-code-action skip signature `outcome success` + empty `conclusion` (source at 6d5ae52); what the Anthropic server compares
  for the workflow validation; `GITHUB_SHA` of a pull_request_review_comment run; GitHub's evaluation of the OAuth-first expression
  (E2E-12) with both secrets set.
- 2026-11-01 COMMENTUX, E2E-34, E2E-35 (docs/impl/decisions-log.md §23), not seen on a real repository yet: the rendering of the new
  summary and reviewer-eval comments and the gate step summary on GitHub (web, mobile, e-mail); the first-install display copy
  (`rv8.required_checks.display`) on a real install PR.
