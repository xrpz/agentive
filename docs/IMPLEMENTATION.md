# agentive implementation guide (agent-facing)

Read this before adding or changing anything under `src/`, `tools/`, `tests/` or `fixtures/`.
Purpose of agentive: "AI agent가 길을 잃지 않고 쉽게 코드베이스를 이해하며 항상 균일한 high quality output을 내는
AI-native SDLC·codebase framework". Claude Code implements, Codex reviews. Every file here is optimized for the
agent that maintains it.

Authority: `design/framework.yaml` (+ `design/schemas/`) is the design and wins over this file. This file fixes
how the design is implemented where the design names no file or tool; each such choice is marked `[설계 판단]`.
A claim not checked in an official source is marked `미검증`. Do not change the design from an implementation
task: write the gap in your `docs/impl/<owner>.md` (§7) and tell the main agent.

Design lookup: `grep -n "id: <ID>" design/framework.yaml`, spec keys `grep -n "^  [a-z_]*:" design/framework.yaml`.
Most-used design keys: `spec.repo_layer.files` (every installed file, ids `f-*`), `spec.init_apply` (init, update,
managed regions, drift), `spec.plugin` (commands, invocation), `spec.guard`, `spec.enforcement`, `spec.stop_hook`,
`spec.verb_wrapper`, `spec.spec_stage`, `spec.work_loop`, `spec.review_pipeline`, `spec.repo_checks`,
`spec.maintenance`, `spec.distribution`, `spec.framework_repo`, `contracts` (file -> schema -> writer/reader).

## 1. Directory layout

```
AGENTS.md  CLAUDE.md(@AGENTS.md)  README.md  LICENSE(MIT)      editing rules; CLAUDE.md imports AGENTS.md (FM.rules)
framework.yaml                    framework-repo config (contracts[framework-repo-config]): plugin name, U3, U4
package.json  package-lock.json   version + changesets (the only Node use)        .changeset/   one file per change
requirements-dev.txt              PyYAML, jsonschema: generator and tests only (never at run time)
design/framework.yaml             canonical design            design/schemas/*.schema.json   22 contract schemas
tools/generate.py                 generator CLI (§2)          tools/bundles.yaml   plugin output layout
tools/selftest.py                 self-test runner (§6)       tools/map/gen.py + tpl.html   map page renderer
tools/schemas/units.schema.json   schema of src/units/*.yaml
src/units/<area>.yaml             unit lists: foundation (A), plugin (B), content (C), review (D)
src/repo-layer/                   templates installed into user repos, at their escaped install path (§3)
src/engine/                       init/update/reprove apply engine (python3 stdlib), bundled in every payload
src/plugin/claude-code/           Claude Code plugin files (skills/init|update|reprove, hooks/) without the manifest
src/plugin/codex/                 Codex plugin files (skills/agentive-init|agentive-update) without the manifest
tests/<area>/test_*.py            component tests       tests/design/   schema validation      tests/sample_repo/ (D)
fixtures/{ts,py,go,java}/         sample repos for sample_repo_tests (D), dot paths escaped like src/
GENERATED (never edit):  plugins/claude-code/agentive/   plugins/codex/agentive/   .claude-plugin/marketplace.json
                         .agents/plugins/marketplace.json   docs/map/index.html   docs/DECISIONS.md
                         AGENTS.md install-sequence region
```

Generated plugin layout:

```
plugins/claude-code/agentive/.claude-plugin/plugin.json      name, version (from package.json), description ...
plugins/claude-code/agentive/skills/<name>/SKILL.md ...      from src/plugin/claude-code/
plugins/claude-code/agentive/hooks/hooks.json ...            from src/plugin/claude-code/
plugins/claude-code/agentive/payload/                        engine/ + repo-layer/ + index.json (one copy)
plugins/codex/agentive/plugin.json                           portable manifest at the plugin root [S14]
plugins/codex/agentive/skills/agentive-{init,update}/        SKILL.md, agents/openai.yaml, payload/ (one copy EACH)
plugins/codex/agentive/skills.lock                           sha256 of both Codex skill dirs (codex_only_bootstrap)
```

Why two plugin directories [설계 판단]: Claude Code and Codex both discover `skills/` at the plugin root, but the
skill names differ (`init` invoked as `/agentive:init` vs `agentive-init` invoked as `$agentive-init`), so one shared
`skills/` would expose wrong names in one tool. Each Codex skill dir is self-contained because the Codex-only
bootstrap copies exactly those two dirs into `.agents/skills/` of a user repo.

