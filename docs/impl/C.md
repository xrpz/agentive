# C: repo-layer content + work-loop skills (status, contracts, decisions)

Owner C (docs/IMPLEMENTATION.md §7). Status: DONE for v1 (2026-10-28); open items in §6.
Units: `src/units/content.yaml`. Tests: `tests/content/` (75 tests; run `python3 -m unittest discover -s tests/content`).

## 1. What C ships (install path <- source)

| install path | source | unit |
|---|---|---|
| `AGENTS.md#context`, `AGENTS.md#path-rules` | `src/repo-layer/AGENTS.md.regions/{context,path-rules}.md` | rl.agents-md |
| `CLAUDE.md#imports` | `src/repo-layer/CLAUDE.md.regions/imports.md` | rl.claude-md |
| `code_review.md#rules` (agentive-review-rules block) | `src/repo-layer/code_review.md.regions/rules.md` | rl.code-review |
| `REVIEW.md` (`when: review.managed_code_review_copy`) | `src/repo-layer/REVIEW.md` | rl.review-copy |
| `.gitignore#state` | `src/repo-layer/dot-gitignore.regions/state.txt` | rl.gitignore |
| `.agents/decisions.yaml` (confirm_scope review_config) | `src/repo-layer/dot-agents/decisions.yaml` | rl.decisions |
| `.agents/rubric.yaml` | `src/repo-layer/dot-agents/rubric.yaml` | rl.rubric |
| `.agents/rules/structure.md` (`when: rule_packs.boundary`) | `src/repo-layer/dot-agents/rules/structure.md` | rl.rules.structure |
| `docs/adr/0000-template.md`, `docs/{system,features,guides}/.gitkeep` | `src/repo-layer/docs/` | rl.docs |
| `.agents/skills/{chain-intent,chain-spec,chain-plan,pr,pr-feedback,distill,verify,precommit-review,janitor}/` (SKILL.md, agents/openai.yaml, templates/, scripts/) | `src/repo-layer/dot-agents/skills/<name>/` | rl.skill.<name> |
| `.agents/skills/chain-{intent,spec,plan}/scripts/stage-check.sh` (one source, three installs) | `src/repo-layer/dot-agents/skills/chain-stage-check.sh` | rl.skill.chain-stage-check |

