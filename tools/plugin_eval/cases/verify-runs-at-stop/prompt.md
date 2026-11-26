---
description: "The Stop hook runs verify before the agent may stop (spec.stop_hook); the record is .agents/state/verify.json."
expected_outcome: "With the plugin the work ends with a verify record; without it nothing runs the checks."
max_turns: 25
timeout_seconds: 900
allowed_tools: [Read, Glob, Grep, Write, Edit, Bash]
append_system_prompt: "@@AGENTS_MD@@"
---

Add a function `shout(text)` to the package under src/ that returns the text in upper case with an exclamation mark appended, and add one test for it. Say "done" when you are finished.
