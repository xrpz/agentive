# Repo-layer content and work-loop skills, area C (contracts and decisions)

Area C of docs/IMPLEMENTATION.md §7. Units: `src/units/content.yaml`. Tests: `tests/content/`
(`python3 -m unittest discover -s tests/content`). Open items are in §6.

## 1. What C ships (install path <- source)

| install path | source | unit |
|---|---|---|
| `AGENTS.md#context`, `AGENTS.md#path-rules` | `src/repo-layer/AGENTS.md.regions/{context,path-rules}.md` | rl.agents-md |
| `CLAUDE.md#imports` | `src/repo-layer/CLAUDE.md.regions/imports.md` | rl.claude-md |
| `code_review.md#rules` (agentive-review-rules block) | `src/repo-layer/code_review.md.regions/rules.md` | rl.code-review |
| `REVIEW.md` (`when: review.managed_code_review_copy`) | `src/repo-layer/REVIEW.md` | rl.review-copy |
| `.gitignore#state` | `src/repo-layer/dot-gitignore.regions/state.txt` | rl.gitignore |
| `.agents/rubric.yaml` | `src/repo-layer/dot-agents/rubric.yaml` | rl.rubric |
| `.agents/rules/structure.md` (`when: rule_packs.boundary`) | `src/repo-layer/dot-agents/rules/structure.md` | rl.rules.structure |
| `docs/adr/0000-template.md`, `docs/{system,features,guides}/.gitkeep` | `src/repo-layer/docs/` | rl.docs |
| `.agents/skills/{chain-intent,chain-spec,chain-plan,pr,pr-feedback,distill,verify,precommit-review,janitor}/` (SKILL.md, agents/openai.yaml, templates/, scripts/) | `src/repo-layer/dot-agents/skills/<name>/` | rl.skill.<name> |
| `.agents/skills/chain-{intent,spec,plan}/scripts/stage-check.sh` (one source, three installs) | `src/repo-layer/dot-agents/skills/chain-stage-check.sh` | rl.skill.chain-stage-check |

Run-time library modules of area C (inside the foundation unit `lib.agentive`, header `agentive-unit: lib.agentive`):
`agentive/gitgh.py` (git and `gh api` helpers, default branch with the ls-remote fallback, no-human signals),
`agentive/prbody.py` (/pr body), `agentive/prstate.py` (/pr-feedback state), `agentive/distillq.py` (/distill queue), `agentive/evalcase.py` (/distill reviewer eval case drafts via `scripts/eval-case.sh`, EVALBUNDLE, docs/impl/decisions-log.md §27).

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
  predicted or uncertain, then `## Verification`, `## Risk` (first line `Request: ...`, E2E-22), `## How to revert`, then the block
  under `## agentive review record` folded in `<details>` (E2E-23, docs/impl/decisions-log.md §19).
- **Script output lines**: stage-check `AGENTIVE_CHAIN=ok:<stage>:<slug> DEFAULT=<b>` / `AGENTIVE_CHAIN=stop:<stage>:<slug> NEXT=<action>`
  (exit 1); pr-body `PR_BODY=<file> MODE=new|refresh|explain PR=<n|none> TIER=<0|1|2|uncertain> EXPLAINER=required|optional CHAIN=<slug:stage|none> U2=<..>; NEXT=..`,
  `AGENTIVE_PR=stop reason=<r>; NEXT=..`; pr-body publish `PR=<n> URL=<u> CREATED` / `PR=<n> UPDATED=<section>`, on a PR a human
  must merge `MERGE=human pr=<n> url=<u> why=<codes>; NEXT=..` and, with no human present, the last line
  `AGENTIVE_PR=done pr=<n> waiting=human_merge url=<u>; NEXT=..` (E2E-14); pr-state `PR_STATE=ok|wait|stop ...; NEXT=..`
  (`wait reason=human_merge`: gate human_required, nothing left to fix, exit 0); chain-plan stage-check outside plan mode on
  Claude Code: the guard hook's deny reason `AGENTIVE_CHAIN=stop:plan:<slug> NEXT=..` (E2E-18); queue-read `QUEUE ... ; NEXT=..`;
  janitor gate `GO <kind>` (0) / `SKIP <kind>: #N open` (1) / `AGENTIVE_FAIL ...` (2).

## 3. Seams used

- B render keys (docs/impl/B.md §1): `render.orientation`, `render.doc_pointers`, `render.verbs`, `render.path_rules`
  (block placeholders, one per line); the boundary and service facts keys are gone (NOBOUNDARYLINE, 2026-10-29).
- Generate-time values (tools/generate.py): `%%agentive:gen.headless_cmd%%` (the design's headless standard command, in the
  janitor, pr-feedback and distill SKILL.md) and `%%agentive:gen.install_skill%%` (the skill directory of each stage-check.sh
  copy) (HEADLESSBINS, SKILLPATHS).
- B `when` grammar: `review.managed_code_review_copy`, `rule_packs.boundary` (dotted key, absent = false).
- B `agentive.handoff.write(root, "open_question", failure_reason, next_step)` from /chain-spec (headless open question).
- D `.agents/bin/agentive-review precommit [--human-confirmation obtained]` (writes precommit-review.json; exit 3 =
  LLM tasks, run in the forked skill), D gate job log line `AGENTIVE_GATE=<decision> tier=<t> reasons=<codes>` (read by
  pr-state.sh from the gate job log), D finding marker `<!-- agentive-finding: <id> -->`.
- D `.agents/bin/agentive-lint lock` (verify step 1), B `.agents/bin/verb verify`.

## 4. What the content area needs from the engine (all met; answers in docs/impl/B.md §5)