## 2. Generator: sources -> outputs

`python3 tools/generate.py` reads `framework.yaml`, `design/framework.yaml`, `design/schemas/`, `package.json`,
`tools/bundles.yaml`, `src/units/*.yaml` and `src/**`, and builds the full output set in memory:

1. units: every `src/units/*.yaml` is validated against `tools/schemas/units.schema.json`; ids are unique; every
   `refs` entry resolves in `design/framework.yaml`; `f`, region ids and `schema` exist in the design.
2. sources: every file under `src/repo-layer/`, `src/engine/`, `src/plugin/*/` belongs to exactly one unit
   (otherwise ERROR orphaned source); naming, header, exec-bit, LF and schema rules (§5) are checked.
3. payload = `engine/` + `repo-layer/` (templates at escaped install paths) + `index.json` (the install map, §3).
4. plugin outputs: generated manifests, plugin units, payload copies, Codex `skills.lock`.
5. marketplaces (`.claude-plugin/marketplace.json`, `.agents/plugins/marketplace.json`), `docs/map/index.html`
   (tools/map/gen.py, unchanged renderer of the design page), `docs/DECISIONS.md`, root AGENTS.md install region.
6. generate-time placeholders `%%agentive:gen.version%%`, `%%agentive:gen.plugin_name%%`,
   `%%agentive:gen.marketplace_name%%` are replaced in every copied text file.

Write mode writes changed outputs, fixes exec bits, deletes ORPHANED files under `generated_roots`
(`plugins/`, `docs/map/`) and empty dirs. `--check` builds the same set, writes nothing, prints one line per
problem and exits 1:

- `ERROR <where>: <what>. NEXT: <fix>`: a source or unit problem (nothing is compared until these are fixed)
- `STALE <path>` (bytes differ), `MISSING <path>`, `ORPHANED <path>` (a file under a generated root no unit
  makes), `MODE <path>` (exec bit), then `generate --check: N ... NEXT: python3 tools/generate.py`

The output is deterministic: no timestamps except `skills.lock verified_at`, which changes only when that skill's
hash changes. `--list` prints every output and its maker (unit id or `builtin:*`).

## 3. Units: how to add a template

Adding a file = (1) put it under the right source root, (2) add one entry to YOUR `src/units/<area>.yaml`,
(3) `python3 tools/generate.py`, (4) `python3 tools/selftest.py`. Field reference: `tools/schemas/units.schema.json`.

```yaml
owner: C
units:
  - id: rl.skill.pr                       # stable, lowercase, dots/dashes; scripts name it in their header
    kind: repo_layer                      # repo_layer | engine | plugin_claude | plugin_codex
    src: src/repo-layer/dot-agents/skills/pr/        # file, or directory ending with '/'
    f: f-pr                               # design spec.repo_layer.files id (repo_layer)
    refs: ["spec.repo_layer.files[f-pr]", "spec.work_loop.pr_skill"]   # quote refs containing [ ]
    # optional: install (path or list of paths), class whole|shared, regions, confirm_scope, schema, when, header
```

- Escaping [설계 판단]: no path component under `src/` (and `fixtures/`) may start with `.`. Store `.agents` as
  `dot-agents`, `.claude` as `dot-claude`, `.github` as `dot-github`, `.gitignore` as `dot-gitignore`. The default
  install path turns every `dot-x` component back into `.x`. Reason: nested `.claude/`, `.github/`, `.gitignore`,
  `AGENTS.md` or `CLAUDE.md` inside this repo would be loaded or obeyed by the agent editing this repo.
  Files named `AGENTS.md` / `CLAUDE.md` are forbidden anywhere under `src/`.
- Shared files (design `init_apply.managed_regions`): a unit with `class: shared`, `src:
  src/repo-layer/<escaped path>.regions/` holding one `<region>.md` (or `.txt`) per region and
  `regions: [<ids>]`. Region ids must exist in the design (`AGENTS.md#context`, `AGENTS.md#path-rules`,
  `CLAUDE.md#imports`, `code_review.md#rules`, `.gitignore#state`). A region file holds only the region body:
  markers and the provenance line are written by the engine.
- One source, several install paths: `install: [a, b, c]` on a single-file unit (for example `stage-check.sh`
  into `chain-intent`, `chain-spec`, `chain-plan`; design: "rendered from one framework source").
