---
name: chain-intent
description: Start a chain task - interview the human, then write docs/plans/<slug>/intent.md in their words and open the intent PR. Use when the chain-entry triage "intent.md needed?" answers yes, or when the user asks for intent first or to be interviewed.
argument-hint: <slug>
disable-model-invocation: false
---

# /chain-intent <slug>

Run every command below from the repository root (script and `.agents/bin/` paths are repo-root relative).

Goal: write what the human wants, in their words and without technical design, as `docs/plans/<slug>/intent.md` and open the intent PR. Done when the PR is open: it is always tier 2 (a human merge accepts the intent, closing the PR rejects it), and /chain-spec starts only after the merge. This stage writes no spec.md or plan.md.

Slug: `$0` (kebab-case, not `archive`). If it reads literally `$0` or is empty (Codex), take the slug from the user's message, or pick a short one and say it.

1. Run `sh .agents/skills/chain-intent/scripts/stage-check.sh <slug>` alone in its Bash call (the script prints its result). If it prints an `AGENTIVE_CHAIN=stop:...` line, print it, stop, and ask the human what it says.
2. Look for an earlier try: `git fetch --quiet --no-tags origin +refs/heads/intent/<slug>:refs/remotes/origin/intent/<slug>` (`couldn't find remote ref` means none), then `git rev-parse --verify --quiet intent/<slug>` and `git rev-parse --verify --quiet origin/intent/<slug>` (no output means none), each alone in its Bash call.
   - A branch exists: you are resuming. `git switch intent/<slug>` (only the remote has it: `git switch -c intent/<slug> --no-track origin/intent/<slug>`), take its intent.md as the draft and go on from step 3. A correction is one more commit on this branch, not a second branch; refresh an open PR with /pr, plus `/pr --explain` when the correction changes the proposed behavior.
   - Neither exists: `git switch --detach origin/<DEFAULT>` (DEFAULT is in the check's output), so no branch exists before the human agrees.
3. No human present (`AGENTIVE_HEADLESS=1`, `CI` or `GITHUB_ACTIONS` set; a headless `claude -p` or Routines run): no interview. Draft intent.md from the request (instructions in the run prompt count as the user's words), put every unknown under `## Open questions` and go to step 6.
4. Interview (a human is present): look up what code, docs, git history, the tracker, CI and connected tools can answer and ask only about decisions they cannot. One question per turn with your recommended answer, highest impact first. Stop when Problem, Proposed outcome, Affected users and systems, Constraints and Open questions are filled or the user says stop; what is still unknown goes to `## Open questions`.
5. Draft intent.md from `templates/intent.md` in the user's words, with no technical design. Every requirement the user stated is one bullet under `## Proposed outcome`, in the user's own terms: do not ask again what was already said, never merge two requirements into one bullet, never drop one. Show it and apply the user's corrections until they agree.
6. Unless you are resuming, create the branch now: `git switch -c intent/<slug> --no-track origin/<DEFAULT>` (`--no-track` keeps the default branch from becoming the upstream; /pr sets it with `git push -u origin HEAD`). Write intent.md and commit only that file through the normal commit procedure.
7. Run /pr (Codex: $pr).
