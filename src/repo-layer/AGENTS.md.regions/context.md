## Project (generated)

%%agentive:render.orientation%%

Run every verb through `.agents/bin/verb <verb>`: it keeps full output in `.agents/state/logs/` and prints a short excerpt; on failure follow the `NEXT=` of its last line.
%%agentive:render.verbs%%

Review rules: @code_review.md
Chain entry (intent.md needed?): yes if (a) the diff cannot be described in one sentence, (b) it touches two or more modules, (c) the approach is uncertain, or (d) it is hard to reverse; yes -> say so in one line and run /chain-intent <slug> (intent -> spec -> plan); no -> implement directly; a user instruction overrides both ways.
Chain tasks: before working on docs/plans/<slug>/, read intent.md, spec.md and plan.md there. Stages: /chain-intent, /chain-spec, /chain-plan <slug> (Codex: $chain-intent ...); /pr pushes and opens every PR.
Commit: run /verify, then /precommit-review; commit only when it reports no blocking finding. A staged change to hooks, permissions, CI, .claude/settings.json, .codex/ or review config needs the human's yes first; with no human present, commit on a work branch and open a PR with /pr instead.
Docs vs code: descriptive docs (paths, commands, counts, behavior) follow the code, so fix them in the same change; when code violates a rule, ADR or spec, change neither side and tell a human.
Running the app: use the project's run skill; if none exists, generate one with Claude Code's /run-skill-generator.
Worktrees: untracked files a new Claude Code worktree needs (for example .env) are listed in .worktreeinclude.
%%agentive:render.boundary_line%%
%%agentive:render.service_facts%%
