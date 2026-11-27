---
name: chain-spec
description: Second chain stage - write docs/plans/<slug>/spec.md (numbered requirements R<n> each with one verification means, design, how each intent constraint is met) after the intent PR is merged, then open the spec PR. Use for /chain-spec <slug> or when a merged intent.md needs its spec.
argument-hint: <slug>
disable-model-invocation: false
---

# /chain-spec <slug>

Run every command below from the repository root (script and `.agents/bin/` paths are repo-root relative).

Goal: turn the merged intent into `docs/plans/<slug>/spec.md` and open the spec PR. Done when the PR is open: it is always tier 2 (a human merge accepts the spec, closing rejects it). Only spec.md changes in this stage: no code, no plan.md.

Slug: `$0`. If it reads literally `$0` or is empty (Codex), take it from the user's message.

1. Run `sh .agents/skills/chain-spec/scripts/stage-check.sh <slug>` alone in its Bash call (the script prints its result). If it prints an `AGENTIVE_CHAIN=stop:...` line, print it, stop, and tell the human its NEXT.
2. `git switch -c spec/<slug> --no-track origin/<DEFAULT>` (/pr sets the upstream). Read `docs/plans/<slug>/intent.md`, AGENTS.md and the code the change touches.
3. Write `docs/plans/<slug>/spec.md` from `templates/spec.md`:
   - Requirements R1, R2, ... each with exactly one verification means (a test name or a command). Numbers are not renumbered or reused; a dropped requirement keeps its line marked `(dropped)`.
   - Map every requirement bullet of the intent's `## Proposed outcome` and every constraint to an R<n>, a design item, or `(not adopted: <reason>)`. The spec PR review flags an unmapped bullet.
   - Answer every open question of the intent, or carry it forward to `## Areas of concern`. A conflict between normative sources (rules, ADRs, review criteria, policies) goes there too, with the owner who decides it: it is not resolved silently.
   - Missing information: look it up first (code, docs, git history, tracker and CI through a CLI, connected tools); with a human present, ask. No human present and the question is hard to reverse (data deletion, schema, payments, mass sends): do not guess. Write the bullet `- [OPEN] <question>` under `## Open questions from the intent` (delete the template's example [OPEN] bullet when you have no such question), run `sh .agents/skills/chain-spec/scripts/handoff.sh "<the question>" "<what the human must decide>"` (each argument at least 20 characters; it prints `HANDOFF=<path>`), finish the rest of the spec and go to step 5: the handoff directory does not survive a Routines run, so the spec PR carries the question (its `## Risk` lists every `[OPEN]` bullet). A human answers it in the PR by replacing `[OPEN]` with `-> answered: ...`; /chain-plan stops while the merged spec.md still has one. Any other gap: assume, and add a `[inferred]` line under `## Decisions`.
4. Commit only spec.md on `spec/<slug>`.
5. Run /pr (Codex: $pr). The human checks first that the spec solves the intent's problem and that every open question is answered or carried forward; a bullet that still says `[OPEN]` only the human can answer.
