---
description: "A one-line fix is implemented directly: the chain entry says no (AGENTS.md chain entry)."
expected_outcome: "The typo is fixed and no chain intent is opened, with or without the plugin (a delta of 0 is expected: the case guards precision)."
max_turns: 10
timeout_seconds: 300
allowed_tools: [Read, Glob, Grep, Skill, Write, Edit]
append_system_prompt: "@@AGENTS_MD@@"
---

Fix the typo "recieve" in README.md so it reads "receive".
