---
name: pr
description: Push the current work branch and open its PR, or refresh an open PR, with the agentive PR body (## Explainer first on tier 2, ## Verification, ## Risk, ## How to revert, the folded agentive review record). Use after committing work, for every chain intent, spec and feature PR, and with --explain to write only ## Explainer.
argument-hint: "[--explain]"
disable-model-invocation: false
---

# /pr [--explain]

Run every command below from the repository root (script and `.agents/bin/` paths are repo-root relative).

Arguments: `$ARGUMENTS` (Codex: read `--explain` from the user's message). All GitHub calls go through `gh api`; `gh pr` is blocked in cloud sessions and Routines runs.

Goal: push the work branch and open its PR, or refresh the open one, with the agentive PR body: `## Explainer` first on tier 2, `## Verification`, `## Risk`, `## How to revert` and the folded `## agentive review record`. Done when `publish` printed its last line (below). When stuck, the script's `AGENTIVE_PR=stop reason=...` line has the NEXT to follow; the script wrote nothing when it stopped.

## Open or refresh (no --explain)

1. Work on a work branch with everything committed (normal commit procedure); the PR shows committed work only.
2. `git push -u origin HEAD`, once, before the draft.
3. `sh .agents/skills/pr/scripts/pr-body.sh draft --model <the model you run as>`. Add `--intent-skipped` when the user told you to skip intent.md on a hard-to-reverse change, `--hotfix-of <n>` when this PR fixes the merged PR #n, and `--request "<issue URL, or the user's request in one sentence>"` when you know where the change was asked for (a chain task gets its `docs/plans/<slug>/` line by itself). Read its output lines:
   - `AGENTIVE_PR=stop reason=...` (verify, or a base `origin/<default>` that could not be compared): do its NEXT, then draft again. `NOTE` lines say what the script fetched or assumed.
   - `CHAIN_PROBLEM`: CI will block the PR (reason chain_order); fix the branch (one slug, one new artifact per PR) before publishing.
   - `REASONS ...`: why the tier is 2; more than 3 are grouped by code, and every reason is in the file named by `REASONS_FILE=` (`.agents/state/pr-tier2-reasons.txt`). The draft's first Explainer bullet starts from this line: shorten it to the one or two causes the human must judge.
   - `NOTE PRECOMMIT_STALE ...`: the saved precommit review is of other content, so the draft has no review entry from it; run /precommit-review on this content as the note says, or publish without one (CI reviews the PR either way).
   - `MODE=refresh` (the branch has an open PR made by /pr): the draft holds only `## Verification`; fill it and go to step 5.
   - `MODE=adopt` (the open PR has none of the /pr sections, for example one opened in the web UI): the draft is the whole body (tier 2 starts with `## Explainer`); fill it as in step 4.
4. Edit the draft at its `PR_BODY=` path: replace every `<!-- agent: ... -->` and `% agent:` marker with text, or delete the line.
   - `## Explainer` is for a human who must see the changed workflow at a glance. It is present when `EXPLAINER=required` (tier 2 predicted); with `EXPLAINER=optional` (`TIER=uncertain`) skip it: if CI decides tier 2, the gate blocks with `explainer_missing` and `/pr --explain` adds it.
     - First line `**What changes:** <one sentence>`, then 3-5 bullets in the PR's language (new or changed, unchanged, what the human must judge), each claim citing code as `path:line`, `path:start-end` or `path#symbol`. Code locations go in the bullets, not in the diagrams.
     - `### Before` is a mermaid flowchart of the flow on the base branch for the part the PR touches, `### After` the same flow on the head; the draft's classDef and `How to read:` lines stay. Draw only steps and calls that exist in that tree (a new function nothing calls yet gets an arrow label saying so); when the flow does not change, After names the unaffected nodes and why; on a chain intent or spec PR, Before is the current behavior and After the proposed one. `.agents/skills/pr/templates/explainer-example.md` is a filled example that renders in light and dark themes; step 5 prints each style rule that is broken.
   - `## Verification`: keep the generated lines; give each R<n> a result (pass | fail | not run); for a bug fix add the red run (fails before the fix) and the green run (passes after). No raw logs.
   - `## Risk`: what could break and how it was checked. Its first line is `Request: <issue URL | docs/plans/<slug>/ | user request: "<one sentence in the user's words>">` (reviewers check a new capability against it): replace the marker, keep the line. Keep `intent skipped: user instruction` when it is there.
   - `## How to revert`: steps beyond reverting the merge commit (data, config, flags).
   - Leave `## agentive review record` (the folded `agentive-review` block) as generated.
5. `sh .agents/skills/pr/scripts/pr-body.sh check <file> --explainer <required|optional as the draft printed>` until it prints `check_ok`. `PROBLEM explainer_style:<code>: ...` lines name the Explainer rules and are checked here only (CI checks that the section, Before, After and their diagrams exist).
6. `sh .agents/skills/pr/scripts/pr-body.sh publish <file> --title "<type>: <what, in a few words>"`. Add `--label <name>` (repeatable) when the calling skill asks for a label (the janitor passes `agentive-janitor:<kind>`): publish adds it through `gh api`, reads it back (one retry) and prints `LABELED <names> pr=<n>` before its final line. A last line `AGENTIVE_FAIL tool=pr-label ...` means the PR exists but a label is missing: follow its NEXT and say in the report that the PR is not labeled.
   - No open PR: it opens one against the default branch.
   - An open PR made by /pr: it replaces only `## Verification` (comments, checks and the rest of the body stay). Stop and report its last line `next: /pr-feedback <n>`.
   - An open PR without the /pr sections (`MODE=adopt`): it writes the whole drafted body and keeps the old text, each line behind `> `, under `## Original description` (the old body is also saved to `.agents/state/pr-body-before-<n>.md`).
   - `AGENTIVE_PR=stop reason=...` (the PR changed after the draft was made): nothing was written; do its NEXT (draft again, or publish with `--replace-body` to write the whole drafted body).
   - `MERGE=human pr=<n> url=<url>` (tier 2: the install PR, a workflow change, a trust-boundary path, a chain intent or spec PR): a human reviews and merges it; tell the human the URL. With no human present (headless runs (`claude -p`), Routines runs) the last line is `AGENTIVE_PR=done ... waiting=human_merge` (with `labels=<names>` when `--label` was given): report the PR URL and end the run; when a `HANDOFF=` line was printed in this run (SessionStart, a guard deny or the Stop hook), end your final message with that line too.

## --explain

1. `sh .agents/skills/pr/scripts/pr-body.sh draft --explain`, fill the markers as in step 4, then `sh .agents/skills/pr/scripts/pr-body.sh check <file> --explain-only`.
2. `sh .agents/skills/pr/scripts/pr-body.sh publish <file>` replaces only `## Explainer` of the open PR (adds it when missing). Run it again after a push that changes the explained flow.

Merging, approving, auto-merge, review comments and failing checks are not part of /pr (review comments and checks: /pr-feedback), and the agentive-review block of another PR is not copied.