- `confirm_scope`: U2 categories (`hooks`, `permissions`, `ci`, `claude_settings`, `codex_dir`, `review_config`).
  The engine asks a human before writing these (and skips + records them in headless runs).
- `when`: a condition the engine evaluates (for example `review.managed_code_review_copy` for REVIEW.md).
- `schema`: the design schema the installed file satisfies; the generator validates the source when it holds no
  install-time placeholder.
- Install-time placeholders `%%agentive:<key>%%` (any key not starting with `gen.`) are left for the engine.
  `<key>` is a dotted path in the agentive manifest or `.agents/framework.yaml` (for example `verbs.test.cmd`) or a
  computed block `render.<name>` produced by the engine (B lists the available `render.*` names in
  `docs/impl/B.md`). The syntax never collides with GitHub `${{ }}`.

`payload/index.json` (generated, consumed by the engine): `{plugin, version, entries: [{unit, owner, class,
confirm_scope, f?, when?, schema?, install, region?, template, executable?}]}` sorted by install path. `template`
is relative to the payload root. The engine never walks `repo-layer/` itself; it installs exactly the entries.

## 4. Run-time choices

| where | runtime | why |
|---|---|---|
| everything installed into user repos (hooks, `.agents/bin/*`, skill scripts, engine) | POSIX `sh` for thin wrappers and git plumbing; `python3` >= 3.9 standard library for JSON/YAML/hash logic; nothing else | stack-agnostic (W1): no Node/Go/Java assumption about the user's stack; python3 is present on macOS (Command Line Tools), common Linux distributions and GitHub-hosted runners (미검증 for every user machine, so scripts fail with a NEXT line when it is missing); hook input is JSON, which `sh` cannot parse safely without `jq` (not guaranteed) [설계 판단] |
| YAML at run time | `.agents/lib/agentive/ayaml.py` (owner A): the agentive YAML subset, YAML 1.2 core scalars, loud `ayaml: line N:` errors | no PyYAML at run time; tested against PyYAML (tests/lib) and on all of design/framework.yaml |
| framework repo tools | python3 + PyYAML + jsonschema (requirements-dev.txt); Node only for `@changesets/cli` | dev-time only; changesets is a constant of the design (F4) |
| network | none at run time; `gh api` only where the design calls it (PR skills, janitor gate) | design: no network allowlist in the repo |
| Windows | Git Bash or WSL (design `verb_wrapper.windows`); no `.ps1` port in v1 | design |

Run-time python scripts start with `#!/usr/bin/env python3`, import only the standard library and
`agentive.*` from `.agents/lib` (`sys.path.insert(0, os.path.join(repo_root, ".agents", "lib"))`), and print
`AGENTIVE_FAIL ...; NEXT=install python3 >= 3.9` style lines when a prerequisite is missing. Shell scripts are
`#!/bin/sh` (POSIX, `set -eu`), no bashisms unless the file is `#!/usr/bin/env bash` and the design needs it.
CI checks the python floor: job `runtime-python-floor` runs tests/lib on Python 3.9.

## 5. Conventions every implementer follows

1. Header: every script and yaml/toml/rules source carries, in its first 3 lines (after a shebang),
   `# agentive-unit: <unit id>` and `# design: <design refs>` (generate fails without the first). JSON and
   Markdown carry no header (JSON has no comments; Markdown is agent context and SKILL.md must start with
   frontmatter); their refs live in the unit entry.
2. Design references: refs are design paths (`spec.guard`, `spec.repo_layer.files[f-guard]`,
   `decisions.accepted[CHAIN]`, `contracts[manifest]`). Source ids appear as `[S14]` in prose. Never invent ids.
3. Files: LF only, UTF-8, executable bit iff shebang, no symlinks in `src/`, JSON written with indent 2 and a
   final newline (`agentive.common.write_json`).
4. Agent-facing output lines (design formats, keep them exact):
   `AGENTIVE_FAIL verb=<v> exit=<n> log=<path>; NEXT=<cmd>` (verb wrapper),
   `BLOCKED <id>: <reason>. NEXT: <next>` (guard), `AGENTIVE_STATE=...` (SessionStart),
   `AGENTIVE_CHAIN=stop:<stage>:<slug> NEXT=<action>` (stage-check), `PENDING`/`DONE`/`CAP` (Stop hook).
   Anything a script prints last for an agent ends with one `NEXT` action.
