# agentive implementation decisions log (§10 to §35)

Dated implementation notes that used to be sections 10 to 33 of `docs/IMPLEMENTATION.md` (moved 2026-11-02, sections 34 and 35 were added after the move;
`decisions.accepted[IMPLSPLIT]`). The section numbers are unchanged, so section 23 here is the note that older code
comments named as section 23 of `docs/IMPLEMENTATION.md`. This file is history, not a checklist: read only the section a
reference or a design record sends you to. The rules from these notes that still hold for new work were copied into
`docs/IMPLEMENTATION.md §5` (items 10 to 24); where this file and sections 1 to 9 of that file differ, those win.
A reference to one of sections 1 to 9 names the file (`docs/IMPLEMENTATION.md §4`); a bare `§N` here means a section of this file.

## Contents

- §10 JSON key merge, init writes without asking, no generated map
- §11 Default policies, finding identity, label triggers, pending checks, lint scope
- §12 Security-sensitive path detection (SECPATHS)
- §13 Bugfix batch from the implementation verification (V1-*, V2-*)
- §14 Final triage: skill paths, headless bins, no boundary line, tool-neutral rules
- §15 End-to-end bug fixes E2E-01 to E2E-05: the installed layer is not linted
- §16 AI step failure, workflow change, CI credential, rubric@2 (E2E-10b to E2E-13)
- §17 Base ref, verify on the staged tree, region notice without a sha (E2E-06, E2E-08, E2E-09)
- §18 Human merge, REST only, no baseline, inline precommit review, plan-mode gate (E2E-14 to E2E-18)
- §19 Effective required checks, lockfile dependencies, review record, pending notice (E2E-19, E2E-21 to E2E-24)
- §20 Dependency changes need a human (DEPHUMAN, E2E-20)
- §21 Wording and display rechecks (E2E-25 to E2E-27)
- §22 Explainer visual spec and local tier estimate (E2E-28 to E2E-31)
- §23 Comment readability, headless Bash hint, sandbox caches (COMMENTUX, HEADLESSHINT, E2E-32 to E2E-38)
- §24 Dependency review wording, finding line placement (E2E-30, E2E-31 rest, E2E-39)
- §25 Skill scripts for fragile steps, no dates in skill text (SKILLGUIDE)
- §26 Labels belong to PR publication (PRLABEL, E2E-40)
- §27 Reviewer eval bundle: case supply, line tolerance (EVALBUNDLE)
- §28 CI fixes: Python runner image, backup commit without a git identity
- §29 End-to-end run PRs #1 to #5 and CI of 278bebc (E2E-41 to E2E-46)
- §30 Minimal end-to-end run (E2E-47 to E2E-51)
- §31 `/pr` on an open PR whose body is not a /pr body (PRADOPT)
- §32 Second minimal run (E2E-52 fix, E2E-54 check)
- §33 Engine run from the template source: NEXT of an unreadable payload index (E2E-56)
- §34 Handoff, requirements and Routines handoff (CTXREINJECT, HANDOFFFILL, REQSTATED, OPENMARK, OPENWORK, HANDOFFREPORT, 2026-11-02)
- §35 Reply and install-path fixes: a `fixed` reply, the reply-run artifact, `--tool`, the `/chain-intent` branch (E2E-59 to E2E-61, 2026-11-02)

## 10. JSON key merge, init writes without asking, no generated map (2026-10-29)

Design: `decisions.accepted[JSONMERGE]`, `[INITWRITE]`, `[NOMAP]`; engine choices marked `[설계 판단]` in
docs/impl/B.md §4 item 2. Short version for anyone adding a template:

- A JSON settings file that users and other tools also edit (`.claude/settings.json`, `.codex/hooks.json`) is listed in
  `OWNED` of `src/engine/jsonmerge.py` with the top-level keys agentive owns. Do not add keys to a template outside that
  list: the engine would add them only when absent and never update them. A new hook script must live under
  `.agents/hooks/` (that path is the ownership marker).
- Every file a unit installs is written without a question. Put a risky behavior behind `--no-hooks` (units with
  `confirm_scope: [hooks]`), not behind a prompt. A new hook script needs a clean exit 0 on the smoke input
  (`Engine.SMOKE_INPUT`); otherwise `/agentive:init` leaves settings.json unwritten.
- `render.orientation` and the region templates carry commands, test locations and pointers to documents that exist
  (`render.doc_pointers`). Never add a generated list of directories or modules; the budget is about 20 lines of
  generated text in the managed regions, 200 lines per AGENTS.md/CLAUDE.md, and an `agentive-lint size` warning at
  24 KiB of AGENTS.md files on one path.

## 11. Default policies, finding identity, label triggers, pending checks, lint scope (2026-10-29)

Design: `decisions.accepted[DEFAULTS]`, `[PENDINGNOTICE]`, `[FINDINGID]`, `[LABELTRIGGER]`. Choices the design left open are
marked `[설계 판단]`; claims not checked against an official page are `미검증`.

- DEFAULTS (V1-18). init writes no `.agents/decisions.yaml`. `agentive.review.config` holds the defaults: `AUTO_MERGE_DEFAULT`,
  U2 (`U2_CATEGORIES`, `pr_only_tier2`) and the rubric preset id; `.agents/framework.yaml` holds overrides only
  (`review.auto_merge: off | dry_run | low_risk | low_and_medium_risk`); U6 is the preset named in `.agents/rubric.yaml`.
  [설계 판단] the default is `low_risk` (the original U1=C: tier 0 merges, tier 1 runs in shadow and gets the label
  `agentive:shadow-auto-merge`), after the user's "원래대로 최소안으로 가자"; it is not the majority practice and the design says so.
  [설계 판단] `dry_run` labels every PR (tier 0 included) that would have merged. Migration (`/agentive:update`, `apply.py
  migrate_decisions` + `config.migrate_decisions`): U1 A -> `off`; U1 C with `review.pipeline.auto_merge.tier1: on` ->
  `low_and_medium_risk`; U1 B and U1 C otherwise equal the default and are dropped; a differing U2 or U6 is reported and
  ignored; an existing `review.auto_merge` is never overwritten; the `decisions.yaml` is removed when it is the unmodified file
  init recorded, else kept with a warning. A malformed `review.auto_merge` is read as `off` (fail-closed). The user sees the
  value in the init report line, the gate comment line and `agentive-lint settings --effective`.
- PENDINGNOTICE (V2-23). RV8 keeps the pending-verb rule (`human_required`, never auto-merged). The reason names the verbs and
  both exits (give the command, or remove the verb from `review.pipeline.stages.RV1.require_checks`); the same text is the
  init/update report (`AGENTIVE_REPORT auto-merge unavailable:` x2), the `settings --effective` reason, a gate-comment
  sentence and the Stop hook tail `DONE; PENDING=<verbs> ...` (all verbs pending keeps the greenfield `PENDING:` line).
  The agent asks the user once for the command and never picks a tool. Revised 2026-10-31 by EFFECTIVECHECKS (§19): the rule now
  applies to a pending verb the user named in require_checks; a pending verb of the default list is "not required".
- FINDINGID (V2-16). `review/findings.py`. [설계 판단] M3 is the same line hash (first 100 characters, whitespace stripped)
  within +-3 lines of the old anchor; an anchor line that cannot be hashed falls back to `line:<n>` as the anchor key; an
  unknown `bug_class` is stored as `other`; `unconfirmed` and `reopened` are fields of the report finding (schema
  `review-report`); a replied finding keeps its reply at the same location. Reviewer output is validated against
  `design/schemas/reviewer-output.schema.json` and again in the script. Renames come from `git diff -M`.
  미검증: whether `claude -p --json-schema` and `codex exec --output-schema` reject a bad enum on every version (the script
  re-validates, so a bad value becomes `other` or an invalid-output problem).
- LABELTRIGGER (V2-19). `agentive-review.yml` adds `labeled`. [설계 판단] exact allowlist `agentive:full-review` or a label
  starting `agentive-janitor:`; `outer-verbs` and `dependency-review` skip on every labeled event; `ai-review` and
  `agentive-independence` run on a labeled event only for the allowlist; the gate keeps `always()` and, when independence was
  skipped on a labeled event, evaluates it inline (a skipped required job would report success). A label run is not a new round;
  the `agentive:full-review` label discards the cached tasks only in the planning step and is removed after `--post`
  (`gh.remove_label`). `ai-review` has a per-PR concurrency group with `cancel-in-progress: false`.
  미검증 at run time: queueing semantics of that concurrency group, the event order when a label is added at PR creation,
  reuse of an old payload on a re-run, label removal by a token without write access on fork PRs, and GitHub's precedence between
  a skipped and a failed check with the same name. Labels added with `GITHUB_TOKEN` start no run (GitHub), so the janitor token
  must be a different one.
- Lint scope (V1-13 gap). See docs/impl/B.md §4 (PostToolUse lint scope): `evidence.lint_scope`, `file_args_for`.
- Public repo hygiene: no private working-file names in `design/`, `src/`, `docs/`, `tests/`, `tools/` (checked by
  `tests/design/validate_schemas.py`); a public GitHub URL that contains `context/<file>.md` (S66, S67) is allowed.

## 12. Security-sensitive path detection (2026-10-29)

