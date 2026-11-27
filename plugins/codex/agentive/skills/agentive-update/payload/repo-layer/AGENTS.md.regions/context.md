## Project (generated)

%%agentive:render.orientation%%
%%agentive:render.doc_pointers%%

Run verbs through `.agents/bin/verb <verb>`: it keeps full output in `.agents/state/logs/` and prints a short excerpt; on failure follow the `NEXT=` of its last line.
Headless runs (`claude -p`): the allowlist matches one command at a time, so send one command per Bash call (no `; echo $?`, `cd`, redirect, heredoc or `sed -i`), change files with Write and Edit, and run checks with `.agents/bin/verb`. A refused command stops only that command: go on with /verify, /precommit-review and the commit.
Branches: create a work branch with `git switch -c <type>/<slug> --no-track origin/<default branch>` (the headless allowlist has `git switch *` and denies `git checkout -b`); /pr sets the upstream with `git push -u origin HEAD`.
%%agentive:render.verbs%%

Review gate config: code_review.md holds the machine rules of the CI review gate (security paths, confirm scope, tiers); open it only to change them. What a reviewer checks is under Code Review Rules below.
Codex: a `/<skill>` in this file and in skill text is `$<skill>` (for example `$verify`, `$pr`).
Chain entry (intent.md needed?): yes if (a) the diff cannot be described in one sentence, (b) it touches two or more modules, (c) the approach is uncertain, or (d) it is hard to reverse; yes -> say so in one line and run /chain-intent <slug> (intent -> spec -> plan); no -> implement directly; a user instruction overrides both ways.
Chain tasks: before working on docs/plans/<slug>/, read intent.md, spec.md and plan.md there. Stages: /chain-intent, /chain-spec, /chain-plan <slug> (Codex: $chain-intent ...); /pr pushes and opens every PR.
Commit: run /verify, then /precommit-review, and commit when it reports no blocking finding. A staged change to hooks, permissions, CI, .claude/settings.json, .codex/ or review config needs the human's yes first; with no human present, commit on a work branch and open a PR with /pr.
Session start: follow the `NEXT=` of each line it prints (HANDOFF, PENDING_CONFIRMATION, DIRTY, HARVEST); ask the human to resume, discard or postpone a HANDOFF for this branch before other work.
Harvest: when SessionStart prints `HARVEST=<n>; NEXT=ask_user_to_run:/distill`, offer /distill (Codex: $distill) once at the end of the task and run it only after the human agrees.
Docs vs code: descriptive docs (paths, commands, counts, behavior) follow the code, so fix them in the same change; when code violates a rule, ADR or spec, change neither side and tell a human.

## Code Review Rules

- Report a change that leaves a descriptive doc (paths, commands, counts, behavior) wrong, and code that violates an accepted ADR, rule or spec (a human decides; neither side is changed in this PR).
- Do not relitigate decisions tagged [user]; review decisions tagged [inferred].