5. Schemas: every file named in `contracts` is validated against its schema in your component tests
   (use `jsonschema` with the `referencing` registry like tests/design/validate_schemas.py). If a schema blocks
   the design, report it; do not edit `design/schemas/` from an implementation task.
6. Skills: SKILL.md frontmatter writes `disable-model-invocation` explicitly for every skill (design
   `spec.plugin.invocation`); Codex skills carry `agents/openai.yaml` with `allow_implicit_invocation` written
   explicitly. Skill scripts sit in `<skill>/scripts/<slug>.<ext>` and are called skill-root-relative with an
   interpreter (`bash scripts/x.sh` / `python3 scripts/x.py`); no `${CLAUDE_SKILL_DIR}`, no `allowed-tools`
   in skills shared with Codex (design `spec.skill_scripts`). SKILL.md <= 500 lines.
7. Naming: unit ids `<area>.<thing>` (`rl.*` repo layer, `eng.*` engine, `pc.*` Claude plugin, `px.*` Codex
   plugin); test files `tests/<area>/test_<thing>.py`; area dirs = `lib`, `generator`, `design` (A), `engine`,
   `plugin`, `hooks` (B), `content` (C), `review`, `ci`, `sample_repo` (D).
8. Terminology in every file: runs with no human present are only "headless runs (`claude -p`)" / "Routines runs" (tests reject other names),
   "agentive manifest" for `.agents/manifest.yaml`, "package files" for package.json-type files.
9. Changes: one changeset per change (`.changeset/README.md`). The initial import is 0.1.0 without one.

## 6. Self-tests

`python3 tools/selftest.py [step ...]` = CI job `selftest` (`.github/workflows/ci.yml`, on pull_request and push
to main, never scheduled):

| step | what | owner |
|---|---|---|
| generate_check | `tools/generate.py --check` | A |
| schema_validation | `tests/design/validate_schemas.py design/schemas` (port of the design workspace validate.py: meta-validity, sample instances, design regression checks) | A |
| component_tests [설계 판단] | `unittest discover` in every `tests/<area>/` except `design` and `sample_repo` | each owner |
| sample_repo_tests | deterministic init/apply on `fixtures/{ts,py,go,java}` (design FM.selftests): generated files, idempotent re-run changes nothing, user files preserved | D (currently SKIP until D sets `REQUIRE_SAMPLE_REPO_TESTS = True` in tools/selftest.py) |
| version_sync_check | package.json version = both plugin manifests = Codex skills.lock pinned_ref (= CHANGELOG.md head) [S50] | A |

The agent-run half of sample_repo_tests (fixture tasks through the headless standard command, only when
init/apply paths change, 1-2 stacks) needs an API key; D adds it as a separate optional workflow job.

## 7. Ownership map (B, C, D work in parallel after A; never edit another owner's paths)