Run-time library modules owned by C (inside A's unit `lib.agentive`, header `agentive-unit: lib.agentive`):
`agentive/gitgh.py` (git and `gh api` helpers, default branch with the ls-remote fallback, no-human signals),
`agentive/prbody.py` (/pr body), `agentive/prstate.py` (/pr-feedback state), `agentive/distillq.py` (/distill queue).

## 2. Contracts C offers

- **review-rules block (C -> D)**: fenced ```` ```yaml agentive-review-rules ```` in the region `code_review.md#rules`,
  valid against design/schemas/review-rules.schema.json (tested). Defaults = design RV2 default_globs (incl. billing,
  payment), RV2.confirm_scope default_globs plus `.agents/hooks/**` (hooks) and `.agents/guard.yaml`,
  `.agents/custom/.agents/guard.yaml` (permissions), RV4 defaults. Customization: a block with the same info string
  in `.agents/custom/code_review.md` (init_apply custom_overrides text_files).
- **agentive-review PR-body block (C writes, D reads)**: info string `yaml agentive-review`, top key `agentive-review`;
  /pr writes `implementer`, `reviews` (the precommit reviewer from precommit-review.json `reviewer`), `u2`
  (confirm_scope hits recomputed over the whole PR diff; `obtained` only when precommit-review.json says obtained in an
  interactive run and covers every hit), `chain {slug, stage}` on chain PRs, optional `hotfix_of`. Never `pipeline`.
  Validated against pr-body-agentive-review.schema.json in tests. Body headings: `## Explainer` first when tier 2 is
  predicted or uncertain, then `## Verification`, `## Risk`, `## How to revert`, then the block.
- **Script output lines**: stage-check `AGENTIVE_CHAIN=ok:<stage>:<slug> DEFAULT=<b>` / `AGENTIVE_CHAIN=stop:<stage>:<slug> NEXT=<action>`
  (exit 1); pr-body `PR_BODY=<file> MODE=new|refresh|explain PR=<n|none> TIER=<0|1|2|uncertain> EXPLAINER=required|optional CHAIN=<slug:stage|none> U2=<..>; NEXT=..`,
  `AGENTIVE_PR=stop reason=<r>; NEXT=..`; pr-state `PR_STATE=ok|stop ...; NEXT=..`; queue-read `QUEUE ... ; NEXT=..`;
  janitor gate `GO <kind>` (0) / `SKIP <kind>: #N open` (1) / `AGENTIVE_FAIL ...` (2).

## 3. Seams used

- B render keys (docs/impl/B.md §1): `render.orientation`, `render.verbs`, `render.boundary_line`,
  `render.service_facts`, `render.path_rules` (block placeholders, one per line).
- B `when` grammar: `review.managed_code_review_copy`, `rule_packs.boundary` (dotted key, absent = false).
- B `agentive.handoff.write(root, "open_question", failure_reason, next_step)` from /chain-spec (headless open question).
- D `.agents/bin/agentive-review precommit [--human-confirmation obtained]` (writes precommit-review.json; exit 3 =
  LLM tasks, run in the forked skill), D gate job log line `AGENTIVE_GATE=<decision> tier=<t> reasons=<codes>` (read by
  pr-state.sh from the gate job log), D finding marker `<!-- agentive-finding: <id> -->`.
- D `.agents/bin/agentive-lint lock` (verify step 1), B `.agents/bin/verb verify`.

## 4. Requests to A

1. `tests/generator/test_generate.py::test_shared_unit_regions` creates `src/repo-layer/AGENTS.md.regions` without
   `exist_ok` and a unit with id `rl.agents-md`: both now exist as C's real unit, so the test errors (FileExistsError)
   and would collide on the unit id. Use a throw-away region dir and unit id (or remove C's unit in the temp copy).
2. Same file, `test_source_errors`: `src/engine/stray.py` is now covered by B's `src/engine/` unit, so the expected
   "belongs to no unit" error does not appear (an orphan outside every unit root is needed).

## 5. Requests to B

1. Add install-time key `render.code_review_copy` (block): the exact bytes of the rendered `code_review.md`. The
   `REVIEW.md` template is that one placeholder with no trailing newline, so the copy is byte-identical.
2. `when: rule_packs.boundary`: say which run sets it (the init skill proposes the boundary pack when it sees two or
   more modules, spec.one_way.boundary_lint; the human answers; then `--values`?). v1 never installs the pack, which
   matches B's v1 `render.boundary_line` (always empty).
3. Optional: a CLI for the handoff writer (`python3 -m agentive.handoff write --reason open_question ...`); /chain-spec
   now uses `python3 -c` with `agentive.handoff.write` (tested in tests/content/test_stage_check.py).

## 6. Design judgments [설계 판단] (also in the file headers or SKILL text)

1. stage-check.sh source lives at `src/repo-layer/dot-agents/skills/chain-stage-check.sh` (not inside a skill dir, so
   the three skill directory units stay disjoint); the stage comes from the installed directory name; the no-human
   stop of the plan stage is in the script; a failed `git fetch` falls back to the local `origin/<default>` with a NOTE.
2. confirm_scope permissions globs add `.agents/guard.yaml` and its override (guard.yaml renders permissions).
3. `classify` holds only `whitespace_only_is_format: true`; file-type rules for RV3 stay D's built-ins.
4. AGENTS.md#context adds a `## Project (generated)` heading, a verb-wrapper line, a commit-procedure line (U2 and the
   precommit order) and the `.worktreeinclude` guidance line (context_management.worktree).
5. spec.md template ends with `## Decisions` (spec_stage.provenance_tags names spec.md ## Decisions).
6. ADR template file `docs/adr/0000-template.md` (Status, Date, Decided by, Context, Decision, Alternatives
   considered, Consequences); `docs/{system,features,guides}/.gitkeep`.
7. CLAUDE.md `# Compact instructions` content (plan path + open boxes, AGENTIVE_FAIL lines, HANDOFF line, [user]
   decisions, next action), mirroring the compact re-inject.
8. /pr: `pr-body.sh draft|check|publish`; `<!-- agent: ... -->` and `%% agent:` markers must all be replaced before
   `check` passes; local tier prediction (RV2 globs, built-in protected_check_config paths, managed-region body change,
   chain_approval, U2, >100 files, large changes, agentive-check tags, then rule-decided classes) -> 2 | 0 | 1 |
   uncertain; implementer session id: `--session-id`, else `AGENTIVE_SESSION_ID`, else stop.json `session_id`, else
   `unknown`; verify.json reused only when head_sha = HEAD, worktree_dirty false and the tree is clean now; an open PR
   gets only `## Verification` (`--explain`: only `## Explainer`) through gh api PATCH; the default revert line.
9. /pr-feedback: full state in `.agents/state/pr-state-<n>.json`, failing job logs in `.agents/state/logs/`; gate
   reasons from the gate job log line, else from the check-run output text; a human thread = a root review comment
   without a finding marker from a non-Bot user.
10. /distill queue: an entry is ready when its transcript is >= 30 s old or missing; `--done <session_id>...` removes
    handled entries (no ledger file); invalid lines are reported, never removed.
11. janitor: gate.sh exit codes 0 GO / 1 SKIP / 2 error; owner/repo parsed from the origin URL (gh's `{owner}/{repo}`
    placeholders as fallback); branch `janitor/<id>-<YYYYMMDD>`; label via gh api issues labels.
12. Codex argument reading (UV-23): every skill uses `$0`/`$ARGUMENTS` and says: when it reads literally `$0` or is
    empty, take the value from the user's message.
13. decisions.yaml `decided_at` written as `<date>T00:00:00Z` (schema format date-time; the design gives dates only).

## 7. Open issues for the main agent

1. Design/schema text: pr-body-agentive-review.schema.json `x-agentive-chain.next_actions` still names the retired
   `/spec spec <slug>` and `/spec plan <slug>`; the skills are `/chain-spec <slug>` and `/chain-plan <slug>` (D's rv1.py
   already uses the new names). Needs a design fix (C does not edit design/schemas).
2. Headless standard command (context_management.headless.standard_cmd) allows `Bash(.agents/bin/verb *)`, git and
   `gh api` only; with `--permission-mode dontAsk` the skill scripts (`sh .agents/skills/<skill>/scripts/*.sh`) of
   /pr, /pr-feedback, /distill and /janitor, and `git switch`/`git fetch` used by the chain skills and stage-check.sh,
   would be denied in headless runs. The allow list needs `Bash(sh .agents/skills/*)`, `Bash(git switch *)` and
   `Bash(git fetch *)` (or B's settings render them) - design gap, not fixed here.
3. code_review.md is not in any U2 confirm_scope glob (design default); RV2 protects it as check config (tier 2).
