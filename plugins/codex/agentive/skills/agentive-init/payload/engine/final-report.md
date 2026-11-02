# Final report of init and update

The init and update skills of Claude Code and Codex read this after the engine run.

Report to the user in this order. A headless run (`claude -p`, Routines) reports the same things. Lines marked (init) or (update) belong to that command only.

1. **Hooks and guard status, always.** The `AGENTIVE_HOOKS` and `AGENTIVE_GUARD` lines, verbatim: `installed`, or `off` with its reason and NEXT. `off` is never silent: with the hooks off the guard rules are not enforced.
2. **What is now on.** Every `AGENTIVE_HOOK` line (the event and the command it runs automatically) and any `AGENTIVE_SKIPPED` line (`--no-hooks`).
3. **Revert.** The `AGENTIVE_REVERT` lines (`git checkout -- ...` for modified files, `git clean -fd -- ...` for new files).
4. **Changed files.** Every path after `AGENTIVE_CHANGED:`. If you open a PR for these changes, put the same list in the PR body, and the `AGENTIVE_REPORT github: install PR:` line when it is printed: the first install PR and every PR that changes `.github/workflows/agentive-review.yml` get no CI AI review: a human reviews and merges them, and the install PR is merged first, before other PRs.
5. **Warnings.** `AGENTIVE_NOT_EFFECTIVE` (written, not yet effective), `AGENTIVE_CONFLICT` (a key of your own settings.json that differs from agentive's value: yours was kept) and every `AGENTIVE_REPORT` and `AGENTIVE_WARN` line, verbatim.
   - (init) Say plainly which repository secret the user must add for the CI AI review; the `AGENTIVE_REPORT github: the CI AI review ...` line names it.
   - (update) `AGENTIVE_REPORT rubric:` lines say that the rubric preset moved (and the override's `extends` with it) and how to stay on the old preset: tell the user in plain words what changes for tier 1 PRs.
6. **Auto-merge** (`AGENTIVE_REPORT auto-merge:` line). Tell the user in plain words what happens now. Default `low_risk`: tier 0 PRs are merged automatically, tier 1 runs in shadow mode and a PR it would have merged gets the label `agentive:shadow-auto-merge`; `dry_run` and `off` merge nothing, `low_and_medium_risk` also merges tier 1. The GitHub repository setting 'Allow auto-merge' and branch protection requiring `agentive-review-gate` must be on before any value that merges can work. To change it, the user asks you or sets `review.auto_merge` in `.agents/framework.yaml` (a review config change: a human confirms the commit; with no human present you open a PR only). `.agents/bin/agentive-lint settings --effective` shows what is in force.
   - (update) An `AGENTIVE_MIGRATED` line says an older `.agents/decisions.yaml` was moved into `.agents/framework.yaml`: repeat its `AGENTIVE_REPORT migration:` lines.
7. **Pending verbs** (`AGENTIVE_PENDING`), shown as "pending (no evidence)".
   - All verbs pending (no stack evidence): tell the user to scaffold the project with their own agent and then run update (Claude Code `/agentive:update`, Codex `$agentive-update`).
   - Some verbs pending: repeat the `AGENTIVE_REPORT required-checks:` line. A pending verb is not required (the gate computes the list, nothing is written, no decision is needed) and becomes required once it has a command.
   - `AGENTIVE_REPORT auto-merge unavailable:` is printed (no required check has a command, or the user listed a pending verb in `require_checks`): ask the user ONCE for the missing command and never pick a tool or command for them (the engine reads evidence only), or tell them they can remove a check their project does not have from `review.pipeline.stages.RV1.require_checks`. A command they give is a pinned verb value (`"pinned": {"by": "user", ...}` in the values file); run update and let the proof run.
8. **Drift or conflicts** (`AGENTIVE_DRIFT`). Existing files were kept; the proposed diffs are under `.agents/state/conflicts/`.
9. **Commit.** init and update never commit. Commit the changed files only when the human asks, through the normal commit procedure; the `AGENTIVE_NEXT` line names the follow-up (init: a work branch, then the install PR with /pr, merged by a human).
