# D: review pipeline + CI + tests (status, contracts, decisions)

Owner D (docs/IMPLEMENTATION.md §7). Status: DONE for v0.1 (2026-10-28). Units: `src/units/review.yaml`.
Run-time modules `.agents/lib/agentive/review/**` and `.agents/lib/agentive/lint/**` are files of A's unit `lib.agentive`
(one module = one owner); their headers say `owner D`.

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
- `precommit [--run interactive|non_interactive] [--human-confirmation obtained|not_obtained]`: profile precommit
  (RV1 from `.agents/state/verify.json` when its head_sha is HEAD, RV2, RV5 `profiles.precommit.reviewers`) on
  `git diff --cached`; writes `.agents/state/precommit-review.json` (schema precommit-review) and
  `.agents/state/review-report.json`; last line `AGENTIVE_PRECOMMIT=commit | blocked | confirm ...; NEXT=...`.
  Work dir `.agents/state/review/precommit-<index tree>`: a rerun with `--human-confirmation obtained` reuses the review.
  `obtained` is refused when a no-human signal is set (AGENTIVE_HEADLESS=1, CI, GITHUB_ACTIONS). C's precommit-review
  skill uses exactly this.
- `ci <pr>` (ai-review job), `gate <pr> [--apply]` (agentive-review-gate), `independence <pr>`, `outer-verbs`,
  `checks`, `merge-group --head-ref`, `eval --base --head --work` (reviewer eval runs). `--pr-json` / `--comments-json`
  replace gh api reads (tests).
- LLM task protocol (tasks.py): `AGENTIVE_REVIEW_TASK id=<task> agent=<agent> input=<path> output=<path>` lines, then
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

### `.agents/bin/agentive-lint`: `ci` | `size provenance check-tags persist skills lock links` | `rubric --effective`
One `LINT <sub> <path>[:<line>]: <what>. NEXT: <fix>` per problem, then `AGENTIVE_LINT=ok|fail:<subs>; NEXT=...`.
C's verify skill calls `agentive-lint lock`.

### `.agents/bin/agentive-reviewer-eval`: `detect | run | score | comment` (see the script header).

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
    unavailable -> RV2 `critical_cve_unchecked` when a package file or lockfile changed.
13. agentive-lint: SKILL.md tokens = bytes/4; Codex combined size per AGENTS.md path; check-tags needs a fix sentence
    (>= 3 words) except distill markers `noise-*`/`eval-*`, ignores backtick text and generated files; links on when
    `.agents/link-check.txt` (the ignore list) exists; side-effect detection by command patterns.
14. Reviewer eval: the reviewer config overlaid on the case head = `.agents/agents`, `.claude/agents`, `.codex/agents`,
    `.agents/bin/agentive-review`, `.agents/lib/agentive`, `code_review.md`, `REVIEW.md`, `.agents/framework.yaml`,
    `.agents/rubric.yaml`, `.agents/decisions.yaml`; eval runs read policy from the working tree; a failed run fails
    that case run; the library of the pipeline counts as a reviewer change with the script. The headless command adds
    `Bash(.agents/bin/agentive-review *)`, read-only git commands and `Task,Agent` to the standard tool list (the
    review run needs them).
15. Agent-run self-test: stacks ts and py; triggered by changes under src/engine, src/plugin, src/repo-layer,
    src/units, tools/generate.py, tools/bundles.yaml, fixtures, tests/sample_repo.
16. Fixtures: Go has no go.sum (no dependencies); the Java `gradle.lockfile` is hand-written (Maven Central was not
    reachable from the build sandbox) — 미검증 until a `gradle test` run.

## 4. Requests to A

1. `tests/generator/test_generate.py` fails now that B/C sources exist: `test_shared_unit_regions` creates
   `src/repo-layer/AGENTS.md.regions` in its copy (already exists: FileExistsError) and `test_source_errors` expects
   `src/engine/stray.py` to be orphaned, but B's unit `eng.apply` covers `src/engine/`. Not D code; the tests need
   fixture paths that no unit owns.
2. docs/IMPLEMENTATION.md §7 D: note that D's run-time modules live inside A's `lib.agentive` unit (as above).
3. `.github/workflows/ci.yml` job runtime-python-floor: also run `tests/review` on Python 3.9 (it passes locally on
   3.9.25: 48 tests; the review scripts are run-time code).

## 5. Cross-owner notes

- C: `pr-body-agentive-review.schema.json` x-agentive-chain.next_actions still name `/spec spec <slug>` and
  `/spec plan <slug>`; the scripts print the current skills (`/chain-spec`, `/chain-plan`). Design text, not code.
- C: the default review-rules block validates (review-rules schema) and parses with ayaml; its hooks globs include
  `.agents/hooks/**`; permissions adds `.agents/guard.yaml` (the built-in fallback uses the same list).
- B: renders `.agents/agents/*.md` (symlink to `.claude/agents/`, `.codex/agents/*.toml`) as expected; frontmatter keys
  used: name, description, tools, model: inherit.

## 6. 미검증 (unverified, to check on a real repository)

- claude-code-action@v1 inputs `prompt`, `claude_args` (official action docs as mirrored), `id-token: write` need.
- dependency-review-action output `vulnerable-changes`; the check run of a pull_request_review_comment run attaches
  to the PR head commit; `gh pr merge --auto` with GITHUB_TOKEN (UV-16); REST reply bodies with author_association
  (UV-37); upload-artifact `include-hidden-files`.
- Subagent tool names `Task` / `Agent` in `--allowedTools`; `claude --plugin-dir` for the agent-run self-test.