Shared files nobody but A edits: `tools/**`, `framework.yaml`, `package.json`, `.github/workflows/ci.yml`,
`AGENTS.md`, `README.md`, this file, `src/units/foundation.yaml`, `src/repo-layer/dot-agents/lib/agentive/{__init__,ayaml,common}.py`.
Exception: D flips `REQUIRE_SAMPLE_REPO_TESTS` in `tools/selftest.py` (one line). Need a change in an A file?
Write it in your `docs/impl/<owner>.md` under "Requests to A" and tell the main agent. New python modules for
everyone go in `src/repo-layer/dot-agents/lib/agentive/<your_module>.py` (one module = one owner, listed below).
Each owner keeps `docs/impl/<owner>.md`: status, decisions marked [설계 판단], contracts it offers, open issues.
All of `src/repo-layer/dot-agents/lib/agentive/` is ONE unit, `lib.agentive` (in `src/units/foundation.yaml`):
the modules B, C and D add there (for example D's `review/**` and `lint/**`) need no unit entry of their own; their
headers name `agentive-unit: lib.agentive` and the owner. Ownership stays per module file or subpackage.

Design judgments index (every `[설계 판단]` made while implementing; read the owner file before changing that area):

| owner | file | sections |
|---|---|---|
| A | this file | §1 to §4, §8, §9 (marked inline) |
| B | [docs/impl/B.md](impl/B.md) | §1 placeholders and `when` grammar, §2 engine CLI, §4 design judgments |
| C | [docs/impl/C.md](impl/C.md) | §6 design judgments (13 items) |
| D | [docs/impl/D.md](impl/D.md) | §2 contracts, §3 design judgments, §6 미검증 |

### B: plugin layer + enforcement (units file `src/units/plugin.yaml`)
- `src/plugin/claude-code/**`: skills `init`, `update`, `reprove` (SKILL.md frontmatter per spec.plugin.invocation,
  flags spec.plugin.command_flags), plugin `hooks/hooks.json` + hook scripts (SessionStart notify for an
  uninitialized repo, spec.init_apply.session_start).
- `src/plugin/codex/**`: skills `agentive-init`, `agentive-update` (SKILL.md + `agents/openai.yaml`, allow_implicit_invocation: false).
- `src/engine/**`: the apply engine (evidence reading, slot derive/prove harness, render, managed regions with
  provenance and region hashing, `manifest.generated` baseline, drift report, `--force`, `--status`,
  `.agents/custom/` merge, stale-line check, symlink-or-copy of `.claude/skills|rules|agents` (T1),
  rendering `.agents/agents/*.md` -> `.claude/agents/*.md` and `.codex/agents/*.toml`, `.agents/skills.lock`,
  headless init/update + `pending-confirmation.json`, compat check, U2 confirm_scope prompts, final report lines).
- `src/repo-layer/`: `dot-agents/bin/verb`; `dot-agents/hooks/**` (installed hook scripts [설계 판단 path]:
  PreToolUse guard + context gate + test rewrite, PostToolUse lint, Stop + stop-state + NO-PROGRESS handoff,
  FileChanged -> dirty.json, SessionEnd harvest, SessionStart ready/compact/handoff/pending notices);
  `dot-agents/guard.yaml`; `dot-agents/manifest.yaml` seed; `dot-claude/settings.json` (hooks, permissions incl.
  DENYEDIT, sandbox, env); `dot-codex/{hooks.json,config.toml,rules/agentive.rules}` + execpolicy fixture;
  lib modules `agentive/{engine_api,guard,handoff,nohuman,state}.py` as needed.
- tests: `tests/engine/`, `tests/plugin/`, `tests/hooks/`.
- Engine CLI contract (D's sample_repo_tests call it; keep it stable, document changes in docs/impl/B.md):
  `python3 <payload>/engine/apply.py {init|update|status|reprove} --repo <dir> [--managed-review-copy |
  --no-managed-review-copy] [--force] [--headless] [--confirm-all | --confirm <path>...] [--values <json file>]`.
  Deterministic, no LLM, no network. Exit 0 = done (prints the changed-file list, one path per line after
  `AGENTIVE_CHANGED:`), 1 = entry check stop (init on an initialized repo / update on an uninitialized one, with
  `NEXT=`), 2 = error. The skills run it and do the LLM parts (deriving ambiguous slots into `--values`).

### C: repo-layer content + work-loop skills (units file `src/units/content.yaml`)
- Shared-file regions: `src/repo-layer/AGENTS.md.regions/{context,path-rules}.md`,
  `CLAUDE.md.regions/imports.md`, `code_review.md.regions/rules.md` (default `agentive-review-rules` block, schema
  review-rules; D consumes it), `dot-gitignore.regions/state.txt`; the REVIEW.md copy rule (`when`).
- `src/repo-layer/dot-agents/rules/**` rule packs (`.claude/rules/` rendering is B's engine).
- Repo skills under `src/repo-layer/dot-agents/skills/`: `chain-intent`, `chain-spec`, `chain-plan` (+ one
  `stage-check.sh` source installed three times, templates/intent|spec|plan.md), `pr` (+ `scripts/pr-body.sh`,
  Explainer, agentive-review block), `pr-feedback` (+ `scripts/pr-state.sh`), `distill` (+ `scripts/queue-read.sh`),
  `verify`, `precommit-review`, `janitor` (+ `scripts/gate.sh`); Codex `agents/openai.yaml` for each.
- `dot-agents/decisions.yaml` (U1=C, U2=B, U6=agentive:rubric@1), `dot-agents/rubric.yaml` (preset agentive:rubric@1),
  `docs/adr/` template (f-adr), docs dirs.
- lib modules `agentive/{chain,prbody}.py` if needed. tests: `tests/content/`.

### D: review pipeline + CI + tests (units file `src/units/review.yaml`)
- `src/repo-layer/dot-github/workflows/*.yml`: outer-verbs, ai-review, agentive-independence,
  agentive-review-gate (RV1-RV8), agentive-lint, dependency-review, reviewer-eval.
- `src/repo-layer/dot-agents/bin/{agentive-review,agentive-lint,agentive-reviewer-eval}` [설계 판단 name for the
  last] and lib modules `agentive/review/**` (RV1 signals + chain check, RV2 trust boundary, RV3 classify, RV4
  tiers + raise-only, RV7b rubric score, RV8 gate incl. kill switch, explainer presence, reply lines, finding
  verification plumbing) and `agentive/lint/**`.
- Reviewer prompts `src/repo-layer/dot-agents/agents/{correctness-reviewer,security-reviewer,testing-reviewer,
  verifier,coordinator,classifier,raise-only}.md` (f-reviewers; B's engine renders them for Claude/Codex).
- Reviewer eval: runner, case format docs (schema reviewer-eval-case exists), CI comment summary block.
- `fixtures/{ts,py,go,java}/` (escaped dot paths; lockfiles; no AGENTS.md/CLAUDE.md), `tests/sample_repo/`
  (deterministic init/apply via B's engine CLI contract), `tests/review/`, `tests/ci/`; flips
  `REQUIRE_SAMPLE_REPO_TESTS`. Optional agent-run fixture job in a separate workflow file
  `.github/workflows/selftest-agent-run.yml` (D owns that file).

Split rationale: B owns everything that writes or enforces at tool level (engine, hooks, permissions), C owns
what agents read and run in the work loop (instructions and skills), D owns everything that judges a PR (review
pipeline, CI, eval) plus the tests that exercise B's engine end to end. Cross-owner seams: index.json (A->B),
engine CLI (B->D), `render.*` keys (B->C), review-rules block (C->D), `.agents/agents/*.md` (D->B render),
`agentive-review` PR-body block (C writes via /pr, D reads; schema pr-body-agentive-review).

## 8. Installed layout in a user repo (where things land)

Design-given paths are in `spec.repo_layer.files`. Paths the design leaves open, chosen here [설계 판단]:
`.agents/hooks/<event>-<purpose>.{sh,py}` (hook scripts called from `.claude/settings.json` and `.codex/hooks.json`),
`.agents/lib/agentive/` (run-time library), `.agents/bin/agentive-reviewer-eval`, `.agents/skills.lock` and the
Codex release lock as JSON (valid YAML too; schema skills-lock), and the tree hash `agentive.common.tree_sha256`
(sorted `<relpath>\0<sha256>\n` lines) for both locks.

## 9. Open issues (verify before relying on them)

1. Resolved 2026-10-28: Claude plugin skills reach the shared payload as `${CLAUDE_PLUGIN_ROOT}/payload`; the plugin
   manifest reference says `${...}` resolves "anywhere in the Markdown body" of skill, command and agent content and
   in hook `command`/`args` (code.claude.com/docs/en/plugins-reference, Environment variables). The variable is not in
   the Bash tool environment, so write the substituted path into the command the skill tells Claude to run. Do not
   add a plugin `bin/` directory: claude.ai and Cowork do not install a plugin that has one (same page).
2. Codex marketplace entry `category` and `policy.authentication` values (`ON_INSTALL`) are taken from the Build
   plugins page [S14] as summarized on 2026-10-28; first-use value name and category vocabulary are 미검증.
3. Codex also reads `.claude-plugin/marketplace.json` (legacy-compatible); which file wins when both exist is
   미검증. If Codex picks the Claude entry it would see the Claude plugin directory.
4. Design UV items that implementation must not assume: UV-3 (Codex ignores Claude-only frontmatter keys: T3
   probe), UV-20/UV-27 (region hashing stability), UV-23 (Codex skill argument reading), UV-24 (override merge on
   workflow yaml), UV-25 (Codex and HTML comment markers), UV-39, UV-40.
5. `claude plugin validate .` passes for the marketplace and the Claude plugin (Claude Code 2.1.312,
   2026-10-28). Re-run it after adding skills and hooks.
6. The design's `u3_confirm_paths` did not cover template sources under `src/`; AGENTS.md adds `src/**/hooks/**`,
   `**/dot-agents/hooks/**`, `**/dot-codex/**`, `**/guard.yaml` [설계 판단].
7. User-repo review-rules confirm_scope globs (design example: `**/hooks.json`, `plugins/**/hooks/**`) do not match
   `.agents/hooks/**`; C must include `.agents/hooks/**` in the hooks category of the default block.
8. `framework.yaml` (root, framework-repo-config) and `design/framework.yaml` (design) share a name because the
   design names both so; AGENTS.md states which is which.
