---
description: "An ambiguous request that touches several modules opens a chain intent (AGENTS.md chain entry, skill chain-intent) instead of code."
expected_outcome: "With the plugin the agent names the chain entry and starts /chain-intent; it writes no production code."
max_turns: 15
timeout_seconds: 600
allowed_tools: [Read, Glob, Grep, Skill, Write, Edit, Bash]
append_system_prompt: "@@AGENTS_MD@@"
---

I want multi-currency support in this project. Prices, invoices and the reports would all change, and I have not decided yet how rounding or stored amounts should work. Please take care of it.