Design: `decisions.accepted[SECPATHS]`, `spec.review_pipeline.stages[RV2].security_paths`, `spec.init_apply.security_evidence`,
`spec.maintenance.distill.distill.home.security_paths` (finding V2-18). Four layers: L1 what the organization declared
(CODEOWNERS snapshot, security tool results, the project's `add`), L2 the shipped floor (case-sensitive), L3 content rules and
stack presets, L4 the RV4 raise-only record. Choices the design left open are marked `[설계 판단]`; claims not checked in an official
source are `미검증`. Area files: docs/impl/D.md §3 items 22-27 and docs/impl/D.md §5 (review side), docs/impl/B.md §1 (`render.rules_*`).

- Code: `review/secpaths.py` (matching, entries, CODEOWNERS parser; shared by RV2, `prbody.py` and the engine),
  `review/config.py` (`effective_trust_boundary`: the block MERGES with the floor, it no longer replaces it), `review/rv2.py`
  (layers, shadow hits, content rules, security tool checks), `review/rv4.py` (`raised_by`), `src/engine/secscan.py` (init/update
  evidence: CODEOWNERS, presets, tools; the report lines), `agentive-review rv2` (the proof command of /distill).
- [설계 판단] Matching: a glob also covers the directory it names; a token matches one word of one path segment; no case folding
  and no camelCase split; content rules are plain substrings, `contains` may be a list.
- [설계 판단] CODEOWNERS: a line is copied only when an owner name holds `security`, `appsec`, `secops`, `infosec`, `sre`, `iam` or
  `privacy` as a word; the pattern table is in `secpaths.codeowners_glob`; `source: codeowners:<n>` carries the line number.
- [설계 판단] Presets are written by the engine into the managed list (not computed at run time), so RV2 stays a pure function of
  the base commit. Next.js reads the major version from the root `package.json` range; a range without a number gets both names.
  K8s RBAC kinds and Terraform `aws_iam_*` are not shipped (미검증, deferred in the decision).
- [설계 판단] A shadow entry (`shadow: true`) is report only; a learned rule is written that way and a later human-merged PR
  removes the flag. A security tool whose check did not run is recorded (`not_run`), only a failed check is an `always_human` item.
- To change the floor: edit `config.DEFAULT_RULES` and `src/repo-layer/code_review.md.regions/rules.md` together (a test compares
  them); update refreshes the managed region and the report names the entries that changed.

## 13. Bugfix batch: implementation verification findings (2026-10-29)

Findings V1-* and V2-* of the 2026-10-29 verification; triage, fixes and open user decisions are listed per area:
docs/impl/B.md §4 "Bugfix batch", docs/impl/C.md §5 items 14-17, docs/impl/D.md §3 items 28-34 and docs/impl/D.md §5. Regression tests are
named after the finding id (`test_v2_01_...`) in `tests/{hooks,engine,content,review}/test_regressions.py`,
`tests/content/test_stage_check.py` (V1-04) and `tests/sample_repo/test_prove.py` (V1-21, skipped where the stack's tools
are missing). Rules that follow for anyone adding code:

- A python entry point under `src/repo-layer/` or `src/engine/` sets `sys.dont_write_bytecode = True` before it imports
  `agentive`; a sh shim runs `python3 -B` (V1-08: no `__pycache__` in a user's `.agents/`).
- An agent-facing line a hook adds to the context ends with `NEXT=`; `systemMessage` is for the human only
  (code.claude.com/docs/en/hooks) (V1-06).
- RV3 tier-0 classes come only from the mechanical checks in `review/rv3.py` (`whitespace_only_texts`, `_is_doc`,
  `_manifest_class`, `NOT_DOCS`); the local estimate in `prbody.py` calls the same functions (V2-01, V2-02) [설계 판단].
- Facts about Codex taken from the official openai/codex GitHub source are marked with the file and commit; source code is
  not documentation, so a release can change them.

## 14. Final triage decisions (2026-10-29)

Design: `decisions.accepted[SKILLPATHS]`, `[HEADLESSBINS]`, `[NOBOUNDARYLINE]`, `[RULESNEUTRAL]`. Regression tests are named after
the decision (`test_skillpaths_...`, `test_headlessbins_...`, `test_noboundaryline_...`, `test_rulesneutral_...`) in
`tests/{content,engine,hooks,generator}/`, plus `tests/review/test_lint_eval.py::test_skills`.

- SKILLPATHS. Skill scripts are called `sh .agents/skills/<skill>/scripts/<x>.sh` from the repository root; the generator bakes the
  skill directory into each stage-check.sh (`gen.install_skill`). The headless standard command starts with
  `AGENTIVE_HEADLESS=1 CLAUDE_BASH_MAINTAIN_PROJECT_WORKING_DIR=1` (code.claude.com/docs/en/tools-reference). guard rule
  `agentive-path-traversal` denies `sh|bash|python3 .agents/skills/*../*` and `.agents/bin/*../*`: Claude permissions.deny and the
  PreToolUse hook (Claude Code and Codex); no Codex prefix_rule (a pattern token holds no `*`) [설계 판단]. 미검증: a live
  `claude -p` run showing that the mid-text `*` deny rule blocks the traversal (the hook test blocks it offline).
- HEADLESSBINS. `Bash(.agents/bin/agentive-lint *)` and `Bash(.agents/bin/agentive-review *)`, one entry per framework bin (a bin that
  /distill writes is not allowed headless). No Skill entry: the skills page says Claude can invoke any skill without
  `disable-model-invocation: true` by default, while the tools reference table says `Permission required: Yes`; a dontAsk run
  with Claude Code 2.1.312 invoked project skills without an entry. The content test pins the choice; a live re-check belongs to
  the agent-run self-test (미검증 for later versions).
- NOBOUNDARYLINE. No `Module boundaries:` line and no service facts lines in the managed regions; teams write them in their own
  AGENTS.md area. The boundary rule pack stays (default off).
- RULESNEUTRAL. `.agents/rules/<name>.md` is the tool-neutral source; `agentive.rulesync` (area B) renders AGENTS.md#path-rules
  and the pointer-mode `.claude/rules` copies for the engine and for `agentive-lint rules --write|--check`. Merged mode writes
  no `.claude/rules` copy (no double read) [설계 판단]; rules without `paths:` sit under `### All files`. The size test counts
  AGENTS.md outside the managed regions so the mode does not flip after the region is written [설계 판단]. 미검증: whether Claude
  Code's protected-path check also applies to a `.claude/rules` write made by a Bash script (pointer mode only).

## 15. End-to-end bug fixes (2026-10-29)

Bug fixes from an end-to-end test (a Python uv repo with `[tool.ruff]`), not user decisions. Design:
`spec.init_apply.installed_layer_excluded` (status: bug fix), `spec.enforcement.hooks.post_tool_use.derive`.

- E2E-01 (high). init derived `uv run ruff check .`, which linted the installed layer (`.agents/lib/**/*.py`, hooks, bins):
  100 errors, verify red right after install. [설계 판단] The project's tools never check `.agents`, `.claude`, `.codex`:
  `evidence.OWNED_DIRS` and `evidence.owned_exclude(tool)` add the tool's additive exclude option to a derived repo-wide
  command (ruff and flake8 `--extend-exclude .agents,.claude,.codex`, eslint `--ignore-pattern <dir>/` three times); biome,
  which documents no CLI exclude, is scoped to the manifest `paths.src` + `paths.tests` (`.` when none is known). Additive
  options only: `mypy --exclude` and `black --exclude` replace the configured value, so they are not used. mypy, pyright,
  go `./...`, golangci-lint, tsc, cargo, maven and gradle keep their command (the tool skips dot directories by default, or
  reads its project model); pyright's default exclude `**/.*` was read on a docs mirror and measured locally (pyright
  1.1.414), the official page was not reached: 미검증. A project-owned command (package.json script, Makefile target,
  `--values`) is recorded as given; the init skills tell the agent to give a repo-wide command with the same exclusion.
  `lintscope.select_files` never hands a file under the three directories to the project's linter.
- E2E-04 (high). The PostToolUse lint appended the file to `uv run ruff check .`, so ruff still linted the whole repository.
  `.agents/bin/verb lint -- <file>...` now calls `lintscope.file_command`: a trailing whole-repo target (`.`, `./`, `./...`, a
  manifest src/tests path) is replaced by the files; no such target -> appended; a shell-joined command or flag arguments
  (`-- --fix`) -> appended, command whole [설계 판단]. A package.json `scripts.lint` body that names `.` gets `file_args`
  false (`npm run lint -- <file>` can only append), so it runs once at Stop.
- E2E-02 (low). Subject-verb agreement in the pending-verb sentences: `common.no_command(names)` ("typecheck has no command",
  "lint, typecheck have no command"), used by the init/update report, `settings --effective`, the RV8 reason ("required check
  typecheck has ...", "required checks ... have ...") and the Stop tail.
- E2E-05 (low). `guard.block_message` stripped every trailing period (`rstrip('.')`), so the agentive-path-traversal reason
  ended "through . NEXT". Only one sentence-ending period is dropped now, never a `..`, and the reason reads "through a `..`
  path segment".
- Tests: tests/engine/test_engine.py `test_repo_wide_commands_keep_the_installed_layer_out` (per stack) and the updated lint
  scope cases; tests/hooks/test_hooks.py LintHookTest (`file_command` table, target replaced through the hook, layer files
  skipped); tests/sample_repo/test_init_apply.py `InstalledLayerIsNotLintedTest` (ruff repo: verify green right after init,
  the old `ruff check .` red as the control, the edit lint runs `ruff check ... <file>`; skipped without ruff);
  tests/review/test_stages.py and tests/hooks/test_regressions.py for E2E-02 and E2E-05.

## 16. End-to-end decisions batch A (2026-10-30): E2E-10b, E2E-11, E2E-12, E2E-13

User decisions of 2026-10-30 (E2E-13 by the orchestrator under the user's delegation). Design: `decisions.accepted[AISTEPFAIL]`,
`[WFCHANGE]`, `[CIAUTH].revised`, `decisions.decided[U6].revised`, `spec.work_loop.ci_ai_review_floor.{auth,llm_failure,workflow_change}`,
`spec.work_loop.ci_independence_check.workflow_change`, `spec.review_pipeline.stages[RV7b].rubric`. Regression tests are named after the id:
`test_e2e_10b_*`, `test_e2e_11_*` (tests/review/test_pipeline.py E2EBatchATest, tests/ci/test_workflows.py, tests/engine/test_engine.py
E2EBatchATest, tests/content/test_prbody.py), `test_e2e_12_*` (tests/ci, tests/review/test_lint_eval.py), `test_e2e_13_*`
(tests/review/test_stages.py, tests/engine/test_engine.py), and the "E2E-12 OAuth first / E2E-10b / E2E-11 / E2E-13" block of
tests/design/validate_schemas.py.

- E2E-10b (AISTEPFAIL). `review/llmstate.py` (area D) + the ai-review step `llm-state` right after the LLM step
  (`agentive-review llm-state --work D`, env `AGENTIVE_LLM_OUTCOME` = `steps.llm.outcome`, `AGENTIVE_LLM_CONCLUSION`,
  `AGENTIVE_LLM_EXECUTION_FILE`). It writes `llm-state.json` {tool, outcome, conclusion, started, failed, code} and, on failure, one
  `::error` annotation. Codes: insufficient_credits, auth, rate_limit, timeout, context_too_long, max_turns, no_result, unclassified;
  codex: codex_exit. The summary comment (`post.summary_comment` extras `ai_step_failed`; since COMMENTUX §23 the cause is a Why line
  from `post.AI_STEP_WHY`, the fix half of `llmstate.SENTENCES` with the run link is the What to do line, the code is in Details), the
  gate reason `ai_step_failed` and the gate line `NEXT=ai_step_failed:<code>: <sentence> (run <url>)` carry only the code, fixed
  sentences and the run URL. Rule for
  anyone touching it: the execution-file text is read only inside `classify_claude` and never returned, stored or printed (the tests
  plant a secret-shaped string and grep every output for it). [설계 판단] the substring patterns per code (`llmstate.PATTERNS`, first
  match wins, credits before auth) and `cancelled` counted as a failure.
- E2E-11 (WFCHANGE). `llmstate.workflow_skip` compares the blob of `.github/workflows/agentive-review.yml` at `GITHUB_SHA` (the PR merge
  commit; `HEAD` outside Actions), at `origin/<AGENTIVE_DEFAULT_BRANCH>` (fetched with depth 1 when missing) and at the PR base sha.
  Kinds: `fork` (`AGENTIVE_HEAD_REPO` != `GITHUB_REPOSITORY`; named first, no exemption), `workflow_change` (run blob != base blob),
  `base_differs` (base ref != default branch), `stale_merge` (base ref == default branch: "push a new commit or close and reopen the PR").
  It returns None when Claude ran (`llm-state` started) or a CI review exists (hybrid), on the codex path, and whenever a fact cannot be
  read (fail-safe: the earlier behaviour). Exempt kinds turn the missing CI review of `independence.evaluate(..., ci_skip)` into a note
  (`AGENTIVE_INDEPENDENCE=ok note=ci_review_skipped:<kind>`); the gate re-evaluates a failed independence check with the exemption (the
  job may have run an older script) and adds the reason `ai_review_skipped`, a `::notice::` and the decision's NEXT. Workflow-level env
  `AGENTIVE_DEFAULT_BRANCH`, `AGENTIVE_HEAD_REPO` (data in env only). Report: `AGENTIVE_REPORT github: install PR:` (claude only);
  `prbody.WORKFLOW_CHANGE_LINE` in ## Risk of a PR that adds or changes the workflow. [설계 판단] the independence job fetches the base
  commit and the default branch with depth 1 (its checkout is shallow). `ai_review_skipped` and `ai_step_failed` joined
  `$defs.gate_reason` and `rv8.reason_codes` (`ai_review_missing` stays a free-text reason, §13 / docs/impl/D.md).
- E2E-12 (CIAUTH revised, OAuth token first): docs/IMPLEMENTATION.md §8.
- E2E-13 (U6 revised to agentive:rubric@2). `config.RUBRIC_PRESETS` holds @1 and @2, `RUBRIC_PRESET_ID` / `DEFAULT_RUBRIC` = @2,
  `RUBRIC_CHANGES` the update report text. New RV1 signals `production_modules_touched`, `production_shared_path_touched`,
  `production_churn_commits_90d` (`rv1.production_files`: not a test, not a doc by the RV3 rule, not a lock / generated / vendor /
  exclude_globs path) and RV7b inputs `production_modules_or_shared_path`, `production_churn_90d` (`>=N`, default 5); the @1 inputs are
  unchanged. One history call `gitio.log_first_parent` (`git log --first-parent --name-only`) feeds incident_history (merge commits
  kept: a merged revert PR carries the `Revert "` title), @1 recent_churn_30d and the @2 per-file count of changes landed on the base
  [설계 판단: incident_history and @1 churn now read the first-parent history too; a revert commit inside a branch merged with a merge
  commit is seen only through the merge message]. The @2 count (revised 2026-10-31 [user] "1번으로 확정해") takes every first-parent
  record, merge commits included: a merged PR, a squash commit or a direct commit counts 1, a rebase-merged PR one per commit; no flag
  is added to the call, because `--first-parent` makes a merge commit list the files of its PR (tests `test_e2e_13_churn_*` pin this
  on the CI git) [설계 판단: incident_history is unchanged by the revision; the generic `Merge pull request #N from <branch>` subject
  alone is no incident, squash and direct commits are matched by their own message]. An override with `extends` naming another framework preset is merged onto that preset (a pin;
  `extends` alone is then valid); `/agentive:update` (`apply.migrate_rubric`) moves an override's `extends` line of the old preset
  together with the preset and prints `AGENTIVE_REPORT rubric:` lines. Bug fixed on the way [설계 판단]: the engine merged
  `.agents/custom/.agents/rubric.yaml` into `.agents/rubric.yaml` at install time, which put `extends` into the preset file and made
  every tier 1 PR `rubric_invalid`; the rubric override is now applied only at run time (like guard.yaml, it is excluded from the
  install-time overlay). 미검증: the calibration of 500 lines, 5 landed changes and 30 points (no data; shadow labels only); the git
  version from which `--first-parent` turns the merge diff to first-parent by default (seen on git 2.43); on a merge-commit repository
  the landed-change count is less sensitive than a commit count (one PR = 1).

## 17. End-to-end decisions batch B (2026-10-30): E2E-06, E2E-08, E2E-09

User decisions of 2026-10-30. Design: `decisions.accepted[BASEREF]`, `[VERIFYTREE]`, `[REGIONNOSHA]` (and the `revised` notes on
`[MR]`, `[VERIFY]`, `spec.verification.E1c`, `spec.stop_hook.fingerprint`), `spec.work_loop.pr_skill.base_ref`,
`spec.spec_stage.chain.start_check`, `spec.verb_wrapper.staged`, `spec.init_apply.managed_regions`. Regression tests are named after
the id: `test_e2e_06_*` (tests/content/test_prbody.py E2E06BaseRef, tests/content/test_stage_check.py; fixture `helpers.DepthSandbox`:
an empty bare origin cloned with `--depth 1 file://`), `test_e2e_08_*` (tests/hooks/test_hooks.py ContentTreeTest, VerifyStagedTest,
StopFingerprintTest; tests/review/test_regressions.py; tests/content/test_prbody.py), `test_e2e_09_*` (tests/engine/test_engine.py
RegionTest, E2E09RegionTest; the E2E-13 idempotence test now commits in between), and the "E2E-06 / E2E-08 / E2E-09" block of
tests/design/validate_schemas.py.

- E2E-06 (BASEREF). `gitgh.default_branch`: origin/HEAD only when `git rev-parse --verify` of its target succeeds, else
  `gitgh.remote_head` (`ls-remote --symref`); no gh api. `gitgh.base_ref(root, fetch)` fetches
  `+refs/heads/<d>:refs/remotes/origin/<d>` once (GIT_TERMINAL_PROMPT=0, `--no-tags`), names the branch again and refetches only when
  git says `couldn't find remote ref` [설계 판단: other fetch failures are taken as offline], runs `git remote set-head origin <d>`
  only when origin/HEAD is missing or names another branch, and returns NOTE lines plus at most one stop line. `prbody.Repo(root,
  fetch)` adds `no_merge_base` (shallow flag from `rev-parse --is-shallow-repository`) and `no_commits_ahead` (`rev-list --count`);
  an empty net diff with commits ahead stays `TIER=uncertain` with the reason `empty diff: the <n> commit(s) ahead of origin/<d>
  change nothing in total`. `Repo.rules()` appends a NOTE when it falls back to the work tree's code_review.md. `cmd_publish` uses
  `Repo(root, fetch=False)`. [설계 판단] the reason name `no_default_branch` is kept (its NEXT changed); NOTE lines go to stdout before
  the final line. chain-stage-check.sh does the same in sh (NOTE on stdout, the new stop NEXTs).
- E2E-08 (VERIFYTREE). `hookio.content_tree(root)`: scratch index in the git dir (`shutil.copy2` of `git rev-parse --git-path
  index`, so the mtime and with it git's racy-clean check stay; `read-tree HEAD` or `--empty` when there is no index), `git add -A`
  of what `git ls-files -z --others --exclude-standard --modified --deleted -- . ':(exclude).agents/state'` lists (a plain
  `git add -A -- . ':(exclude).agents/state'` fails when .agents/state is ignored), `git write-tree`; `hookio.index_tree` is
  `git write-tree` of the real index. `verb.fresh_for(record, tree, manifest)`: content_tree equal and, when the manifest is known,
  `{verb: cmd}` of the record equal to `verb.planned_commands(manifest)` [설계 판단: without a manifest only the tree is compared]. The
  Stop hook, `agentive-review precommit` (`verify_fresh`) and `prbody.verify_state` call it with the work content tree, the index
  tree and `HEAD^{tree}`. The PR `## Verification` adds `Tree: <content_tree>`. `verb verify --staged` (`verb._verify_staged`):
  index tree and work content tree, `git diff --name-only` between them = the paths to set aside; a backup commit of the work tree
  in `refs/agentive/verify-staged` (`commit-tree` + `update-ref`) whose recovery command the NOTE line prints; `_set_paths`
  (`git --literal-pathspecs restore --source=<tree> --worktree --pathspec-from-file`, removal for paths the tree lacks; the index
  is never touched) to the index tree, `_verify(tree=index)`, then in `finally` back to the work tree, checked with a new
  content tree over those paths; the backup commit is made with a fixed internal identity and no signing (§28), the backup ref is deleted only after that check, otherwise an `AGENTIVE_FAIL ... NEXT=git restore
  --source=refs/agentive/verify-staged ...` line. SIGTERM is turned into KeyboardInterrupt for the run so `finally` restores; a
  SIGKILL leaves the backup ref. [설계 판단] a record without content_tree (older wrapper) is never fresh; the precommit NEXT names
  `verify --staged` when the work content tree differs from the index tree; the /pr stale NEXT always names it (with a clean
  tracked tree it verifies exactly HEAD's content and sets untracked files aside). `hookio.worktree_fingerprint` and
  `unstaged_fingerprint` are removed; verify-result drops `worktree_fingerprint`. 미검증: cost of the scratch-index `git add -A` in
  a large repository; git restore needs git >= 2.23.
- E2E-09 (REGIONNOSHA). `regions.provenance(style)` is the constant notice (`regions.NOTICE`); `PROV_RE` (and `rulesync._PROV`)
  still matches the older dated line so it stays outside the hash. `apply_regions` writes a region only when its body changes,
  or once when its first line is not the constant notice [설계 판단]; region records are {kind, sha256, framework_version
  (, overrides)}, and verified_at / verified_commit of an older manifest are dropped on load. `stale_check` is sha-free and absorbs
  the former `pointer_stale`: `named_paths(body)` = doc-pointer entries, `Tests:` entries and tokens that hold a `/` or end with a
  file extension (backticks, brackets, a leading `@` and sentence punctuation stripped; URLs, absolute paths and version numbers
  skipped) [설계 판단]; a name still in the work tree is skipped; otherwise `path_history` runs `git log -1 -M --full-diff
  --diff-filter=DR --name-status -- <name>` and maps a directory rename by its suffix; a doc pointer without such a commit is
  reported with commit null. `stale_check` = {done, since: the previous drift report's until, until: HEAD} or skipped with
  `no_commits` / `not_a_git_repository`. `--today` is accepted and unused. 미검증: a path-scoped `git log -1` for a name that never
  existed walks the whole history (one call per missing name).

## 18. End-to-end decisions batch C (2026-10-31): E2E-14, E2E-15, E2E-16, E2E-17, E2E-18

E2E-14 and E2E-15 are user decisions ("결정 필요 없는건 그대로 확정하고"); E2E-16, E2E-17 and E2E-18 were decided by the
orchestrator under the user's delegation after a critical review of the recommendation research. Design:
`decisions.accepted[HUMANMERGE]`, `[RESTONLY]`, `[NOBASELINE]`, `[PRECOMMITINLINE]`, `[PLANMODEGATE]` (and `revised` on
`[EVALOVERRIDE]`, `amended` on `[INDEPCI]`, `plan_gate_amended` on `[CHAIN]`), `spec.work_loop.pr_skill.human_merge`,
`spec.work_loop.pr_feedback.human_merge`, `spec.outer_loop.ci_logs`, `spec.review_pipeline.reviewer_eval.ci.no_baseline`,
`spec.work_loop.precommit_review.inline`, `spec.repo_layer.files[f-precommit].frontmatter`,
`spec.spec_stage.chain.flow[plan].plan_mode_gate`, `spec.enforcement.hooks.pre_tool_use_guard.chain_plan_gate`,
`spec.no_human_run.gap`, UV-12 (amended), UV-41, UV-42; sources S198-S202 (S20, S24, S76, S170 re-read). Regression tests are
named after the id: `test_e2e_14_*` (tests/content/test_prbody.py Publish, tests/content/test_pr_feedback.py,
tests/engine/test_engine.py E2EBatchATest), `test_e2e_15_*` (tests/content/test_skills.py Wording), `test_e2e_16_*`
(tests/review/test_stages.py EvalScoringTest, tests/review/test_lint_eval.py, tests/ci/test_workflows.py), `test_e2e_17_*`
(tests/review/test_attest.py, tests/content/test_skills.py), `test_e2e_18_*` (tests/hooks/test_hooks.py ChainPlanGateTest,
tests/content/test_skills.py), and the "E2E-14 / ... / E2E-18" block of tests/design/validate_schemas.py.

- E2E-14 (HUMANMERGE). `prbody.human_merge(repo)` re-runs `predict_tier` on the local refs at publish (publish does not fetch):
  tier 2, or `changes_review_workflow`, is a human merge; `uncertain` is not [설계 판단: CI decides an uncertain tier].
  `prbody.merge_lines` prints `MERGE=human pr=<n> url=<u> why=<codes>; NEXT=...` (the codes are the reason prefixes:
  trust_boundary, check_config, chain_approval, U2.non_interactive, workflow_change), then the last line: with a human present
  the `next: /pr-feedback <n> ...` line with "(fixes only; the merge stays with the human)", with no human present
  (`gitgh.no_human`) `AGENTIVE_PR=done pr=<n> waiting=human_merge url=<u>; NEXT=report the PR URL and end the run ...`. The
  refresh path reads `html_url` from the `pulls/<n>` call it already makes. `prstate.waits_for_human_merge(state)`: gate decision
  human_required, no open finding, no failing check, no human thread without a reply [설계 판단: a thread the agent answered
  waits for the same human] -> `PR_STATE=wait reason=human_merge pr=<n> url=<u> file=<f>; NEXT=...` exit 0 (explainer_missing
  wins: /pr --explain first). The state file gains `url`. `apply.py` init (not update) writes the work-branch, /pr and human-merge
  wording into `AGENTIVE_NEXT`. The optional guard rule for `gh api -X PUT .../pulls/<n>/merge` is not adopted.
- E2E-15 (RESTONLY). Design text only plus tests: `outer_loop.ci_logs` names check-runs and job logs; the OUTER rule says the
  same. `test_e2e_15_*` scan every text file of src/repo-layer and src/engine: no NEXT / AGENTIVE_REPORT / AGENTIVE_NEXT line and
  no AGENTS.md, CLAUDE.md or code_review.md region template may name `gh pr <x>`, `gh issue <x>` or GraphQL; `gh api graphql`
  appears only in /pr-feedback's resolve step (with "only reply and report \"resolve pending\""); the only `["pr", "merge"`
  argv in code is `review/github.py enable_auto_merge` (CI gate job, GitHub runner). No AGENTS.md line was added [설계 판단].
- E2E-16 (NOBASELINE). `evals.has_baseline(repo, base)` = `git cat-file -e <base>:.agents/bin/agentive-review`
  (`evals.BASELINE_BIN`) [설계 판단: the program the baseline run executes; REVIEWER_GLOBS would count .claude/agents/ or REVIEW.md
  from before agentive]. `agentive-reviewer-eval detect` writes `baseline=none|present` (only on a reviewer change) and appends
  `; baseline: none (first install: ...)`; `score --no-baseline`; `evals.summarize(..., no_baseline=True)` -> reason no_baseline,
  adopt_suggested true, ordered after not_run; `evals.comment` leaves out both tables for no_baseline and writes the first-install
  sentence and a `NEXT:` line; the insufficient line now names the missing cases (`evals.CASES_HELP`). reviewer-eval.yml: the CLI
  install and the run step add `steps.detect.outputs.baseline != 'none'`; the score step gets the output through env `BASELINE`
  and passes `--no-baseline`. Schema: `$defs.summary.reason` gains no_baseline. 미검증: none beyond the design entry (a user
  who adds cases in the install PR gets no run).
- E2E-17 (PRECOMMITINLINE). `skills/precommit-review/SKILL.md` has no `context` / `background`; the body pins the orchestrator
  role, the foreground subagent (`run_in_background: false`), the fixed prompt and the self fallback
  `{"executed_by": "self", "reason": "no_agent_tool" | "spawn_failed"}`. `attest.local_fields` copies a known reason into
  `self_reason` (`attest.SELF_REASONS`); `pipeline.precommit_record` and `prbody.review_block` carry it;
  `$defs.review_self_reason` in review-config.schema.json, referenced by precommit-review `reviewer`, review-report
  `rv5.reviewers[]` and pr-body `reviews[]`. `agentive-review precommit` prints `NOTE independence=self reason=<r|unstated>: ...`
  before its last line when the record says self [설계 판단: `unstated` when the meta names no reason]. No SessionStart notice.
  미검증: UV-10 (Codex), UV-42 (the standard command without an Agent allow entry; observed working on CLI 2.1.316 only).
- E2E-18 (PLANMODEGATE). `gates.chain_plan_gate(command, hook_input, tool, no_human)` is called first in
  `hooks/pre-tool-use-guard.py` (before guard.yaml is loaded, so it does not depend on it): Claude Code only, `permission_mode`
  present and not plan, and a simple command (`guard._walk`: env assignments, wrappers and one `sh -c` level stripped; shell
  options skipped) whose script path is `.agents/skills/chain-plan/scripts/stage-check.sh` or ends with `/` + that path ->
  deny with `AGENTIVE_CHAIN=stop:plan:<slug> NEXT=...` (slug = the next argument, `none` without one, cut at 80 characters); the
  no-human wording when `nohuman.no_human(hook_input=...)` holds. chain-stage-check.sh keeps its env rule (comment only).
  chain-plan SKILL.md: step 1 handles the deny line (EnterPlanMode, rerun, else stop); step 3 pins approval = the plan-mode
  prompt's accept choice, the stop line without ExitPlanMode and "Never write plan.md through Bash". 미검증: UV-41 (the interactive
  deny -> EnterPlanMode -> approval flow), Codex hook `permission_mode`; residual risk recorded in the design: `claude -p
  --permission-mode plan` still runs allow-listed Bash.

## 19. End-to-end decisions batch D (2026-10-31): E2E-19, E2E-21, E2E-22, E2E-23, E2E-24

E2E-19 was decided by the orchestrator under the user's delegation after a critical review of the US / China / Japan practice
research; E2E-21..24 needed no user decision (presented to the user 2026-10-31). E2E-20 was decided later by the user as
DEPHUMAN (§20); no fallback scanner was built. Design:
`decisions.accepted[EFFECTIVECHECKS]`, `[LOCKFILEDEPS]`, `[REQUESTLINE]`, `[REVIEWRECORD]`, `revised` on `[PENDINGNOTICE]`,
`spec.review_pipeline.stages[RV8].rules`, `[RV2].protected_check_config.paths` and `always_human`, `spec.init_apply.report`,
`spec.work_loop.pr_skill.request_line` / `.review_record`. Regression tests: tests/review/test_e2e_batch_d.py (`test_e2e_19_*`,
`test_e2e_21_*`, `test_e2e_23_*`, `test_e2e_24_*`), tests/content/test_prbody.py (`test_e2e_22_*`, `test_e2e_23_*`),
tests/engine/test_engine.py (`test_e2e_19_*`, `test_e2e_21_*`), tests/hooks/test_hooks.py (`test_e2e_19_*`) and the
"E2E-19 / ... / E2E-24" block of tests/design/validate_schemas.py.

- E2E-19 (EFFECTIVECHECKS). `review/config.required_state(require, pending, explicit)` -> {required, not_required,
  pending_required, floor, source}; `load_project_config` sets `Loaded.require_explicit` when `.agents/framework.yaml` names
  `stages.RV1.require_checks` (an invalid file counts as not written); `required_state_of(manifest, loaded)` and
  `pending_required(manifest, loaded)` (now: only the named pending verbs) are what the engine report, `settings --effective` and
  the pipeline use. `rv8.decide` takes `require_explicit` (the pipeline passes it; a caller-given `required_checks` counts as
  explicit [설계 판단]), writes `rv8.required_checks` and adds `no_required_check` (floor, `config.floor_text`) or `pending_verbs`
  (text now says "you required it in .agents/framework.yaml"). The floor is not added when `pending_verbs` already covers the same
  verbs [설계 판단: one reason]. A verb is "without a command" exactly when the manifest marks it pending (a verb missing from the
  manifest, or `ci`, is not pending, as before) [설계 판단]. Info line `- not required: <verbs> (no command in this repo; info, not
  blocking)` and `- required checks: <verb> (<status>) ... [<source>]` (`post.required_lines`) in the gate job log; since COMMENTUX
  (§23) the summary comment and the step summary show them as one `**Checks:**` line (`post.checks_line`). Engine: `AGENTIVE_REPORT required-checks: ...` (`config.required_report`) on every init/update; `auto-merge unavailable`
  lines only for the floor or a named pending verb. Stop hook tail: "until then the check is not required, unless ... names it".
  init/update skills (Claude Code and Codex): repeat the required-checks line, ask once only on `auto-merge unavailable`.
  `prstate.GATE_REASONS` gains no_required_check. Not adopted: the stateful regression rule (design rule 7).
- E2E-24 (in EFFECTIVECHECKS rule 6). `rv8.decide`: for tier 1 with a score above the threshold, `score_above_threshold` is added
  when another reason already stopped auto-merge (`elif` of the step-5 block; the decision is unchanged). `post.reason_lines`
  turns `code` + `code: detail` pairs into one line per reason (a "human review needed ..." detail stands alone, else
  `code: detail`, else the bare code; duplicates dropped) [설계 판단: wording]; the gate job log lists them. Since COMMENTUX (§23)
  the summary comment shows them as grouped plain sentences under `**Why**` (`post.why_items`), each reason once.
- E2E-21 (LOCKFILEDEPS). `engine/evidence._verb` drops lockfiles (`evidence.LOCKFILES`, the same names as `review/rv1.LOCKFILES`)
  from lint, typecheck and format evidence (setup and test keep them; consequence: a lockfile change no longer marks the lint
  slot dirty in FileChanged). `rv2.evidence_config_files` also skips lockfiles and package files for manifests written earlier.
  `rv1.PACKAGE_FILES` gains Pipfile (`[packages]`, `[dev-packages]`) and setup.py (quoted names in install_requires,
  extras_require (keys dropped), tests_require; no evaluation) [설계 판단], so they are RV1 dependency sources, RV3 class deps and
  RV2 `critical_cve_unchecked` inputs (since §20: `dependency_change` inputs); requirements*.txt was already one. 미검증: none
  beyond the design entry.
- E2E-22 (REQUESTLINE). `prbody.request_line(chain, request, hotfix_of)` is the first line of `## Risk`: `--request` text
  (whitespace collapsed), else the chain link, else `hotfix of PR #<n>`, else `prbody.REQUEST_MARKER` (an agent marker, so `check`
  fails until it is written). No presence check [설계 판단]. /pr SKILL.md step 3 adds `--request`, step 4 says how to fill it.
- E2E-23 (REVIEWRECORD). `prbody.wrap_review_record` puts the fence under `## agentive review record` inside
  `<details><summary>Machine-read review provenance (CI reads and updates it; do not edit)</summary>` with blank lines around the
  fence. No parser changed; tests pin `pr_body.block`, `util.fenced_blocks` (independence exemption and evaluate), `prstate.
  review_block`, `prbody.block_problems` / `check_body`, `pr_body.with_pipeline` (the wrapper survives) and the old unwrapped form.

## 20. E2E-20 DEPHUMAN (user decision 2026-10-31, "추천안으로 확정")

Design: `decisions.accepted[DEPHUMAN]` (supersedes the OSV fallback decision of E2E-20; `revised` on `[LOCKFILEDEPS]`),
`spec.review_pipeline.stages[RV1].signals`, `[RV2].always_human`, `[RV3].package_files`, `[RV4].tier0_classes_rule`,
`[RV7b].change_type_note`, `spec.repo_checks.dependency_review`, `spec.repo_layer.files[f-ci]`, `spec.maintenance.supply_chain.packages`,
`spec.init_apply.report`; sources S203-S208; schema review-report (`rv1.signals.dependency_changes`, gate_reason `dependency_change`).
Regression tests: tests/review/test_e2e_20.py (`test_e2e_20_*`), tests/ci/test_workflows.py and tests/engine/test_engine.py
(`test_e2e_20_*`), the updated `test_e2e_21_*` of tests/review/test_e2e_batch_d.py and the "E2E-20 DEPHUMAN" block of
tests/design/validate_schemas.py.

- RV1 (`review/rv1.py`). Every `_deps_*` parser now returns `{key: spec}` (or None for an unreadable file) from the dependency
  entries only; key `<ecosystem>:<name>` for a dependency and `<ecosystem>-<kind>:<name>` for an entry that changes where or which
  version is resolved (npm-override, pypi-option, pypi-url, go-replace, cargo-patch, maven-parent, maven-plugin,
  maven-properties, gradle-plugin); spec = group + version text without whitespace, so a moved or re-pinned entry is `changed`
  [설계 판단: kinds and spec format]. pyproject.toml, Cargo.toml and Pipfile go through a small TOML reader (`_toml_entries`,
  `_toml_scan`: table headers, dotted and quoted keys, arrays and inline tables across lines, strings and comments; no
  tomllib; the layer's floor is python 3.13, which has tomllib, but the switch is a separate change) [설계 판단]; pyproject keys as in the design rule (F5 fixed:
  `[tool.ruff] extend-select = ["B"]` is no dependency; `{include-group = ...}` in `[dependency-groups]` is skipped).
  `dependency_changes(diff, statuses)` -> `{added, removed, changed, lockfiles, unreadable, package_files}` (+ private
  `_direct_added`); a lockfile counts unless it is a pure rename with equal content; a package file whose base and head texts are
  equal is skipped. `signals.dependency_changes` is new; `new_direct_dependencies` = the added dependency keys of the five
  direct ecosystems, go `// indirect` left out. requirements*.txt lines: option lines and URL or path lines are entries; a
  comment needs `#` at the start or after whitespace (pip's rule) [설계 판단]. constraints*.txt now has the requirements parser.
- RV2 (`review/rv2.py`). `dependency_change` (detail `rv1.dependency_change_text`) is added when `rv1.is_dependency_change`
  holds, in every profile (precommit ignores always_human items; reviewer eval and CI get tier 2). In CI the detail ends with
  the dependency-review state as information (`DEP_REVIEW_TEXT`: pass / fail / could not run / did not run, plain words, section 24); `critical_cve_unchecked` is no longer
  written (kept in the gate_reason enum for earlier reports). A dependency-review `fail` keeps its always_human line and, as
  before, the failing check (block) [설계 판단]. When the caller passes no `dependency_changes` signal, RV2 computes it.
- RV3 (`review/rv3.py`). `classify_rules(..., dep_files=None)`: a package file is `deps` only when it is in
  `dependency_changes.package_files`, else `config`; the pipeline passes the RV1 signal. `_manifest_class` stays a static
  "is a manifest" check (docs exclusion).
- RV8 and the gate. `rv8.decide` forwards `dependency_change` with its detail line (the summary comment shows it once: since
  COMMENTUX as the two Why lines `Dependencies changed: ...` and `Dependency review: ...`, the detail itself in Details); `critical_cve_unchecked` left the forwarded list. `agentive-review gate` prints
  `NEXT=` `rv1.DEPENDENCY_NEXT` for a human_required decision with that code.
- CI (`dot-github/workflows/agentive-review.yml`). `actions/dependency-review-action@a1d282b36b6f3519aa1f3fc636f609c47dddb294`
  (v5.0.0, node24; outputs read from action.yml at that commit, S203); the status step prints a `::notice::` when the action
  did not complete [설계 판단: notice, not warning: a repository without the dependency graph would see it on every PR]. The other
  actions keep their tags (the decision names the scanner only).
- Engine (`engine/apply.py`). `dependency_report(labels)` -> `AGENTIVE_REPORT dependencies: ...` on init and update: the rule
  and the cooldown setting of the detected ecosystem (npm `min-release-age` in .npmrc, pnpm `minimumReleaseAge` in
  pnpm-workspace.yaml, uv `exclude-newer = "7 days"` under `[tool.uv]`; all three when none matches) [설계 판단: wording and
  the 7-day example]; nothing is written. The GitHub Free line now says dependency review is information only.
- Not built: the opt-in conditional auto-merge mode (`review.dependencies.auto_merge`), the OSV fallback, an Explainer
  `### Dependencies` check and an AGENTS.md dependency line (the decision text names none of them).
- 미검증: Gemfile, composer.json and Gradle version catalogs are not read as dependency lists (their lockfiles are); a
  dependency list built in code (setup.py) is not seen; the npm and pnpm versions that ship the settings.

## 21. End-to-end recheck (2026-10-31): E2E-25, E2E-26, E2E-27 (wording and display only)

No decision changed. Design text: `spec.review_pipeline.reviewer_eval.scoring.insufficient` and `.ci.merge` (E2E-25),
`spec.work_loop.ci_ai_review_floor.workflow_change.effect` (E2E-26), `spec.init_apply.report` (E2E-27). Regression tests:
tests/review/test_e2e_batch_e.py (`test_e2e_25_*`, `test_e2e_26_*`), tests/engine/test_engine.py (`test_e2e_27_*`), the updated
`test_e2e_16_order_and_the_kept_override` of tests/review/test_stages.py.

- E2E-25 (EVALOVERRIDE unchanged). Fact checked in the code: the PR-body line `eval-override: <reason>` is read by nothing
  (`grep eval-override` finds only `review/evals.py` comment text; RV8, the gate, independence and `pr_body` never parse it, as
  `decisions.accepted[EVALOVERRIDE].line` says), and the reviewer-eval job is `continue-on-error` and no gate input. So the line
  stays required as the human's merge rule, and the text now says what it is. `evals.comment`, reason insufficient_cases: the
  first line says why nothing was compared ("evals/reviewer/ has no case yet, so nothing was scored" | "a positive and a negative
  case are both needed, so the result is not compared"), then `Review this reviewer change by hand. <evals.HUMAN_OVERRIDE>` and, for the case problem, `<CASES_HELP>`; the closing `adopt_suggested: false — merge only with ...` line gains `(<evals.OVERRIDE_FACT>)` (no check reads
  the line, the gate does not wait for it) for every false reason. `evals.summary_line(repo, base, head)`: a reviewer change
  (reviewer_paths) with a baseline (E2E-16 rule) and a case set without both labels -> one `- reviewer eval: ...` line in the
  agentive-review-summary comment (`agentive-review ci --post`, `extras["reviewer_eval"]`); no line for no_baseline (that comment
  needs no override) or a scorable set (the score exists only in the reviewer-eval job) [설계 판단]. Case files are read from the
  ai-review checkout (the PR head; display only). evals/reviewer/README.md says the same.
- E2E-26 (WFCHANGE unchanged). `post.skipped_view(reasons, problems, skip, rv4_reasons)`: with a skip, the problems
  `<task>: not run | no output` (`post.NOT_RUN`) leave the `llm_raise` reasons and the notes, a bare `llm_raise` goes too unless
  RV4 raised it, and `ai_review_skipped` + `ai_review_skipped: <kind>: <detail>` is added once (the gate's reason, E2E-11 detail
  sentence) [설계 판단: wording]. Used by `post.summary_comment` (skip from `extras["ai_review_skipped"]`, set by `agentive-review
  ci --post` through the gate's `_skip`) and by the gate's step-summary lines; the recorded `rv8.reasons` are unchanged. Without a
  skip a problem already shown as an `llm_raise:` reason is no longer repeated as a `- note:`. The ai-review job checks out the PR
  head, so `llmstate.workflow_skip` fetches a missing 40-hex `run_rev` (GITHUB_SHA, the merge commit) once by sha, like
  actions/checkout; a failed fetch keeps None (fail-safe, the old display) [설계 판단]. Detection still never uses the head.
- E2E-27. The rerun's NEXT was not printed without lines: `AGENTIVE_NEXT` already named them only when `pending` was non-empty,
  but the lines came out early (right after the slot proofs, about 40 lines above NEXT), so they were easy to miss. Now
  `write_pending` keeps the files (`Engine.not_effective`) and `report()` prints the `AGENTIVE_NOT_EFFECTIVE` lines right above
  `AGENTIVE_NEXT`, whose part names them with count and `path: reason` only when there is at least one [설계 판단: wording, order].

## 22. Explainer visual spec and local tier estimate (2026-10-31): explainer_visual, E2E-28 to E2E-31

User decisions 2026-10-31 (`explainer_visual`: "After는 색이 너무 쨍해서 ... 추천안으로 해주고"; `e2e_28_31`: "추천안으로 해주고").
Design: `spec.work_loop.pr_explainer.revised_visual`, `.contents`, `.visual`, `.when_written`, `.enforcement.local_style_check`,
`.review`; `decisions.accepted[EXPLAIN].revised_visual` (rule text revised, `revised_at` stays 2026-10-27), new
`decisions.accepted[LOCALTIER]`, `[DEPHUMAN]` rule (2) wording, `spec.review_pipeline.reviewer_eval.ci.insufficient`, RV5
correctness-reviewer scope, `spec.repo_layer.files[f-pr]`, `view.explainer` + `layout.explainer`, sources S209-S215; schema
pr-body-agentive-review `x-agentive-body-headings.tier2_explainer.local_style_check`. Regression tests: tests/content/test_prbody.py
(`ExplainerVisual.test_explainer_visual_*`, `Flow.test_e2e_29_*`, `Flow.test_e2e_30_*`), tests/review/test_e2e_batch_f.py
(`test_e2e_28_*`, `test_e2e_31_*`), the updated wording tests in tests/review/test_e2e_20.py and test_e2e_batch_e.py, and the
"EXPLAIN visual revision / LOCALTIER" block of tests/design/validate_schemas.py.

- Explainer visual (prbody.py). `LEGEND`, `CLASSDEFS`, `TAGS`, `SUMMARY_HEAD` are the fixed words; `explainer_section` writes the
  skeleton (summary line marker, bullet marker, `- Tier 2 (predicted): ...`, Before without classDef, After with the three
  classDef lines, the legend). `parse_flowchart` reads the one-statement-per-line profile (node declarations with the usual
  shapes and `:::cls`, `class a,b cls`, arrows `-->` `==>` `-.->` `---` with `|label|`, chains and `&` split, `accTitle`,
  `accDescr {...}`, `%%` comments, `subgraph`/`end` skipped; frontmatter, `%%{init`, `style`, `linkStyle`, `fill:`, `color:`
  recorded as styled). `explainer_style_problems(body)` -> `[(code, message)]`, one message per code and diagram (nodes
  listed, `_few` caps at 3) [설계 판단: aggregation]. Codes = schema `local_style_check.problems` (13; `too_many_arrows` is the
  arrow half of 5.2's too_many_nodes [설계 판단]). Path detection: a slash path with an extension, a file name with a known code
  extension, `#` or `:<digits>`; a bare slash (`POST /login`) and a dotted module name (`shop.auth`) pass [설계 판단]. Entry rule:
  nodes without an incoming arrow (orphans excluded) must be at least 1 and at most max(1, subgraphs) [설계 판단: per-diagram,
  not per-subgraph]. Edge labels are not length-checked. `style_lines` skips the checks while an agent marker is left in the
  section; `check_body` and `check --explain-only` add them only when the presence rules hold; the last line's NEXT is
  `STYLE_NEXT` when every problem is a style problem. CI (`review/pr_body.explainer`, RV8) is unchanged: presence only.
- Explainer template and guidance: `skills/pr/templates/explainer-example.md` (a filled English example, the research's PR #6
  rewrite; rendered with mmdc 11.16.0 `-t default` and `-t dark`), SKILL.md step 4 (the rules in four sub-bullets, the example
  path, step 5 says the style lines are local only), `agents/correctness-reviewer.md` (every After arrow must exist in the head
  tree, every tag supported by the diff). Map page: `view.explainer` redrawn by the same rules (shared node positions in Before
  and After, 403 kept as REMOVED, chips NEW / CHANGED / REMOVED, new arrow kinds `exnew` (thick) and `exrem` (dotted, muted),
  `summary` key shown above the bullets and in the source block); tpl.html token `--ex-accent:#0969da` in both themes.
  미검증: GitHub's dark mode uses Mermaid's `dark` theme (community report only), GitHub exposes accTitle/accDescr, node
  declaration order fixes Mermaid's layout.
- E2E-29 / E2E-30 (LOCALTIER). `review/pipeline.boundary` (RV2) and `pipeline.classify` (RV3 rules) are what `run()` calls;
  `pipeline.rules_estimate(repo, base, head, policy, u2, rules_l)` runs rv1.run (no checks, no history, base tree for criteria),
  boundary (pr=None), classify, `rv3.finish(..., llm_layer False)` and `rv4.rules_tier`; undecided files make a tier below 2
  `uncertain`. `prbody.predict_tier` calls it with `Repo.policy` (origin/<default>) and the rules from `Repo.rules_text()` (base,
  else the work tree with the old NOTE) and turns the result into display reasons: `trust_boundary:<reason>:<path>`,
  `check_config:<path>[#section]`, `chain_approval`, `<code>: <detail>` for the other always_human items (the
  protected_check_config line is dropped: the paths are named), `U2.non_interactive`, `large_change`, `tier2_class` only without
  an RV2 hit [설계 판단: display]; the branch-name chain stage stays an extra local raise; a git error gives `uncertain` with the
  reason. Removed from prbody: its own copy of the rules (LOCKFILES, REGIONS, region_body, the rv3 private calls). Remaining
  mismatch with CI (reported, by design): files undecided by the RV3 rules (CI's LLM classifier), the RV4 raise-only check, the
  PR author, labels and check results (dependency-review state), base history signals; rv1 now excludes lockfiles from the
  line count locally too, as in CI.
- E2E-31. `rv1.dependency_change_text`: `human review needed: dependency change (<what>), never auto-merged` + in CI
  `; dependency review: <state> (information only)` + `.`; the "no result" cause is the job's existing `::notice::`.
  `post.reason_items` -> `[(code, line)]` (reason_lines unchanged), `post.tier_reasons(rv4_reasons, listed)` (kept as a helper; since
  COMMENTUX the summary comment has no tier line: the tier and the codes are in Details).
- E2E-28. `evals.summary_line`: since COMMENTUX one clause after `**Reviewer eval:**`: `not scored (<why>); merging needs
  `eval-override: <reason>` in the PR body; details in the reviewer-eval comment.` (shortened by E2E-38, §23)
  The reviewer-eval comment itself was rewritten by COMMENTUX (§23).

## 23. Comment readability and E2E-32 to E2E-38 (2026-11-01): COMMENTUX, HEADLESSHINT, fixes

COMMENTUX is a user decision ("가독성이 하나도 없어서 ... / 추천대로 해"); E2E-32 to E2E-36 were decided by the orchestrator under the
user's instruction after the root-cause and the practice research (HEADLESSHINT is the one decision; E2E-33..36 are fixes recorded
in the spec). Design: `decisions.accepted[COMMENTUX]`, `[HEADLESSHINT]`, `spec.context_management.headless.{standard_cmd, bash_hint,
sandbox_cache}`, `spec.enforcement.hooks.pre_tool_use_context_gate.bash_hint`, `spec.verb_wrapper.scratch_caches`,
`spec.guard.push_without_refspec`, `spec.init_apply.proof.paths_tracked`, the RV2 always_human env-var entry, the RV8 effective-checks
rule (E2E-34 display), `spec.review_pipeline.reviewer_eval.ci.*`, UV-43; sources S216-S227; schema review-report
(`rv8.required_checks.display`). Regression tests: tests/review/test_commentux.py (`test_commentux_*`, `test_e2e_34_*`,
`test_e2e_35_*`), tests/hooks/test_e2e_batch_g.py (`test_e2e_32_*`, `test_e2e_36_*`), tests/content/test_e2e_batch_g.py,
tests/engine/test_regressions.py and tests/sample_repo/test_prove.py (`test_e2e_33_*`), the updated wording tests of
tests/review/test_e2e_batch_{d,e,f}.py, test_stages.py, test_pipeline.py, and the "COMMENTUX / HEADLESSHINT / E2E-33..36" block of
tests/design/validate_schemas.py.

- COMMENTUX (review/post.py, review/evals.py, `agentive-review gate`). `post.why_items(report, extras, reasons)` turns the rv8 reasons
  (skipped_view applied) plus the rv4 always_human details, rv2 hits and confirm-scope hits, rv1 diff_stats and env names and the
  extras (`ai_review_skipped`, `ai_step_failed`) into `[{group, text, codes, block}]` in reading order (block reasons, AI review G5,
  sensitive paths G2, confirmation G3, dependencies G6, env vars G1, size G4, the other always_human items G12, author G11,
  findings G8, checks G7, policy G10; an unknown code shows its detail as is) [설계 판단: order and every sentence]. `post.verdict`
  is the decision table (icon key, words, What to do); `checks_line`, `findings_line`, `detail_lines` build the rest;
  `summary_comment` = marker, `### <icon> agentive review: <verdict>`, `**What to do:**`, `**Why**` (only when the decision is not
  pass; more than 5 -> 4 and `+ k more in Details`, the rest as `Also:` lines), `**Checks:**`, `**AI findings:**`, `**Reviewer
  eval:**`, the findings table, folded minor notes, folded Details. `post.gate_summary` writes the same as markdown to
  `$GITHUB_STEP_SUMMARY`; the gate job log keeps its plain lines (first line `agentive-review-gate: ...`) and the `AGENTIVE_GATE=`
  line. `visible_lines` of tests/review/helpers.py counts what a reader sees before the first `<details>` (tests: the PR #1 fixtures stay within 15).
  `evals.comment`: heading per reason (`evals.VERDICTS`), one purpose sentence (`evals.PURPOSE`), Result and What to do
  (`evals._result`), the score table only for a real comparison, per-case table, reviewer files grouped by kind
  (`evals.reviewer_groups`, `_file_list` writes `dir/`: `a`, `b`), the fenced YAML block and the adopt line inside
  `<details><summary>Machine-read result ...`. The block icon ⛔ is used only when the gate fails (R9 of the research; the
  reviewer eval never uses it) [설계 판단]. No parser changed: markers, `util.fenced_blocks`, finding markers, `prstate` regex.
- E2E-34. `pipeline.run` records `manifest_absent` (the base has no `.agents/manifest.yaml`); on the ci profile it adds
  `rv8.required_checks.display` = `config.required_state` with the pending verbs of the head's manifest and source
  `config.FIRST_INSTALL_SOURCE`. `post._required_state` prefers it for display (`required_lines`, `checks_line`, Details); RV8's
  decision, floor and pending_verbs are untouched [설계 판단: the head's require list is not read; only its pending verbs].
- E2E-35. `rv1` skips files whose first path segment is `.agents`, `.claude` or `.codex` (`rv1.AGENTIVE_DIRS`) for
  `env_vars_changed`; `rv2.env_vars_text(names)` is the one always_human detail `env vars added or removed (<n>): A, B, C, D, E (+k
  more)`; the summary shows one G1 line, the names in Details.
- E2E-33. `engine/evidence._git_ignored`: `git check-ignore -v -- <dir>/`, accepted only when the rule's source is a tracked file
  (`git ls-files --error-unmatch`) outside the candidate, not a negation, not `.git/...` or an absolute (global) path;
  `_tracked_dir` (`git ls-files -- <dir>` not empty; outside git the directory test as before) for generated `exists` and for the
  vendor list [설계 판단: vendor follows the same rule]. Test fixtures that relied on an untracked `.gitignore` now `git add` it.
- E2E-32 hint (HEADLESSHINT). `lib/agentive/headless.py` (area B): `ALLOWED` (= the standard command's --allowedTools, content test),
  `READ_ONLY`/`READ_ONLY_GIT` (a subset of Claude Code's read-only commands) [설계 판단], `problems(command)` (heredoc bodies and fd
  duplications removed first, `guard.segments`/`normalize`, one `sh -c` level), `hint(command, no_human, tool)`. Called only from
  `hooks/pre-tool-use-context-gate.py` for Claude Code Bash calls, inside a try/except, after the context gate; the output adds
  `additionalContext` and never a `permissionDecision` (a context-gate deny keeps its fields). AGENTS.md#context has one line; the
  chain skills run stage-check.sh alone (no `On exit 1 print`). No SessionStart line (deferred by the decision).
- E2E-32 sandbox and cache. The standard command (design string, every `%%agentive:gen.headless_cmd%%` copy, tests/sample_repo/
  agent_run.py) passes `--settings '{"sandbox":{"autoAllowBashIfSandboxed":false}}'`; no allowWrite entry is needed now.
  `verb.cache_env(root, cmd, env)`: tools named in the command (`verb._tools`), scratch caches uv (`UV_CACHE_DIR`, default
  `$XDG_CACHE_HOME/uv` or `~/.cache/uv`), npm (`npm_config_cache`, `~/.npm`), ruff (`RUFF_CACHE_DIR`, project `.ruff_cache`),
  pytest (`-o cache_dir=` appended to `PYTEST_ADDOPTS`, project `.pytest_cache`); a real write probe (`_writable`) decides; the NOTE
  line goes to the excerpt and the log. `verb.blocked_write_next`: a failed verb whose log has a refused write in a tool's
  cache or store path (E2E-37 below) gets a NEXT naming the dependency store (`verb.STORES`: cargo, go, gradle, maven) for allowWrite.
- E2E-36. Skills: `git switch -c <b> --no-track origin/<DEFAULT>` (chain-intent, chain-spec, chain-plan), `git push -u origin HEAD`
  (pr, pr-feedback). `gates.headless_push_gate(command, no_human)` is called by `hooks/pre-tool-use-guard.py` right after the
  chain-plan gate: with no human present a `git push` without a refspec (and without --all, --branches, --mirror, --tags, which
  other rules judge) is denied `BLOCKED push-no-refspec: ... NEXT: git push -u origin HEAD`. Not a guard.yaml rule (a rule cannot
  see whether a human is present) [설계 판단]; interactive sessions unchanged.
- E2E-37 (verb.py). `verb.blocked_write_next(cmd, data, root, env)` returns the allowWrite NEXT only when ONE log line has both a
  denial (`verb.BLOCKED_WRITE`: `Read-only file system`, `Operation not permitted`, `EROFS`, `EPERM`) and a cache or store path of a tool
  in the command (`verb.STORE_PATHS` [설계 판단: the list; paths 미검증 on a sandboxed machine], the tool's default scratch cache,
  a cache directory set in the environment, `.agents/state/cache/<tool>`). Test output that only says `Operation not permitted`
  (a test that chmods a file), or a command with no known tool, keeps the normal `grep -n -m20 ...` NEXT. Tests `test_e2e_37_*`
  in tests/hooks/test_e2e_batch_g.py.
- E2E-38 (wording, post.py, evals.py; markers, YAML fences and parsers unchanged). The G2 line says `all 22 files are review/gate
  config` (one kind) or `(all 22 files: a 20, b 2)`, and `(k of n files: kind)` for one kind alone, instead of `22 of 22 files:
  review/gate config 22`. The `agentive-check:` line reads `It adds or removes a tag that ties a written rule to the check enforcing
  it (a line starting `agentive-check:`).` The role prefix `Reviewer:` is dropped from What to do (imperative sentences: `Merge when
  you are satisfied.`, `Review this reviewer change by hand.`, `Or, to merge anyway, add ...`); `Author:` and `Maintainer:` stay
  because they name someone other than the reader. `evals._group_text`: one group alone is its name without the count (`review
  program`, not `review program 7`). Tests `test_e2e_38_*` in tests/review/test_commentux.py.
- UV-43: `--bare` may become the `-p` default (official headless page); the standard command would then need a non-bare opt-out.
- 미검증: the hint's effect on a larger sample (3 runs per variant); the cache fallback and the allowWrite NEXT on a machine where
  the sandbox runs (bubblewrap, macOS Seatbelt); the uv cache path on macOS; the per-version read-only command list; `<details>`
  with a fenced block on GitHub mobile and in e-mail notifications; the live probe test (`AGENTIVE_LIVE_PROBE=1`) was not run in the
  self-test (it calls the model).

## 24. End-to-end run 2026-11-01, low findings: E2E-31 rest, E2E-30 rest, E2E-39

No user decision is changed (`e2e_28_31`, `comment_ux`, `e2e_20_final` stand); these are fixes inside LOCALTIER, COMMENTUX and DEPHUMAN
that the end-to-end run (PRs #6 to #9 of the test repository) left open. Design: `spec.review_pipeline.stages[RV5].finding_lines`,
`spec.repo_checks.dependency_review.feeds`, `spec.work_loop.pr_explainer.when_written`; schema review-report finding
(`reviewer_line`, `line_note`, `file_level`). Regression tests: tests/review/test_e2e_batch_f.py (`test_e2e_31b_*`,
`test_dep_review_text_*`), tests/review/test_e2e_39.py, tests/content/test_prbody.py (`test_e2e_30b_*`) and the updated wording tests of
tests/review/test_e2e_20.py.

- E2E-31 rest (post.py, rv1.py). The dependency reason sentence is shown once, in Why (`Dependencies changed: ...`, then the review
  state). Details no longer repeats it: `- Dependency change lists:` holds the full lists (added, changed, removed, lockfiles,
  unreadable; Why shows five names) from `rv1.signals.dependency_changes`, and the reason code stays in `Reason codes`. The rv2
  detail string that the gate log and `prstate` read is unchanged.
- Dependency review state in plain words (rv1.`DEP_REVIEW_TEXT`, post.`DEP_RE`, workflow, `agentive-review checks`). Four states:
  `pass` -> "dependency review ran and found no critical CVE", `fail` -> "found a critical CVE (see the dependency-review job)" (not
  "information only": it keeps its own always_human line), `skipped` -> "could not run (usually the dependency graph is off, or a
  private repository has no GitHub Code Security)", no entry -> "did not run for this commit". The old "no result" covered the last two.
  The workflow step now prints `status=unavailable` when the action did not complete (the ::notice:: is unchanged), and `checks`
  records `dependency-review=unavailable` as a check with status `skipped`; an empty or `skipped` job result (the job did not run in
  this event) still adds no entry. The "usually" is the documented precondition of the action, not a measured cause: the step
  cannot read why the action failed [설계 판단: wording] (미검증: the action's own error text for each cause).
- E2E-39 root cause. In PR #9's first head the combined `diff.patch` held `src/shop/auth/__init__.py` (28 lines with headers) and then
  the new `tests/test_auth.py`; file line N of the second file was patch line 34 + N, so the reported `:44` and `:58` were file lines
  10 and 24 (the `def` lines of the tests the finding is about) of a 26-line file. The reviewer read `diff_file` with the Read tool,
  which numbers the lines of the patch file, and the task said only "integer line in the head tree". Nothing compared the line with the
  head file: `findings.clean_reviewer_output` kept any integer >= 1, `Anchors.key` returned None for it (anchor `line:44`), and GitHub
  refused the inline comment on a line outside the diff, so the REST fallback posted a file-level comment shown on line 1.
- E2E-39 fix (findings.py, pipeline.py, tasks.py, the three reviewer prompts, github.py, agentive-review). Prevention: the reviewer task
  has `changed_files` ({lines, added ranges} per changed file, first 300), `OUTPUT_FORMATS.reviewer.line` and the prompts say the line is
  the file's own number (the +c of the hunk header) and never a line of `diff_file`. Validation: `findings.place_line(anchors, changed,
  path, line, patch_map)` runs for every finding and every instance: inside the file -> kept; past the end -> the patch line it names
  (`patch_line_map` of the exact patch file the reviewer was given, round 2+ uses the since-patch) -> the nearest line the diff adds ->
  file-level (`file_level`, line 1; instances past the end are dropped). The anchor key and the finding id come from the placed line.
  The report keeps `reviewer_line` and `line_note`; the inline comment has a `- Location:` line; the summary table shows the file alone
  for a file-level finding (`post.loc`); the reviewer's problem list notes how many findings moved. A file unreadable at head keeps
  its line. Not detectable: a patch number that is also a valid file line (the prevention above covers it) [설계 판단: the order].
- E2E-30 rest (prbody.py `UNCERTAIN_SOURCE`). No safe deterministic rule exists: for an ordinary source file no path rule applies and
  tier 2 can come only from the content (the RV3 classifier calling the change `security`; the RV4 raise-only check). A rule that
  predicted tier 1 would reproduce the E2E-29 failure (explainer_missing in CI) whenever the classifier disagrees. So `TIER=uncertain`
  and `EXPLAINER=required` stay, and the reason now says which file is undecided, that CI asks its LLM classifier, that the result is
  tier 1 unless the classifier calls it a security change or the raise-only check asks for a human (then tier 2 and the Explainer is
  required), and that the Explainer is written now to be safe. The tier-2 rules the estimate can settle (security paths, dependency
  change, protected config) are unchanged.

## 25. Skill guidance: scripts for fragile steps, no dates in skill text, sources (SKILLGUIDE, user 2026-11-01, "네 추천대로 확정")

Source: the skill guidance audit of 2026-11-01 (official pages: Claude skill authoring best practices S228, Agent Skills specification
S229, Prompting Claude Fable 5 S230; the video claim "the rules changed" is not backed: the rules were in the 2025-11-05 page too).
Design: `decisions.accepted[SKILLGUIDE]`, `spec.skill_scripts.authoring_rules`, `spec.work_loop.pr_feedback.writes`,
`spec.maintenance.janitor.label`. Decision 4 of the audit, a per-model skill eval (`claude plugin eval --model`) and trimming prose per
model, is deferred and not implemented.

- Rule [설계 판단 for the threshold]: a skill step becomes a skill script when its failure changes the outcome or a model-written string
  goes through shell quoting; judgment steps stay prose. Scripts are POSIX `sh` plus `gh api` (no Claude-only variable), so the same file
  runs under Claude Code and Codex; they sit in `<skill>/scripts/`, are called repo-root relative (SKILLPATHS) and need no unit entry
  (the skill's unit covers its directory; only the unit `note` lists them).
- `pr-feedback/scripts/pr-reply.sh <pr> <comment id> <body file>`: the agent writes the reply text with the Write tool to
  `.agents/state/pr-reply-<comment id>.md` (no heredoc or redirect, which headless runs refuse) and the script posts it with
  `gh api -X POST repos/<owner>/<repo>/pulls/<n>/comments/<id>/replies -F body=@<file> --jq .html_url` (REST only, RESTONLY). It first checks
  every `agentive-reply:` line against the format `pr-state.sh` reads (`prstate.REPLY`; the sentence is required unless the reason is
  `fixed`) and ends with `REPLY_POSTED pr=<n> comment=<id> url=<url>; NEXT=...` or `AGENTIVE_FAIL tool=pr-reply reason=<code>; NEXT=...`
  (exit 2). It never repeats the POST (a second POST would double the reply). The thread-resolve step (GraphQL, local only, reply-only
  fallback) stays prose: cloud sessions and Routines block GraphQL, so there is nothing a script could do there (S7).
- `janitor/scripts/label-pr.sh <kind> <pr number>`: POST the label `agentive-janitor:<kind>`, read the labels back with GET, repeat the POST
  once when the exact label (a whole-line match, so `agentive-janitor:lint-fix-2` does not count) is missing; `LABELED ...` (exit 0) or
  `AGENTIVE_FAIL tool=janitor-label reason=label_missing; NEXT=...` (exit 2). The token rule (not `GITHUB_TOKEN`) is in the script header
  and the skill text. The observed gap (in a headless run `/pr` ends with `AGENTIVE_PR=done ... waiting=human_merge; NEXT=report the PR URL and end the
  run`, which can read as "stop" before the janitor labels the PR) is fixed in §26 (E2E-40).
- The init question names the fact without the date: "managed Code Review (Team/Enterprise; generally available)" in both init skills and in
  `spec.init_apply.optional_outputs.questions[review_copy].text`.
- Tests: `tests/content/test_pr_feedback.py` (`PrReply`: file content posted as is, format check, bad arguments post nothing, failed POST not
  repeated), `tests/content/test_maintenance_scripts.py` (`JanitorLabel`: add and confirm, one retry, `label_missing`, exact-name match,
  bad arguments), `tests/content/test_skills.py` (`SkillGuidance`: SKILL.md calls the scripts and holds no raw POST, no date in the init
  skills, S228-S230 in the design). `tests/content/fakegh.py` gained `"seq"` routes (the n-th matching call gets the n-th answer) for the retry.
- 미검증: a reply and a label in a live Routines run (tests use a fake `gh`); that Codex follows the repository-root sentence for the new
  script calls (the same open point as SKILLPATHS).

## 26. Labels belong to PR publication; no step after /pr (E2E-40, 2026-11-01)

Design: `decisions.accepted[PRLABEL]` (decided_by orchestrator: HUMANMERGE and SKILLGUIDE are unchanged), `spec.work_loop.pr_skill.labels`,
`spec.maintenance.janitor.label` and `.merge`. Finding: in headless runs (`claude -p`) `/pr` on a tier 2 PR ends with `AGENTIVE_PR=done ...
NEXT=report the PR URL and end the run` (HUMANMERGE), so the janitor, whose label step (`label-pr.sh`) came after `/pr`, could stop before
`agentive-janitor:<kind>` was set; the label is what makes the PR tier 2.

- `pr-body.sh publish <file> [--title T] [--label <name>]...` (`prbody.py`: `apply_labels`, `label_fail_line`, `merge_lines(labels=)`).
  The label names are checked first (`^[A-Za-z0-9][A-Za-z0-9:._/-]*$`, else `AGENTIVE_PR=stop reason=bad_label`, exit 2, nothing
  created). After the PR is created (or the open PR refreshed) each label is added with `gh api -X POST repos/<o>/<r>/issues/<n>/labels -f
  'labels[]=<name>'`, read back with `-X GET ... --jq '.[].name'` (whole-line match) and added once more when missing: the same three calls
  as `janitor/scripts/label-pr.sh` [설계 판단: two implementations, because publish is Python in `.agents/lib` and the script is POSIX sh
  for a bare `<kind>`; both are tested]. Output order: `PR=<n> ... CREATED|UPDATED=...`, `LABELED <names> pr=<n>`, the MERGE/next lines. The
  headless final line carries the state: `AGENTIVE_PR=done pr=<n> waiting=human_merge url=<url> labels=<names>; NEXT=...`. A label that
  stays missing: the last line is `AGENTIVE_FAIL tool=pr-label reason=label_missing pr=<n> url=<url> labels=missing:<names>; NEXT=...`
  (exit 2); its NEXT names `label-pr.sh <kind> <n>` for a janitor label, says the PR is not labeled and ends with "report the PR URL
  and end the run". Without `--label` the output is byte-for-byte the old one.
- `janitor/SKILL.md` step 2.4 is one step: open the PR with /pr and pass `--label agentive-janitor:<id>`; it has no step after /pr.
  `label-pr.sh` stays as the repair. Other skills checked for a work step after `/pr`: `chain-intent`, `chain-spec`, `chain-plan` end with
  `/pr` (no change); `distill` removed the handled queue entries after `/pr`, now before it (the queue is local and untracked; the fix is
  committed on its branch first, so a failed `/pr` loses no work) [설계 판단]; `pr-feedback` resolved threads after the `/pr` refresh, now
  step 7 is the resolve, step 8 the `/pr` refresh and step 9 the report (`pr-reply.sh`'s NEXT follows the new order). The only text after a
  final `/pr` is the report.
- Tests: `tests/content/test_e2e_40.py` (label applied and verified in call order before the final line, headless `labels=`, refresh, one
  retry, failure line with PR URL and NEXT, a failing REST call, bad label creates nothing, unchanged output without `--label`, janitor
  SKILL.md uses `--label` and has no later step, `/pr` SKILL.md documents it, distill and pr-feedback order, chain skills end with `/pr`);
  `tests/content/test_skills.py` and `tests/ci/test_workflows.py` (janitor text) and `tests/content/test_pr_feedback.py` (new NEXT).
- 미검증: publish labeling in a live Routines run and with a GitHub App token (tests use a fake `gh`).

## 27. Reviewer eval bundle: case supply, line tolerance, changed-case table (EVALBUNDLE, user 2026-11-01, "일단 그렇게 진행해줘")

Design: `decisions.accepted[EVALBUNDLE]` (also revises RVEVALRUN and records the SKILLGUIDE revisit triggers), `spec.review_pipeline.reviewer_eval`
(`intake.case_draft`, `scoring.match`, `scoring.diagnostic`, `ci.changed_cases`), `schemas/reviewer-eval-case.schema.json`. The numbers below
(DEFAULT_SLACK 2, MAX_SLACK 20, 10 positives, 20-line warning) are orchestrator defaults after the user's choice [설계 판단, the user can
change them]; no official source gives a line tolerance (미검증). Not done (decided): a CI score gate (revisit at >= 20 cases), an LLM judge, a
dashboard or HTML, a skill eval (SKILLGUIDE decision 4 stays deferred; `decisions.accepted[SKILLGUIDE].revisit_triggers` lists when to look again).

- Scoring (`review/evals.py`): `catches(finding, rng, slack=0)`; an expected range uses `range_slack(rng)` = its `slack`, else
  `DEFAULT_SLACK = 2`; a forbidden range always 0 (`validate_case` and the schema reject `slack` there). Several ranges stay all-required.
  `run_wrong_place(case, run)` is the diagnostic (positive run, a missed range, a counted finding on the same file at another line; the finding's
  `bug_class` must equal the case's when the case has one; `counted()` now carries `bug_class`). It never enters `run_result`, `newly_*`
  or the adopt rule. `summarize()` adds the optional list `wrong_place: [{id, baseline_runs, candidate_runs}]` only when some run had one.
- Case fields: all optional (`base_sha` + `head_sha` together, `bug_class`, `note`, `source{finding_id, reply, range_basis}`, `created`,
  `drafted_by`, per-range `slack`); `validate_case` and the schema agree (a test compares them case by case); nothing required changed. The
  runner (`agentive-reviewer-eval run`, `_case_range`) uses `base_sha..head_sha` when both are present (fetching `pull/<n>/head` once when they are
  missing from the clone), else asks the PR as before.
- Comment (`evals.comment(summary, reasons, notes, detail=None)`): `detail = {cases, baseline, candidate}` is what `cmd_score` knows beyond the
  summary (expected locations, per-run results); without it the table shows `-` for the location and no Why block. Visible: heading, what it is,
  Result, What to do, score table, one noise sentence, "Reference only: fewer than 10 positive cases (<p> now) ..." while `p < MIN_POSITIVES`,
  `**Changed cases (k of n)**` and its table (regressions first, the change as words via `case_change`), the one-line `wrong place` legend when
  the summary has `wrong_place`. Folded: `Why: runs of the changed cases` (`_run_text`), `Per case (n): newly missed a, newly caught b, newly noisy c,
  d unchanged` with the full table (+ a `wrong place before → after` column only with `wrong_place`), reviewer files, machine YAML. The visible
  part is about 7 lines (budget 15, `visible_lines` of tests/review/helpers.py; tables are not counted by it).
- `/distill` (`skills/distill/SKILL.md` section 3a, `scripts/eval-case.sh` -> `lib/agentive/evalcase.py`, area C): `draft positive|negative`.
  Positive range = the narrowest hunk of the fix (merge base of the fix .. fix head, old-side lines; a pure insertion counts the two lines it
  sits between) that overlaps lines the original PR added, mapped onto the original head with `git diff -U0 <fix mb> <head>` offsets when the
  file differs there (a hunk that the original head changed again is reported as `EVALCASE_WARN=unmapped ...` and skipped), then cut to the
  added lines; or `--range`. No fix location -> `EVALCASE=skipped reason=no_fix_location`, no file. Negative: `--forbid` locations on the head of the
  round the finding was posted on, `--reply wrong|intentional` (`not_important` -> skipped). Dedup (`find_duplicate`): same id, the
  `agentive-check: eval-<n>` marker in a case of the same label, the same `source.finding_id`, or the same `pr`/`head_sha` with an overlapping
  range. The file is rendered by hand (comments survive), re-read with `ayaml`, checked with `evals.validate_case`, written only when valid.
  `range_basis` is always `proposed`. Output lines: `EVALCASE_BODY=` (one PR body line per case), `EVALCASE_WARN=` (wide range, range outside the
  added lines), `EVALCASE=written|skipped|duplicate|stop`.
- Tests: `tests/review/test_eval_bundle.py` (slack boundaries, per-range slack, negatives slack 0, wrong-place diagnostic and its invariance of
  pass/adopt, backward compatibility of cases and summaries, optional fields in code and schema, comment layout, reference-only line at 8 and
  10 positives, Why block, regressions first, 15-line budget, failed run, runner `score` with detail, pinned shas), `tests/content/test_eval_bundle_distill.py`
  (real git history: narrowest hunk, line mapping across an unrelated insertion, given location, revert-only skip, dedup four ways, negative draft,
  bad arguments, warnings, SKILL.md and README text), `tests/design/validate_schemas.py` (EVALBUNDLE block); `test_commentux` expects the new
  `Per case (...)` summary.
- 미검증: the draft procedure on a real escape (tests use throwaway repositories); that the review comment field `original_commit_id` is the commit a finding
  was left on (the skill text names it; the GitHub REST page was not re-fetched); the values 2, 20, 10 and 20 above on real cases; how the changed-case table renders on
  GitHub mobile and in e-mail.

## 28. CI fixes for 6e7903b: Python 3.9 runner image, backup commit without a git identity (2026-11-01)

- `runtime-python-floor` failed with "The version '3.9' with architecture 'x64' was not found for Ubuntu 26.04": ubuntu-latest moved to
  Ubuntu 26.04, where setup-python offers 3.10 to 3.15 (3.9.25 stays published for ubuntu-24.04 and 22.04 only; actions/python-versions
  versions-manifest). First plan was a pin to ubuntu-24.04; the user instead raised the floor (2026-11-01, "README에 3.13으로 변경하고
  우리 최소 버전을 3.13으로 정하자", decisions.accepted[PYFLOOR]): the promise is now `python3 >= 3.13` in README.md (quick start and the
  `NEXT=install python3 >= 3.13` row), the engine check (`src/engine/apply.py`: `sys.version_info < (3, 13)`), every `NEXT=install python3 >= 3.13`
  text (`.agents/bin/*`, skill scripts), the docstrings and docs/IMPLEMENTATION.md §4. The job `runtime-python-floor` runs Python 3.13 on ubuntu-latest, so no image pin is
  needed. New job `runtime-python-latest` (ubuntu-latest, Python 3.15, tests/lib and tests/review) so the repo tests both the lowest promised
  and the newest Python; raise its version when setup-python lists a newer release. No 3.10+ feature is used yet [설계 판단]; the TOML reader
  keeps avoiding tomllib. `tests/sample_repo` keeps `requires-python = ">=3.9"` in a fixture project: it is the user's project, not the layer.
  미검증: the run-time tests on 3.15 (not installed locally; CI runs them first).
- `selftest` / component_tests failed in tests/hooks (4 tests of `VerifyStagedTest`, E2E-08) with `AGENTIVE_FAIL verb=verify exit=2
  log=none; NEXT=git could not record the backup commit ...`. Cause: `verb._verify_staged` ran `git commit-tree` for
  `refs/agentive/verify-staged`, and `commit-tree` needs a committer identity; the runner has no user.name/user.email (the tests
  passed locally because the developer's global config has one; the tests gave an identity only to their own git subprocesses, not to
  the in-process verb). Reproduced with `GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null`, no `EMAIL`/`GIT_*` identity variables.
- Product fix [설계 판단]: `verb._backup_commit` runs `git -c commit.gpgsign=false commit-tree` with `GIT_AUTHOR_*`/`GIT_COMMITTER_*` set to
  `agentive <agentive@localhost>` (`verb.BACKUP_IDENT`) in the environment of that one call. The backup is an internal ref, so it needs
  neither the user's identity nor a signing key, and no git config of the user is read for it or changed. Other git calls of the
  layer (`write-tree`, `read-tree`, `add` into the scratch index, `update-ref`, `restore`, `diff`) need no identity.
- Tests: `tests/hooks/test_hooks.py` now runs in an isolated git environment (`no_identity_env`: no system/global/XDG git config, no
  identity variables), so every hooks test is a no-identity test; its own git setup calls pass an identity explicitly (`GIT_ENV`).
  Regression tests `test_ci_no_git_identity_verify_staged_runs_and_restores`,
  `test_ci_no_git_identity_backup_commit_uses_the_internal_identity` and
  `test_ci_no_git_identity_signing_config_does_not_block_the_backup` (they first prove that `commit-tree` is refused in that
  environment, with `user.useConfigOnly`, so they cannot pass vacuously).

## 29. End-to-end run 2026-11-01 on xrpz/agentive-e2e (PRs #1-#5) and CI of 278bebc: E2E-41 to E2E-46

Regression tests are named `test_e2e_41_*` to `test_e2e_46_*` (tests/ci/test_workflows.py, tests/review/test_e2e_41_46.py,
tests/content/test_e2e_44_46.py, tests/hooks/test_e2e_46.py). Choices the design left open are marked [설계 판단]; no user decision
(python_floor 3.13, comment_ux, e2e_10b, e2e_11, e2e_14_15, e2e_16_17_18, eval_bundle) is changed.

- **E2E-41 (high)**. Cause: job `selftest` of `.github/workflows/ci.yml` used `python-version: "3.12"` while the engine stops below 3.13, so
  component_tests failed (73 failures, 4 errors); the two runtime jobs (3.13, 3.15) passed. Fix: `selftest` and `selftest-agent-run.yml` use
  3.13. Test: `test_e2e_41_every_python_version_in_workflows_is_at_least_the_floor` reads the floor from `src/engine/apply.py` and checks every
  `python-version:` of `.github/workflows/`, `src/repo-layer/dot-github/workflows/` and `src/engine/ci/`. The installed templates hold no
  fixed version; the toolchain steps the engine renders (`python-version-file`, CISETUP) take the user's project version, which can be
  below 3.13: then `python3` on that runner is older and the layer scripts stop with `NEXT=install python3 >= 3.13` (E2E-46 b) [설계 판단:
  not changed, the project's own version wins]. 미검증: a user workflow run on such a project.
- **E2E-42 (medium)**. Cause: `reviewer-eval.yml` passed `--not-run` for both a fork PR and a missing secret, and the comment always said
  "fork PR"; the PR summary comment mirrored only `insufficient_cases`. Fix: the `score and comment` step passes `--not-run-cause fork`
  (head repository differs) or `no_secret`; `evals.summarize` records `not_run_cause` (optional, schema `reviewer-eval-case` summary, only with
  reason `not_run`); the comment says "not run — fork PR" or "not run — no AI credential secret" with the secrets to add (`llmstate.secret_names`,
  OAuth token first) and the unchanged `eval-override` way out; without a cause it says only that no secret was available. The summary
  comment's **Reviewer eval:** clause (`evals.summary_line(cause=...)`, from `AGENTIVE_CI_SECRET_SET` of the `finish` step) says the same,
  so the two comments agree.
- **E2E-43 (medium)**. (a) Cause: with no secret `claude-code-action` stops before it writes an execution file, so the record was
  `no_result` ("ended without a result") and the cause was only in the job log. Fix [설계 판단: two codes added to the E2E-10b allowlist; the decision
  itself, code + fixed sentence + run URL and no raw text, is unchanged]: the `llm-state` step gets `AGENTIVE_CI_SECRET_SET`
  (`secrets.<name> != ''` of the tool's credential secrets, from `render.ci_secret_check`: names only, never a value); a failed step that
  never started Claude and has no secret is `no_secret` (sentence names the secrets to add) or `fork_pr` (head repository differs); no
  debug-log hint for these codes. Shown in the annotation, the summary comment (Why, What to do, Details), the gate step summary and the gate
  `NEXT=ai_step_failed:<code>: ...`. (b) Cause: a failed AI step leaves the RV4 raise-only answer missing, `rv4.finish` raises to tier 2
  (`llm_raise`, `llm_output_invalid`) and RV8 then required the Explainer, so a docs-only PR was blocked by `explainer_missing`. Decision
  [설계 판단 within EXPLAIN and AISTEPFAIL]: `rv4.ai_unavailable_only(reasons)` (tier 2 whose only cause is `llm_output_invalid`; the
  rules said tier 0 or 1) does not require the Explainer: RV8 ends `human_required`, `rv8.explainer = {required: false, result: not_required}`,
  `ai_step_failed` names the cause. A tier 2 from a rule (trust boundary, size, dependency, always_human) or a valid LLM raise
  (`llm_raise:a|b|c`) still requires it. Schema `review-report`: new allOf item (and the tier 2 item excludes this case). Design: EXPLAIN
  `applies_to`, RV8 rule, AISTEPFAIL `revised_e2e43`. Tests: `test_e2e_43_*`.
- **E2E-44 (medium)**. Cause: `prbody.review_block` and `u2_block` read `.agents/state/precommit-review.json`, untracked state that outlives the
  change it reviewed, without comparing it with the content. Fix [설계 판단]: the record carries `tree` (`git write-tree` of the staged
  change, schema `precommit-review`, optional); `prbody.precommit_record` uses a record only when `tree` (older records: `reviewer.reviewed_ref`)
  equals `HEAD^{tree}`. Otherwise the record is dropped (no review entry, its U2 confirmation counts as not obtained) and the draft prints once
  `NOTE PRECOMMIT_STALE ... NEXT=run /precommit-review on this content (git reset --soft origin/<default>, run /precommit-review, commit again), then rerun
  ...draft` before the final line; the PR still opens and CI reviews it. A rebase that changes the tree makes the record stale on purpose.
  The pr SKILL.md names the note. Tests: `test_e2e_44_*`.
- **E2E-45 (low)**. Cause: the `agentive-review-summary` comment is written by the ai-review `finish` step, before `agentive-independence` and the
  gate have a result, yet its Details line said `(required check agentive-review-gate: success)`; the gate later failed and never touched the
  comment. Fix: the first version says `Decision ... so far; the agentive-review-gate job makes the final decision and updates this comment`
  (`summary_comment(final=False)`); the gate job posts the same comment again from its own report (`_apply`: same `rep`, the view it
  writes to the step summary, and the Reviewer eval clause stored by `finish` in `summary-extras.json`; one short line starting with `not `
  is accepted from that artifact). The gate job still names no secret. Test: `test_e2e_45_*`. 미검증: the second upsert on a real PR.
- **E2E-46 (low)**. (a) The `AGENTS.md#context` template has one line `Branches:` with `git switch -c <type>/<slug> --no-track origin/<default branch>`
  and the note that the headless allowlist has `git switch *` and denies `git checkout -b`, the same command as the chain skills. (b) The
  installed layer had no floor check (the engine alone printed `AGENTIVE_ERROR python3 < 3.13`): the Python entry points
  (`agentive-lint`, `agentive-review`, `agentive-reviewer-eval`) and the SessionStart hook print that exact line and exit 2 (hook: context line);
  `bin/verb` and the five skill shell wrappers test `sys.version_info` through `python3 -c` and print their own format with
  `NEXT=install python3 >= 3.13` (`python3_too_old`). The other hooks are unchanged [설계 판단]: they run on every tool call and must stay silent
  and fail-open. Tests: `test_e2e_46_*` run every entry point with a `python3` shim that reports 3.12.

## 30. End-to-end run 2026-11-01 on xrpz/agentive-e2e (minimal run, PR #1-#4): E2E-47 to E2E-51

Regression tests are named `test_e2e_47_*` to `test_e2e_50_*` (tests/engine/test_regressions.py `E2E47And48Test`, tests/review/test_pipeline.py,
tests/content/test_prbody.py, tests/content/test_regions.py). No user decision is changed; the design is silent on each point below, so
every choice is marked [설계 판단].

- **E2E-47 (medium)**. Cause: `engine/prove.py` ran the `build` command (`uv build`) in the work tree and left what it made (`dist/`, and
  `src/<pkg>.egg-info/` for setuptools). Outside git (or with a tracked file inside) `evidence._tracked_dir` takes a directory test, so the next
  `update` recorded `paths.generated: [dist]`; inside git the E2E-33 rule already ignores an untracked `dist/`, so that half of the old finding
  did not reproduce on 7b9b0f4 (the leftover itself did). Fix [설계 판단: design `proof.build` says only "produces its artifact"]:
  `prove._dirs` lists the directories at depth 1 and 2 (not `.git`, `.agents`, `.claude`, `.codex`, `.venv`, `venv`, `node_modules`,
  `__pycache__`) before the build proof; afterwards `_clean_build_output` removes the directories that were not in that list and prints
  `AGENTIVE_PROOF_CLEAN verb=build removed=<paths>`. Only the build proof cleans (lint and test caches carry their own `.gitignore`; `.venv` and
  `node_modules` of the setup proof are needed by the later proofs); only directories (a lockfile a build writes stays); a directory that existed
  before is never removed. Cost: a build that is slow to repeat is repeated by the user's next build.
- **E2E-48 (low)**. Cause: `src/repo-layer/dot-gitignore.regions/state.txt` held only the two designed lines
  (`spec.repo_layer.gitignore_state`). Fix (decided: decisions.accepted[GITIGNORESTACK], user 2026-11-01, `spec.repo_layer.gitignore_stack`): the file ends with `%%agentive:render.gitignore_stack%%`; `apply.GITIGNORE_STACK` maps
  an evidence ecosystem to patterns, today `python` -> `__pycache__/`, `*.egg-info/` (github/gitignore Python.gitignore; empty for other
  stacks, the placeholder line then disappears). The rule: the region holds only files that agentive verbs create and that the producing tool
  does not ignore itself; no other language patterns. `dist/` and `build/` are left out on purpose: a tracked ignore rule for them
  would turn `paths.generated` into `gitignored` and add Read/Edit denies (E2E-33). It stays in the region `.gitignore#state` (no new region id,
  so `managed_regions.regions` is unchanged); the next `update` rewrites the region of an existing python repo (an unmodified region is
  refreshed, a modified one is reported as drift as always).
- **E2E-49 (medium)**. Cause: `agentive-review precommit` printed one confirm NEXT for `run=interactive`; a delegated agent has no
  `AGENTIVE_HEADLESS` and no `CI`, so it was `interactive` and saw only "ask the human". Fix [설계 판단]: on the default branch (`origin/HEAD`, else
  main or master) the same line continues `; if no human is present to answer (a delegated agent, a scheduled run): do not commit to <branch>,
  commit on a work branch (git switch -c <type>/<slug> --no-track origin/<branch>) and open a PR with /pr (tier 2, a human merges it)`. On a
  work branch the line was unchanged here (changed by §32, E2E-52); the `non_interactive` line is unchanged; `--human-confirmation obtained` is not weakened.
- **E2E-50 (medium)**. Cause: `prbody.cmd_publish` treated any open PR as a refresh and replaced `## Verification` only, whatever the draft held.
  A draft made in `MODE=new` (`PR=none`) and an open PR at publish time (PR #1 of the e2e repo, whose body was the commit message; who opened
  it is 미검증: the timeline shows no agent action) lost the Explainer, Risk and review record and the run ended as a success. Fix [설계 판단:
  stop, not overwrite, because the open PR body may be a person's text]: a draft with a level-2 heading other than `## Verification` is a full body;
  with an open PR, publish stops `AGENTIVE_PR=stop reason=pr_opened_since_draft pr=<n>; NEXT=...` and sends nothing. `publish --replace-body`
  saves the current body to `.agents/state/pr-body-before-<n>.md`, writes the whole draft and prints `PR=<n> UPDATED=body saved=...`. The refresh
  draft (`## Verification` only) works as before. The case left open here (`draft` against an open PR whose body never had an agentive body)
  is closed by §31 (PRADOPT): `MODE=adopt`, and the stop is kept only for an open PR that already holds a /pr body.
- **E2E-51 (low), no change** (decided: decisions.accepted[TIER0LOCALREVIEW], user 2026-11-01). Tier 0 changes still run the local reviewer
  step or the honest `self` record; CI `agentive-independence` keeps the independent review. Revisit when tier 0 PRs are frequent and the step
  is a measurable burden (the decision lists the condition and the script-only skip to consider then).

## 31. `/pr` on an open PR whose body is not a /pr body (PRADOPT, user 2026-11-01, "확정해줘")

Regression tests: tests/content/test_prbody.py `Adopt`, `AdoptDraft` (fail on 5bd987a). Code: `prbody.py` `is_pr_body`, `quote_original`,
`adopt_body`, `body_fingerprint`, `cmd_draft` (`MODE=adopt`), `cmd_publish`. The rule is in decisions.accepted[PRADOPT]; here the choices
and the parts the design leaves open [설계 판단].

- **Detection** [설계 판단]: a body "has /pr sections" when a level-2 heading `## Explainer`, `## Verification`, `## Risk`, `## How to revert` or
  `## agentive review record` stands outside a code fence, or the ```yaml agentive-review block exists. A person's own `## Risk` heading
  therefore makes the body count as a /pr body (refresh, not adoption). An unreadable body (gh error, no `body` key) counts as a /pr body
  at draft time; publish reads it again and stops `pr_body_not_agentive` when it turns out to be a person's.
- **Written body**: the drafted body, a blank line, `## Original description`, a blank line, the old text with `> ` before each line (empty
  line -> `>`, `\r\n` and `\r` read as `\n`, trailing blank lines dropped). Last position, so tier 2 keeps `## Explainer` as the first heading;
  quoted lines cannot be read as headings, fences or markers by `section`, `fences`, `block_problems` or CI. `check` runs on the draft, not on
  the quote. The old body goes to `.agents/state/pr-body-before-<n>.md` first (untracked, local only).
- **Idempotence**: after adoption the body has /pr sections, so the next draft is a refresh and a full draft published again stops
  `pr_opened_since_draft`. A body that is nothing but an `## Original description` section is reused as is (`quoted_original_only`).
- **Changed since the draft** [설계 판단: the simplest guard that covers the long gap]: the draft stores the sha256 of the body it read
  (`pr-body-seen-<n>.sha256`, line endings normalized, so a web-editor CRLF round trip is no change); publish compares it with the body it
  reads immediately before the PATCH and stops `pr_body_changed_since_draft` when they differ. Without a stored fingerprint (the draft was made
  before the PR existed) there is nothing to compare and the body read at publish is the one quoted. The few milliseconds between that read and
  the PATCH stay open: GitHub's PATCH is not known to offer a body precondition [미검증]. Cost of a second read just before the PATCH was not
  paid.
- **Changed relative to 5bd987a**: the E2E-50 stop `pr_opened_since_draft` is kept but only for an open PR that already holds a /pr body; a
  full draft against a person's body is now adopted (nothing is lost, the text is quoted), and its old test uses a /pr body. `--replace-body`
  is unchanged (full replacement, backup, no original section). New stops: `pr_body_changed_since_draft`, `pr_body_not_agentive`.
- **Review record with a person's commits** (checked, not changed): `review_block` writes `implementer` = the tool, model and session given to
  `draft` (`--tool`, `--model`, `--session-id`) and `started_at` = the first commit ahead of the base, whoever made it. It reads no commit
  author, and nothing in `review/` uses `implementer` to attribute commits or to count independence (CI counts only its own review,
  `independence.py`). The record names the session that ran /pr, so it makes no false claim; `AdoptDraft` pins that no author is written.

## 32. End-to-end run 2026-11-01 (second minimal run): E2E-52 fix, E2E-54 check

User decision (2026-11-01, [user]): fix E2E-52 after the end-to-end run ends; E2E-53 no fix ("E2E가 끝난 뒤 다시 한 커밋으로 올리기해줘"). The design is
silent on the wording of the confirm NEXT (it only fixes the rule: spec.work_loop.precommit_review `order` ends with "work branch + /pr only,
tier 2 forced"), so every choice below is [설계 판단]. Regression test: tests/review/test_pipeline.py `test_e2e_52_*` (fails on 9ccf85c).

- **E2E-52 (medium after review of the cause)**. Cause: `no_human_path` in `src/repo-layer/dot-agents/bin/agentive-review` returned `""` unless the
  current branch was the default branch (E2E-49), so on a work branch the confirm NEXT said only "ask the human ... --human-confirmation obtained". A
  headless run (`claude -p`) or Routines run that follows NEXT could stall, although AGENTS.md "Commit" lets it commit on a work branch and open a PR.
  Fix: on a work branch the same line continues `; if no human is present to answer (a delegated agent, a scheduled run): commit on this work branch
  (<branch>) and open a PR with /pr (tier 2, a human merges it)`. On the default branch the E2E-49 text is kept (switch to a work branch first, never commit
  there). On a detached HEAD (no branch to commit on; a commit there is lost when HEAD moves) the line says `HEAD is detached, so create a work branch first
  (git switch -c <type>/<slug>), commit there and open a PR with /pr (tier 2, a human merges it)`. One final line, same format; the `non_interactive` line
  and `--human-confirmation obtained` are unchanged. The PR then gets tier 2 through U2.non_interactive (RV4), as for any unconfirmed confirm-scope hit.
  The precommit-review skill step 4 names the three cases; the AGENTS.md "Commit" template line and the design already say this and are unchanged.
- **E2E-54 (low), partly a test clone setup, one framework text fixed**. The failing `git pull --ff-only` came from the test clone (`git init` + `git remote add`,
  no upstream on main; cause unconfirmed, 미검증), and no script or skill text tells the agent to pull the default branch: the scripts use an explicit refspec
  fetch (`git fetch origin +refs/heads/<d>:refs/remotes/origin/<d>`, BASEREF) and the branch rule is `git switch -c ... --no-track origin/<default>`. The grep over
  `src/` found one bare `git pull --ff-only`: the `head_mismatch` NEXT of `prstate.py` (`PR_STATE=stop`), which needs an upstream on the PR branch and fails the
  same way when the branch was pushed without `-u`. It is now `git fetch origin pull/<n>/head, then git merge --ff-only FETCH_HEAD; no upstream needed` [설계 판단:
  `pull/<n>/head` also covers a fork head and needs no branch name; GitHub documents the `refs/pull/<n>/head` ref, not re-verified here: 미검증]. Test:
  tests/content/test_pr_feedback.py `test_stops`.

## 33. Engine run from the template source: NEXT of an unreadable payload index (E2E-56, 2026-11-01)

User decision (2026-11-01, [user]): "e2e 56 반영하고". The design is silent on this NEXT (it fixes only that the engine installs exactly the entries of
`<payload>/index.json`), so every choice below is [설계 판단]. Regression tests: tests/engine/test_regressions.py `IndexMissingNextTest` (the src-run case fails on f9f9e64).

- **E2E-56 (low)**. Cause: `Engine._load_index` in `src/engine/apply.py` answered every unreadable index with "reinstall the agentive plugin", but `src/` has no
  generated `index.json` by design, so running `python3 src/engine/apply.py` in the framework repo ended with a NEXT that cannot help (the plugin is fine).
- **Fix**. `run_from_framework_src()` is true when the engine file is `<repo>/src/engine/apply.py` and `<repo>/tools/generate.py` exists [설계 판단: a path rule,
  no state; `src/repo-layer/` cannot be the test because the template source has that directory too, so the brief's "no sibling `repo-layer/`" does not fit; an installed payload
  has `repo-layer/` and `engine/` but never `src/` or `tools/generate.py`]. In that case the one final line keeps its format and NEXT names
  `python3 plugins/claude-code/agentive/payload/engine/apply.py <same arguments>` (run `python3 tools/generate.py` first if `plugins/` is missing). Elsewhere (an installed
  payload whose index is gone: a broken plugin) the NEXT stays `reinstall the agentive plugin (claude plugin update agentive@<name>)`.
- **Entry points**. `_load_index` is the only reader of `index.json` (`prove.py`, `cisetup.py` and the other modules take no part; `apply.py` is the one entry point of the
  engine). The other reinstall NEXT in `apply.py` (CI step source unreadable, `ci_llm_step`) reads a file under `src/engine/ci/`, which exists in the template source, and is unchanged.

## 34. Handoff, requirements and Routines handoff (2026-11-02)

User decision (2026-11-02, [user]): "1번 2번 sonnet 5.5 이용해서 작업하고" (1 = the recommendations for the open handoff and requirements-document
decisions, 2 = the Routines handoff option (a) now, (b) later). The design records are `decisions.accepted[CTXREINJECT]`, `[HANDOFFFILL]`,
`[REQSTATED]`, `[OPENMARK]`, `[OPENWORK]`, `[HANDOFFREPORT]`, and the entries `handoff_resume_gate`, `handoff_pr_comment` in `spec.deferred`. Every number
below (caps, 20 characters, line counts) is [설계 판단] and was not measured on a real repository. One local commit per step; hooks paths were touched
with the user's knowledge.

- **Compact re-inject (CTXREINJECT)**. Defect fixed first: `compact_text` in `session-start-notice.py` joined its parts and cut the text at 10,000 characters, so a
  long plan.md cut the HANDOFF line, the last part, first; it also took the first handoff line (possibly `HANDOFF_OTHER`) as the HANDOFF line. Now five sections in
  this order, each within its own cap: HANDOFF line 600, failed verbs 800 (4 lines), open checkboxes 3,000, `[user]` decisions 2,000, git log 1,500; a long list ends in
  `... N more`. The failed-verb lines are built from `.agents/state/verify.json` (`ok`, `failures`, `log`): that file does not keep the wrapper's fail line, so no exit code
  or NEXT is shown; the header line says whether the record is of the current work tree (`verb.fresh_for`) or an earlier one. Tests: `tests/hooks/test_handoff_req.py`
  (`CompactTextTest`, `CompactFailAndDecisionsTest`).
- **Handoff placeholders (HANDOFFFILL)**. `handoff.is_placeholder` judges form only (empty, `(agent: ...)`, TBD/TODO/n/a, a single `<...>`, no letter or digit; with a
  length rule of 20 characters for the writers that take arguments). `handoff.sh` and `python3 -m agentive.handoff write` print
  `AGENTIVE_HANDOFF=stop reason=placeholder fields=<names>; NEXT=...` and exit 2 without writing. On NO-PROGRESS the Stop that follows the one continuation calls
  `handoff.fill_placeholders` before it clears `handoff_requested`: a field that is still a placeholder (never by length: a short real value stays) becomes
  `(script) ` plus the failing verbs, the last `AGENTIVE_FAIL` line of `stop.json` `last_fail_lines` with its NEXT, and the blocked command when the handoff has one. SessionStart
  adds `agent_notes=missing` to the HANDOFF line. No schema field (`additionalProperties: false`); only the schema description changed. Tests:
  `tests/hooks/test_handoff_req.py` (`PlaceholderUnitTest`, `HandoffPlaceholderTest`).
- **Requirements as bullets (REQSTATED)**. `/chain-intent` step 5 and the intent.md template: every requirement the user stated is one bullet under `## Proposed outcome`, in the
  user's terms, none merged or dropped, nothing asked twice. `/chain-spec` step 3 and the spec.md template: the section `## How each requirement and constraint in the intent is met`
  maps every requirement bullet and constraint to an R<n>, a design item or `(not adopted: <reason>)`. The check stays the review check (the RV5 correctness reviewer on a spec
  PR reports an unmapped bullet as an important finding; `agents/correctness-reviewer.md`); no deterministic lint (user decisions B2_final and A7 of 2026-10-27 stay). Always-loaded
  text is unchanged. 미검증: that the chain-entry triage answers yes for a message that is a list of requirements. Tests: `tests/content/test_skills.py` (`ChainRequirementText`, `Templates`).
- **Open questions as `[OPEN]` (OPENMARK)**. spec.md `## Open questions from the intent`: an unanswered question is the bullet `- [OPEN] <question>` (`agentive.common.OPEN_BULLET`; the
  `grep -Eq` pattern of `chain-stage-check.sh` is the same rule, a test compares them). A headless or Routines spec run marks the question, writes the handoff, finishes the spec and opens
  the spec PR: the draft `## Risk` lists the `[OPEN]` bullets (`prbody.risk_section`, spec stage only). The Stop hook (`stop-verify.py`, `open_question_text`) continues once
  with no human present when the branch is `spec/<slug>`, spec.md has an `[OPEN]` bullet and the branch has no handoff (`stop.json` `open_question_nudged`, a new field of
  `stop-state.schema.json`; reset by a new start). `/chain-plan`'s `stage-check.sh` stops while the default branch's spec.md still has one. An agent that deletes the marker unanswered
  is not detected. 미검증: the Codex Stop behavior in a headless run, and that a Routines run can run `handoff.sh` and `/pr`. The view texts of the spec and plan stages were revised
  (and the spec writer's row now names the requirement mapping of REQSTATED). Tests: `tests/hooks/test_handoff_req.py` (`OpenQuestionStopTest`), `tests/content/test_stage_check.py`
  (`OpenMarker`), `tests/content/test_prbody.py` (`test_openmark_*`).
- **Open work at startup (OPENWORK)**. `session-start-notice.py` `open_work_text`: on startup and resume, on a branch that is not the default branch, the open checkboxes of the
  plan.md files the branch changed (same lookup as the compact re-inject) come as `OPEN_WORK=<plan path>; NEXT=continue with the first open checkbox` plus the checkboxes, at most 1,500
  characters, with or without a handoff file or a human. Recorded without code: the plan.md `## Handoff` section is rejected (conflicts with `spec_stage.files.storage` of 2026-10-15 and the
  plan_ledger removal of 2026-10-27), posting every handoff as a PR comment is deferred (`spec.deferred.handoff_pr_comment`), the resume gate is deferred (`spec.deferred.handoff_resume_gate`,
  precedent 0 of 12). README Daily workflow has the one human line about changing machine. Tests: `tests/hooks/test_handoff_req.py` (`OpenWorkTest`).
- **Handoff line at the end of a run with no human (HANDOFFREPORT)**. Checked in the code first: the claim in `spec.handoff.writers` that a Routines run also puts the handoff in the final
  message and in a PR comment had no code, no instruction and no test; the guard deny reason (agent only) carried the path. Built (option a): `handoff.report_line` makes
  `HANDOFF=<path> reason=<r> blocked=<cmd|none> next=<step>`; the Stop hook puts it in front of its systemMessage (DONE, RED-UNCHANGED, PENDING, CAP) or as the whole message of the Stop after the
  NO-PROGRESS continuation, only with no human present and only for a handoff whose `created_at` is not before `stop.json` `started_at` (new field, set by a new start); the guard deny reason adds
  "end your final message with this line: ..."; `/pr` step 6 reports the line too. Claude Code only for the systemMessage (the Codex Stop output was not checked). Not built, deferred
  (`spec.deferred.handoff_routines_pr_comment`, until UV-40 is verified): the PR comment of a Routines run; the design sentence that claimed it was removed so that design and code agree.
  미검증: that the Stop systemMessage is kept in a Routines transcript, a cloud session UI or the `claude -p` JSON result (the line is asked of the final message too). Tests:
  `tests/hooks/test_handoff_req.py` (`HandoffReportTest`).

## 35. Reply and install-path fixes: a `fixed` reply, the reply-run artifact, `--tool`, the `/chain-intent` branch (E2E-59 to E2E-61, 2026-11-02)

User decision (2026-11-02, [user]): "차례대로 진행해줘 sonnet 5.5 서브에이전트 이용해서" (order: fix E2E-60 and E2E-61, then the `--tool` removal and the verified `/chain-intent`
changes). Evidence: the end-to-end repo `xrpz/agentive-e2e` PR #6 and PR #8 (REST reads of the review comments, the check runs and the workflow job logs). The design is
silent on every choice below unless a design node is named, so each choice is [설계 판단]; `미검증` marks what the logs could not show.

- **E2E-60 (high): a `fixed` reply never unblocked the gate**. Design (`severity_blocking.reply_line.fixed`, FINDINGID `fixed_rule`): a `fixed` reply is a claim; it is
  confirmed only by the fixed rule (new head, a reviewer output, the anchor line gone or changed, or a reviewer's `fixed` with evidence; response source `next_round`). That
  part of the code was right. Root cause: `findings.attach_responses` ran after `carried_findings` and replaced the proof (`response.source` `next_round`) with the reply line
  (`kind open`, source `reply_line`) of the same finding, so `set_blocking` and `rv8._answered` saw an unproven claim and blocked with `unanswered_finding`, while the table
  printed `fixed (reply_line)`. Reproduced on the unchanged code with a round-two fixture (`tests/review/test_pipeline.py`): README line changed + `fixed` reply -> block.
  PR #6 had exactly this (anchor line `Python 3.11 or newer ...` changed by the fix commit `dd70c72`; the reply came 14 seconds after the push). PR #8 (a test-gap finding fixed by a new
  test; its anchor line `def test_short_password_rejected():` was not changed) needs the reviewer's `fixed` evidence, or the label `agentive:full-review`; whether the round-2
  reviewer returned an `open_findings` entry is 미검증 (the review artifact cannot be downloaded through the proxy), PR #8 was merged by a human at 13:41:59Z.
- **Fix**. `attach_responses` skips a `fixed` reply for a finding whose response is already `next_round` (a `wrong`, `intentional` or `not_important` reply still wins, so
  `finding_disputed` still sends a contradiction to a human). A `fixed` reply with no proof stays an open claim and still blocks (the design is not loosened). The comment now says so:
  the table cell is `fixed claimed, not confirmed (reply_line)`, the reason is "has no confirmed fix or answer yet (the author replied "fixed", but the review has not confirmed it; ...)", the
  verdict is "blocked — the fix for N AI finding is not confirmed" and What to do names the way out (make the change reach the flagged line and push, add the label
  `agentive:full-review`, or answer with a reason). Tests: `tests/review/test_findings.py` (`test_a_fixed_reply_never_replaces_a_fix_the_review_proved`), `tests/review/test_pipeline.py`
  (`test_fixed_reply_*`: the #6 shape, the #8 shape with the reviewer's evidence, the unproven claim and its wording, an untrusted author's reply).
- **E2E-61 (medium): a reply run failed `agentive-independence` with "Artifact not found"**. Cause (PR #6 run 41454284605, PR #8): a reply in a finding thread starts a
  `pull_request_review_comment` run of `agentive-review.yml`, which has no ai-review job, so the artifact `agentive-review-<head sha>` belongs to the `pull_request` run of the same
  head. The reply run asked `actions/download-artifact` for it in its own run (that action reads only the current run unless it gets `run-id` and `github-token`), then
  `independence` looked it up by name through REST (13:35:26, 5 seconds after the reply), while the ai-review jobs of the same head were still queued or just started (the artifact appeared
  about a minute later). The
  independence job and the gate of the reply run therefore saw no CI review and failed; the later gate of the push run decided correctly, so the final state was right but the
  checks were red and the comment said "AI review job left no report" for a while.
- **Fix** [설계 판단]. (1) `github.fetch_review_artifact` = the existing newest-by-name lookup plus a bounded wait (`WAIT_SECONDS` 900, step 15, `AGENTIVE_REVIEW_WAIT` overrides, 0 turns it off):
  it polls only while a `pull_request` run of `.github/workflows/agentive-review.yml` for the same head has not completed (`review_run_in_progress`), and gives up at once when none is. Only a
  `pull_request_review_comment` run waits (`wait_seconds`); push, edited and label runs have the artifact in their own run. `cmd_independence` and `ci_context --fetch-prior` (the gate)
  use it. (2) In a reply run the two `actions/download-artifact` steps are skipped (`if: github.event_name != 'pull_request_review_comment'`): they could only fail with an error
  annotation. A workflow-level `concurrency` group per PR was rejected: "Any previously pending job or workflow in the concurrency group will be canceled", so a reply could cancel the
  pending push run that makes the artifact (and the design already records that effect for ai-review, `convergence.label_trigger.concurrency`, 미검증 at run time). Sources:
  https://docs.github.com/en/rest/actions/workflow-runs (list workflow runs: `head_sha`, `event`, `status`, run `path`), https://docs.github.com/en/rest/actions/artifacts (list artifacts, `name`),
  https://github.com/actions/download-artifact (README: other runs need `run-id` and `github-token`), https://docs.github.com/en/actions/using-jobs/using-concurrency (pending runs are canceled).
  Cost: a reply run that arrives during the push run's AI review waits for it (a runner is busy for a few minutes). 미검증 at run time: the wait itself (the unit tests use stand-ins for the
  clock and the REST calls), and the order of the check runs of the same name when a reply run and the push run end close together (unchanged from before). Tests:
  `tests/review/test_e2e_60_61.py`, `tests/ci/test_workflows.py` (`test_a_reply_run_looks_the_review_artifact_up_by_name_instead_of_in_its_own_run`).
- **E2E-59 (low): `--tool` removed from skill texts** (user 2026-11-01, "이렇게해": remove `--tool` from the skill texts and the NEXT lines, rely on auto-detect, keep the record
  field). Cause: the precommit NEXT repeated the command with `--tool claude_code` (`_rerun` echoes the flag as typed, `claude` is read as `claude_code`), while the skill said
  `claude|codex`. Changes: `precommit-review` step 2 and `pr` step 3 no longer pass the flag; `agentive-review precommit` and `pr-body.sh draft` both detect the tool with
  `attest.detect_tool` (flag, then `AGENTIVE_TOOL`, then `CLAUDECODE`, else `other`; `prbody.review_block` ignored `AGENTIVE_TOOL` before). The flag stays accepted and the record
  field `tool` stays; a NEXT line repeats a flag only when the caller typed it. Not changed on purpose [설계 판단]: the engine's `--tool claude|codex` in the plugin skills
  `init`, `update`, `reprove` (it records `framework.init_tool`; the engine has no detection and defaults to claude, docs/impl/B.md) and the `--tool codex` of the Codex hook commands
  (`dot-codex/hooks.json`; hook scripts, not typed by an agent). 미검증: there is no Codex environment variable in the detection (`CODEX_THREAD_ID` and `CODEX_CI` appear in the
  environment of a Codex session in https://github.com/openai/codex/issues/22124, an issue, not an official reference), so a Codex run without `AGENTIVE_TOOL=codex` is recorded
  as `other` (honest, never guessed); adding a Codex variable needs a source from the Codex docs and a user decision. Tests: `tests/content/test_e2e_batch_g.py`
  (`E2E59ToolFlagText`), `tests/content/test_prbody.py` (`test_e2e_59_*`), `tests/review/test_pipeline.py` (`test_e2e_59_*`).
- **`/chain-intent`: the branch after the human agrees, and resume** (verified on throwaway repos with a bare origin, 2026-11-02; the design has no branch step in `spec_stage.chain.interview`,
  only the name `intent/<slug>`, so no design node changes). Step 2 is now: fetch `+refs/heads/intent/<slug>:refs/remotes/origin/intent/<slug>` (an explicit refspec; "couldn't find remote ref"
  means the remote has none), `git rev-parse --verify --quiet` on the local and the remote branch, each in its own Bash call (the headless allowlist has `git fetch *`, `git switch *`
  and `git rev-parse`, not `git ls-remote` or `git branch`). A branch found: resume on it (`git switch intent/<slug>`, or `git switch -c intent/<slug> --no-track origin/intent/<slug>` when only the
  remote has it), read intent.md, go on from step 3 with it as the draft; a correction is one more commit on that branch, an open PR is refreshed with `/pr` (which rewrites `## Verification`
  only) plus `/pr --explain` when the correction changes the proposed behavior. No branch: `git switch --detach origin/<DEFAULT>` so the interview reads the fresh default branch, and step 6
  (after the human agrees) creates the branch with `git switch -c intent/<slug> --no-track origin/<DEFAULT>`. Reasons found by running it: a branch made at step 2 stays behind when the interview
  is abandoned and the next `/chain-intent <slug>` fails at `switch -c` (stage-check does not see it); with only the remote branch `switch -c ... origin/<DEFAULT>` succeeds silently, forks from
  the default branch and the push is rejected as non-fast-forward; moving the branch to step 6 without the detach made the interview read an old local default branch (stage-check fetches
  `origin/<DEFAULT>` but does not update the local one). [설계 판단] the resume branch is made with `--no-track` like every other branch the skills create (AGENTS.md of the installed layer
  allows that form; `/pr` sets the upstream). The sentence "keeping the interview going is normal" was considered and not added: steps 4 and 5 already run to the human's agreement. 미검증: the
  commands under a real `claude -p` allowlist (read from the code, not run); gh-dependent parts (finding the open PR, `/pr` refresh and `--explain`); a local branch that is behind its remote
  when both exist (`git switch` keeps the old local tip; a `git merge --ff-only origin/intent/<slug>` may be needed); whether a headless or Routines run that resumes an intent PR a human edited
  keeps the human's edits. Tests: `tests/content/test_chain_intent_branch.py` (skill text and the git sequence on a bare origin), `tests/content/test_e2e_batch_g.py` (the `--no-track` count is 5).

- **E2E-63 (low): a PR merged while its run is still going** (user 2026-11-03 "low 3건 고치고 push하자"; cause read from the job logs of run 41468215179 of the end-to-end repo, 2026-11-02). The install PR #1 was merged 40 s after it opened.
  The ai-review job was green but claude-code-action had skipped its run ("Skipping action due to workflow validation": the install PR adds `agentive-review.yml`), so no CI review existed. Before the merge,
  `llmstate.workflow_skip` tells that case by comparing the workflow blob of the run's merge commit with `origin/<default>`. The independence job (15:06:46Z) and the gate (15:06:53Z) started after the merge
  (15:06:32Z): the default branch already held the same file, the blobs were equal, `workflow_skip` returned None, and independence failed ("no review produced by the CI job for the PR head") and the gate blocked
  (`failing_required_check: agentive-independence`). The failure was not a defect of the review, only of a run whose PR no longer exists as an open PR. Change [설계 판단]: `cmd_independence` and `cmd_gate`
  read `state` / `merged` of the PR (REST, already in `gh.pr_context`) first and, for a merged or closed PR, print one line (`AGENTIVE_INDEPENDENCE=skipped reason=pr_merged|pr_closed` /
  `AGENTIVE_GATE=skipped reason=pr_...`; `NEXT=none`) and exit 0, before any artifact lookup, wait, comment or merge side effect. An open PR is unchanged. A closed PR that is reopened starts a new run (`reopened`).
  Not changed: the workflow-skip comparison itself (it is right while the PR is open). Test: `tests/review/test_e2e_63.py`. 미검증: the step order of a real merged-PR run (read from the logs and the code, not re-run).
- **E2E-64 (info): `Checks: lint not reported · test not reported` in the first summary comment** (user 2026-11-03; cause read from the saved gate logs of PR #2, gate jobs 125357318791 / 125357450110, in the
  end-to-end clone). The two runs of the same head were an `edited` run (the /pr publish edited the body seconds after the PR was opened) and the `opened` run. `outer-verbs` and `dependency-review` are skipped on
  `edited`, so the edited run's gate had `OUTER:` empty (`AGENTIVE_CHECKS=agentive-independence:pass`) and its ai-review job had no `agentive-outer-verbs-<head>` artifact in its own run; the review artifact of the
  head (`--fetch-prior`) came from that same edited run, so `rv1.checks` held no lint and test: `required checks: lint (not reported), test (not reported)`. The gate did not block on it (only `fail` blocks), so a
  failed lint could not show in that run. Change [설계 판단]: when `--fetch-prior` finds neither a `--outer-results` file nor lint/test/typecheck/build in the checks or the earlier report, `ci_context` looks up the push
  run's artifact `agentive-outer-verbs-<head sha>` (REST list artifacts by name, like E2E-61), waiting up to `WAIT_SECONDS` (override `AGENTIVE_REVIEW_WAIT`) for an edited, labeled or reply run while another
  pull_request run of the head is going, and never for the run itself (`GITHUB_RUN_ID`); the results are added to the checks and to the verifier's outer results. A check still without a result is shown as `pending`
  (comment Checks line and the `required checks:` detail), not `not reported`. Not changed: a missing required check does not block by itself (unchanged; the push run of the head decides with the real results).
  Tests: `tests/review/test_e2e_64.py`, `tests/review/test_e2e_batch_d.py`. 미검증: the wait against a real push run (unit tests use stand-ins), the artifact name in a repo whose outer-verbs ran on a re-run attempt.
- **E2E-65 (info; = E2E-57 and E2E-62): Explainer optional for an uncertain local tier** (user 2026-11-03, decision EXPLAINOPT). `/pr` draft printed `TIER=uncertain EXPLAINER=required` for a small feature PR that CI
  classified tier 1, so the agent wrote an Explainer with two diagrams that CI does not read. Smallest change [설계 판단]: `EXPLAINER=required` only for a predicted tier 2; `TIER=uncertain` gives `EXPLAINER=optional` and no
  skeleton. Safety is unchanged: RV8 `explainer_missing` still blocks a tier 2 PR without a valid Explainer, with next action `/pr --explain`; `/pr-feedback` step 4 already answers `explainer_missing` with `/pr --explain`
  (`skills/pr-feedback/SKILL.md`), and `prstate` NEXT says the same. Changed texts: `prbody.py` (`need`, `UNCERTAIN_SOURCE`, docstring), `skills/pr/SKILL.md` step 4, design `pr_explainer.when_written`, `pr_skill.writes`,
  EXPLAIN design_judgments, new decision EXPLAINOPT. Tests: `tests/content/test_prbody.py` (the two uncertain cases now expect optional and no skeleton). Cost: a PR that CI calls tier 2 after an uncertain local tier
  takes one extra round (block, `/pr --explain`, body edit re-runs the gate).
