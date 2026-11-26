---
description: "The PR body written with the project workflow (skill pr) carries the three headings and the folded agentive review record."
expected_outcome: "With the plugin pr-body.md has Verification, Risk, How to revert and the agentive review record; without it the record is missing."
max_turns: 25
timeout_seconds: 900
allowed_tools: [Read, Glob, Grep, Skill, Write, Edit, Bash]
append_system_prompt: "@@AGENTS_MD@@"
---

Prepare the pull request body for the current branch the way this project does it, and write it to pr-body.md. Do not push and do not call gh.
