---
"agentive": patch
---

The headless standard command now also allows `sh .agents/skills/*`, `git switch` and `git fetch`, so repo-skill scripts (/chain-*, /pr, /pr-feedback, /distill, janitor) and branch switching work in headless runs (`claude -p`) under dontAsk; the guard hook still judges every push target.
