---
name: chain-intent
description: Start a chain task - interview the human, then write docs/plans/<slug>/intent.md in their words and open the intent PR. Use when the chain-entry triage "intent.md needed?" answers yes, or when the user asks for intent first or to be interviewed.
argument-hint: <slug>
disable-model-invocation: false
---

# /chain-intent <slug>

Run every command below from the repository root: script and `.agents/bin/` paths are repo-root relative (never `cd` into the skill directory).

Slug: `$0` (kebab-case, never `archive`). If it reads literally `$0` or is empty (Codex), take the slug from the user's message; if there is none, pick a short kebab-case slug and say it.

1. Run `sh .agents/skills/chain-intent/scripts/stage-check.sh <slug>` alone in its Bash call (no `; echo $?`: the script prints its result). If it prints an `AGENTIVE_CHAIN=stop:...` line, print that line, stop, and ask the human what it says.
2. Look for an earlier try of this slug: run `git fetch --quiet --no-tags origin +refs/heads/intent/<slug>:refs/remotes/origin/intent/<slug>` (`couldn't find remote ref` means the remote has none), then `git rev-parse --verify --quiet intent/<slug>` and `git rev-parse --verify --quiet origin/intent/<slug>` (no output means no such branch), each alone in its Bash call.
   - Either branch exists: you are resuming. `git switch intent/<slug>`, or, when only the remote has it, `git switch -c intent/<slug> --no-track origin/intent/<slug>`. Read `docs/plans/<slug>/intent.md` if it exists, say in one line what you found, and go on from step 3 with that text as the draft. A correction is one more commit on this branch, never a second branch; an open PR for it is refreshed with /pr, plus `/pr --explain` when the correction changes the proposed behavior.
   - Neither exists: `git switch --detach origin/<DEFAULT>` (DEFAULT from the check's output), so the interview reads the fresh default branch and no branch exists before the human agrees.
3. No human present (`AGENTIVE_HEADLESS=1`, `CI` or `GITHUB_ACTIONS` set; a headless `claude -p` or Routines run): no interview. Draft intent.md from the request, put every unknown under `## Open questions`, and go to step 6. Instructions in the run prompt count as the user's words.
4. Interview (a human is present):
   - Look up first: code, docs, git history, then the issue tracker, CI and error tracker through an available CLI (`command -v gh`), then connected tools. Ask the human only for decisions the code and docs cannot answer.
   - Ask one question per turn, each with your recommended answer, highest impact first (scope, users, constraints, what success looks like).
   - There is no question limit. Stop when Problem, Proposed outcome, Affected users and systems, Constraints and Open questions are filled, or when the user says stop.
   - Whatever is still unknown at the stop goes to `## Open questions`.
5. Draft intent.md from `templates/intent.md` in the user's words, with no technical design. Every requirement the user stated is one bullet under `## Proposed outcome`, in the user's own terms: do not ask again what was already said, never merge two requirements into one bullet, never drop one. Show it; apply the user's corrections until they agree.
6. Unless you are resuming, create the branch now: `git switch -c intent/<slug> --no-track origin/<DEFAULT>` (`--no-track` keeps the default branch from becoming its upstream, /pr sets the upstream with `git push -u origin HEAD`). Write `docs/plans/<slug>/intent.md` and commit only that file on `intent/<slug>` through the normal commit procedure.
7. Run /pr (Codex: $pr) to push and open the intent PR. It is always tier 2: a human merge accepts the intent, closing the PR rejects it. Do not start /chain-spec before the merge.

Never push to the default branch, merge, or write spec.md or plan.md here.
