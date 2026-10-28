# agentive

AI-native SDLC and codebase framework for coding agents: Claude Code implements, Codex reviews.
"AI agent가 길을 잃지 않고 쉽게 코드베이스를 이해하며 항상 균일한 high quality output을 내는 AI-native SDLC·codebase framework".

- Install (Claude Code >= 2.1.275, one step): `/plugin install agentive --marketplace xrpz/agentive`, then `/agentive:init`.
- Agent-led install: tell your agent `install https://github.com/xrpz/agentive`; it follows the install
  sequence in [AGENTS.md](AGENTS.md#install-agentive-into-a-project-agent-led-install). Codex: `$agentive-init`. `/agentive:update` re-applies newer templates later.
- `/agentive:init` reads the repo's own evidence (code, lockfiles, package files, CI config, README); it never picks
  a stack or scaffolds, writes files without committing, and asks one question (REVIEW.md copy, default No).
- Design: [design/framework.yaml](design/framework.yaml) (rendered: [docs/map/index.html](docs/map/index.html),
  decision index: [docs/DECISIONS.md](docs/DECISIONS.md)).
- Editing this repo: [AGENTS.md](AGENTS.md) (rules) and [docs/IMPLEMENTATION.md](docs/IMPLEMENTATION.md) (architecture).

License: MIT.
