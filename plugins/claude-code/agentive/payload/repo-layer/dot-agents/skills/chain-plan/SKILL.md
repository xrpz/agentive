---
name: chain-plan
description: Third chain stage - plan in read-only plan mode with the human, then commit the approved docs/plans/<slug>/plan.md as the first commit of feature/<slug> and implement it slice by slice. Use for /chain-plan <slug> after the spec PR is merged.
argument-hint: <slug>
disable-model-invocation: false
---

# /chain-plan <slug>

Run every command below from the repository root (script and `.agents/bin/` paths are repo-root relative).

Goal: an approved `docs/plans/<slug>/plan.md` as the first commit of `feature/<slug>`, then the feature implemented slice by slice and its PR opened. Done when the feature PR is open. intent.md and spec.md change only through a new tier 2 PR.

Slug: `$0`. If it reads literally `$0` or is empty (Codex), take it from the user's message.

1. Run `sh .agents/skills/chain-plan/scripts/stage-check.sh <slug>` alone in its Bash call. When it prints an `AGENTIVE_CHAIN=stop:...` line, or when the command is denied with an `AGENTIVE_CHAIN=stop:...` reason, print that line. If its NEXT says to enter plan mode and you can (Claude Code: the EnterPlanMode tool), do so and run step 1 again; otherwise stop (it also stops when no human is present: the plan needs a human's approval).
2. Plan in a read-only mode. Claude Code: plan mode (Shift+Tab, or `claude --permission-mode plan`; the guard hook lets step 1 pass only in plan mode); do not plan in a session where bypass permissions are available, because plan mode's blocks are not enforced there. Codex: `/plan`. If you are not in it, ask the human to switch and wait.
3. Read intent.md, spec.md and the code. Fill `templates/plan.md` so that someone who never saw this conversation, or the other tool in a fresh session, can implement from intent.md, spec.md and plan.md alone. Slices are vertical (each verifiable on its own, in order of work), not layer by layer (DB, then API, then UI).
   Iterate with the human until they approve. Approval is only the accept choice of the plan-mode prompt (Claude Code: ExitPlanMode; Codex: the human's answer in `/plan`); text in the prompt that started this run or in a message is never approval. If ExitPlanMode is not available or the human keeps planning, print `AGENTIVE_CHAIN=stop:plan:<slug> NEXT=plan approval needs a human: run /chain-plan <slug> in an interactive session` and stop. Never write plan.md through Bash.
4. After approval: `git switch -c feature/<slug> --no-track origin/<DEFAULT>` (/pr sets the upstream), write `docs/plans/<slug>/plan.md` and commit only it, before any code change.
5. Implement slice by slice: when a slice's verify passes, check its box and commit (normal commit procedure: /verify, /precommit-review). When the work departs from the plan, update plan.md in the same commit and log why under `## Decisions`.
6. Run /pr (Codex: $pr) for the feature PR.