1. Add install-time key `render.code_review_copy` (block): the exact bytes of the rendered `code_review.md`. The
   `REVIEW.md` template is that one placeholder with no trailing newline, so the copy is byte-identical.
2. `when: rule_packs.boundary`: say which run sets it (the init skill proposes the boundary pack when it sees two or
   more modules, spec.one_way.boundary_lint; the human answers; then `--values`?). v1 never installs the pack; the pack's
   text no longer points at an AGENTS.md boundary line (NOBOUNDARYLINE).
3. Optional: a CLI for the handoff writer (`python3 -m agentive.handoff write --reason open_question ...`); /chain-spec
   now uses `python3 -c` with `agentive.handoff.write` (tested in tests/content/test_stage_check.py).

## 5. Design judgments [설계 판단] (also in the file headers or SKILL text)

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
   `check` passes (and, since 2026-10-31, the local Explainer visual checks `explainer_style:<code>`, docs/impl/decisions-log.md §22);
   local tier prediction = the CI classifier (`review/pipeline.rules_estimate`: RV1 reading, RV2 with always_human items
   such as dependency_change, RV3 rules, RV4 rules_tier; LOCALTIER, §22) plus the branch-name chain stage -> 2 | 0 | 1 |
   uncertain; implementer session id: `--session-id`, else `AGENTIVE_SESSION_ID`, else stop.json `session_id`, else
   `unknown`; verify.json reused only when its content_tree = HEAD^{tree}, its verb commands are the manifest's and the
   tracked tree is clean now (VERIFYTREE, E2E-08; stale NEXT: `verb verify --staged`); the base is fetched first
   with an explicit refspec and a missing base is a stop (BASEREF, E2E-06; `gitgh.base_ref`); an open PR
   gets only `## Verification` (`--explain`: only `## Explainer`) through gh api PATCH; the default revert line.
9. /pr-feedback: replies go through `scripts/pr-reply.sh` (SKILLGUIDE, §25); full state in `.agents/state/pr-state-<n>.json`, failing job logs in `.agents/state/logs/`; gate
   reasons from the gate job log line, else from the check-run output text; a human thread = a root review comment
   without a finding marker from a non-Bot user.
10. /distill queue: an entry is ready when its transcript is >= 30 s old or missing; `--done <session_id>...` removes
    handled entries (no ledger file); invalid lines are reported, never removed.
11. janitor: gate.sh exit codes 0 GO / 1 SKIP / 2 error; owner/repo parsed from the origin URL (gh's `{owner}/{repo}`
    placeholders as fallback); branch `janitor/<id>-<YYYYMMDD>`; the label is passed to /pr (`publish --label agentive-janitor:<id>`, E2E-40,
    docs/impl/decisions-log.md §26); `scripts/label-pr.sh` (gh api issues labels, read back, one retry; SKILLGUIDE, §25) is the standalone
    repair that a failed publish names.
12. Codex argument reading (UV-23): every skill uses `$0`/`$ARGUMENTS` and says: when it reads literally `$0` or is
    empty, take the value from the user's message.
13. (removed 2026-10-29, DEFAULTS: init writes no decisions.yaml; see docs/impl/decisions-log.md §11.)

14. V1-03 (2026-10-29): the chain-spec open-question handoff is the skill script `scripts/handoff.sh` (wraps
    `python3 -m agentive.handoff write --reason open_question`), so the headless allowlist `Bash(sh .agents/skills/*)` covers it
    when it is called repo-root relative; janitor, pr-feedback and distill write the design's standard command in full.
15. V1-04 (2026-10-29), superseded by SKILLPATHS the same day: tools/generate.py bakes the skill directory into each
    stage-check.sh copy (`skill=chain-<stage>`), so no path logic runs at all.
18. SKILLPATHS (2026-10-29): every SKILL.md calls `sh .agents/skills/<skill>/scripts/<x>.sh` and starts with one line saying
    its commands run from the repository root; script usage and NEXT lines use the same form. /distill writes judgment rules to
    `.agents/rules/<slug>.md` and runs `.agents/bin/agentive-lint rules --write` in the same PR (RULESNEUTRAL).
16. V1-10 / V2-12 (2026-10-29), REVIEWRULES (2026-11-02): `AGENTS.md#context` has no `@code_review.md` import. One pointer line
    (`Review gate config: code_review.md ...`) says the machine rules live there and are opened only to change them; the human review
    rules are the `## Code Review Rules` section at the end of the same region (the heading Codex GitHub code review reads), and
    code_review.md#rules holds the machine block only. The Codex line stays: a `/<skill>` here and in skill text is `$<skill>`.
    Codex concatenates AGENTS.md files as text and resolves no `@` import (openai/codex official GitHub,
    codex-rs/core/src/agents_md.rs at 23934d31; UV-13). A second new line: follow the `NEXT=` of each SessionStart line.
17. V2-01 / V2-02 (2026-10-29): `prbody.predict_tier` uses D's RV3 rule functions (`rv3.NOT_DOCS`, `_manifest_class`,
    `whitespace_only_texts`, `_is_doc`) instead of its own copies, so the local estimate and CI classify the same way.
    Superseded 2026-10-31 (LOCALTIER, E2E-29 / E2E-30): `predict_tier` calls `review/pipeline.rules_estimate`, the same RV1-RV4
    rule functions the CI pipeline runs, so DEPHUMAN and the RV3 test and config rules apply locally too.

## 6. Open issues

1. Resolved 2026-10-29 (SKILLPATHS, HEADLESSBINS): scripts are called repo-root relative and the standard command allows
   `.agents/bin/agentive-lint *` and `.agents/bin/agentive-review *`; a content test checks every command of the skills
   against the allowlist (tests/content/test_regressions.py).
2. code_review.md is not in any U2 confirm_scope glob (design default); RV2 protects it as check config (tier 2).
