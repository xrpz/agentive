# agentive framework repo: rules for the agent that edits it

agentive is an AI-native SDLC and codebase framework: a Claude Code plugin (and a Codex plugin) whose
`/agentive:init` and `/agentive:update` install a repo layer into user projects. This repo holds its
design and the generator that builds the plugins. You (Claude Code or Codex) edit it when the author asks.
No scheduled or autonomous agent loop runs here (decision FM).

## Where things are

| path | what | edit by hand? |
|---|---|---|
| `design/framework.yaml` | canonical design: spec, contracts, decisions, view, sources (ids are stable, never reused) | yes, only on an applied design decision |
| `design/schemas/*.schema.json` | file formats of every contract | yes, with the design |
| `framework.yaml` | framework-repo config: plugin name (D14), U3, U4. Not the design | rarely |
| `src/` | template sources: `repo-layer/` (installed into user repos), `engine/`, `plugin/{claude-code,codex}/` | yes |
| `src/units/<area>.yaml` | which source goes where; one file per owner | yes |
| `tools/generate.py`, `tools/bundles.yaml`, `tools/map/` | generator, output layout, map page renderer | yes |
| `tests/`, `fixtures/` | self-tests | yes |
| `plugins/`, `docs/map/`, `docs/DECISIONS.md`, `.claude-plugin/marketplace.json`, `.agents/plugins/marketplace.json` | GENERATED | never |
| `CHANGELOG.md`, `package.json` "version", `package-lock.json` | written by the version step | never |
| `docs/IMPLEMENTATION.md` | architecture, conventions, ownership: read before adding anything | yes |

## Editing rules (FM.rules)

1. Edit `design/framework.yaml`, `design/schemas/*.json` and the sources under `src/`, `tools/`, `tests/` only.
   Never edit generated outputs (the Claude Code plugin, the Codex plugin, the map page, the marketplace files)
   by hand; `.claude/settings.json` denies those edits.
2. After every edit run `python3 tools/generate.py`, then `python3 tools/generate.py --check`. `--check` writes
   nothing and fails naming every generated file that is STALE, MISSING or ORPHANED.
3. Before every commit run `python3 tools/selftest.py` (generate_check, schema_validation, component_tests,
   sample_repo_tests, version_sync_check). CI runs the same on pull_request and on push to main; never on a schedule.
4. Add one changeset per change: `.changeset/<slug>.md` with the bump level and a description
   (format in `.changeset/README.md`). Never edit the version or `CHANGELOG.md` by hand.
5. Before a commit, compare `git diff --cached --name-only` with the confirm paths below. On a match, or when
   the commit is a release (the version step), ask the author and wait. Commit every other change after the
   self-tests pass.
6. Keep the install-sequence region below as the generator writes it (it is rendered from the design).
7. A template added to `src/` = one file + one entry in your `src/units/<area>.yaml` + generate
   (docs/IMPLEMENTATION.md §3). Every source file belongs to exactly one unit; a design ref must resolve.
8. Do not change the design on your own. Where the design is silent, choose, and mark the choice
   `[설계 판단]` in docs/IMPLEMENTATION.md. A claim you could not verify in an official source is `미검증`.

## Confirm paths (U3 = B; confirm_scope hooks, permissions, sandbox, security, release)

- hooks: `plugins/**/hooks/**`, `**/hooks.json`, `src/**/hooks/**`, `**/dot-agents/hooks/**`
- permissions: `**/permissions*`, `**/settings.json`, `**/dot-codex/**`, `**/guard.yaml`
- sandbox: `**/sandbox*`
- security: `**/security/**`, `**/*secret*`, `**/.env*`
- release: the commit made by the version step (changesets consumed; version and CHANGELOG.md changed)

The first two lines of each list are the design default (`spec.framework_repo.u3_confirm_paths`); the
`src/**`, `dot-*` and `guard.yaml` entries are added because templates live under `src/` [설계 판단].

## Commands

```sh
python3 -m pip install -r requirements-dev.txt   # dev only: PyYAML, jsonschema
python3 tools/generate.py                        # build outputs, delete orphaned generated files
python3 tools/generate.py --check                # write nothing; exit 1 on drift
python3 tools/generate.py --list                 # every output and the unit that makes it
python3 tools/selftest.py [step ...]             # self-tests
npm ci && npm run version                        # release only (U3 release): changesets + generate
```

Find a design entry: `grep -n "id: <ID>" design/framework.yaml`; spec keys: `grep -n "^  [a-z_]*:" design/framework.yaml`.
Rendered view: `docs/map/index.html`; decision index: `docs/DECISIONS.md`.

## Words

- Runs with no human present are called only "headless runs (`claude -p`)" or "Routines runs" (no other name; tests enforce it).
- `.agents/manifest.yaml` in a user repo is the "agentive manifest"; `package.json`, `pyproject.toml`, `go.mod`
  and the like are "package files".
- Plugin name: agentive. Plugin commands: `/agentive:init`, `/agentive:update`, `/agentive:reprove`;
  Codex `$agentive-init`, `$agentive-update`. `/pr`, `/distill` and the other work-loop skills are repo skills.

## Install agentive into a project (agent-led install)

When someone says "install <this repo URL>", run the sequence below exactly.

<!-- AGENTIVE-FRAMEWORK START install-sequence -->
<!-- generated by tools/generate.py from design spec.distribution; edit the design, not this region -->
Claude Code (`spec.distribution.claude_code.install_sequence`): run exactly these steps and check each expected output.

1. `claude plugin marketplace add <owner/repo | git URL>` -> expect `Successfully added marketplace: <name>`
2. `claude plugin install agentive@<name> --scope project` -> expect `Successfully installed plugin: agentive@<name> (scope: project)`
3. `/reload-plugins (inside a running session)` -> verify: `claude plugin list shows agentive@<name>`; `/agentive:init appears when typing /`
4. SessionStart hook tells the agent to ask the human to run /agentive:init

- One-command alternative (Claude Code >= 2.1.292): `claude plugin install agentive --marketplace <owner/repo>` (declares the marketplace in user settings even with --scope project).
- Collaborators: project scope enables the plugin for collaborators but does not download it; each collaborator runs the install once.
- Update: `claude plugin update agentive@<name>`, then /reload-plugins or next session, then run /agentive:update (init_apply.update): init is for first-time setup only.

Codex (`spec.distribution.codex`, policy repo_layer_only): marketplace file `.agents/plugins/marketplace.json`; plugin manifest plugin.json (root); .codex-plugin/plugin.json is a compatibility fallback.

- Agent may run: `codex plugin marketplace add <source>`; `codex plugin marketplace upgrade <name>`.
- Human only: install from plugin browser / Plugins Directory; review and trust plugin hooks.
- Update: codex plugin marketplace upgrade <name>, then restart, then $agentive-update (init_apply.update).
- Codex-only bootstrap: agent copies .agents/skills/agentive-init/ and .agents/skills/agentive-update/ from the framework repo at a pinned tag (sha256 checked against the release's skills.lock), then the human runs $agentive-init. To update, the agent copies both skills again from the new tag and the human runs $agentive-update.

Values for this repo: `<owner/repo>` = `xrpz/agentive`, `<name>` = `agentive` (marketplace name), Codex skill sources `plugins/codex/agentive/skills/agentive-init/` and `.../agentive-update/`, release lock `plugins/codex/agentive/skills.lock`.
<!-- AGENTIVE-FRAMEWORK END install-sequence -->
