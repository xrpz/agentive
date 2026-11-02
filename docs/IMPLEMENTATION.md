# agentive implementation guide (agent-facing)

Reference for building the framework (§1 to §9). Do not read it top to bottom: pick the section you need from the contents
list. Before adding a template read §3 and §5; before moving files between areas read §7; the dated notes of earlier work
(sections 10 to 35) are in `docs/impl/decisions-log.md` with the same numbers, and the rules from them that still hold for new work
are in §5 (items 10 to 24).
Purpose of agentive: "AI agent가 길을 잃지 않고 쉽게 코드베이스를 이해하며 항상 균일한 high quality output을 내는
AI-native SDLC·codebase framework". Target tools: Claude Code and Codex; which tool implements and which tool reviews
is the user's choice, the framework assigns no roles to tools (CITOOL). Every file here is optimized for the
agent that maintains it.

Authority: `design/framework.yaml` (+ `design/schemas/`) is the design and wins over this file. This file fixes
how the design is implemented where the design names no file or tool; each such choice is marked `[설계 판단]`.
A claim not checked in an official source is marked `미검증`. Do not change the design from an implementation
task: write the gap in the matching `docs/impl/<area>.md` (§7) and tell the author.

Design lookup: `design/INDEX.md` (generated) lists every top-level key, spec key and decision id with its line in
`design/framework.yaml`; `python3 tools/design.py get <path>` prints one node (`spec.guard`, `decisions.accepted[PRADOPT]`,
`spec.repo_layer.files[f-pr]`); `python3 tools/design.py find <text>` lists the ids whose entry holds the text (REVIEWRULES
and DESIGNINDEX, 2026-11-02). Most-used design keys: `spec.repo_layer.files` (every installed file, ids `f-*`),
`spec.init_apply` (init, update, managed regions, drift), `spec.plugin` (commands, invocation), `spec.guard`,
`spec.enforcement`, `spec.stop_hook`, `spec.verb_wrapper`, `spec.spec_stage`, `spec.work_loop`, `spec.review_pipeline`,
`spec.repo_checks`, `spec.maintenance`, `spec.distribution`, `spec.framework_repo`, `contracts` (file -> schema ->
writer/reader).

## Contents

- §1 Directory layout: where each source and each generated output lives
- §2 Generator: what `tools/generate.py` builds, checks and reports
- §3 Units: how to add a template (one file + one unit entry)
- §4 Run-time choices: python3 standard library, POSIX sh, no network
- §5 Conventions every implementer follows (headers, schemas, skills, wording) and the rules that still hold from earlier notes
- §6 Self-tests: the steps of `tools/selftest.py`, the fast subset before a commit, the size budgets
- §7 Areas of the source tree: areas A to D, the index of design judgments
- §8 Installed layout in a user repo: where things land, CI tool, CI credential, CI split
- §9 Open issues: claims to verify before relying on them
- §10 to §35 (`docs/impl/decisions-log.md`): dated implementation notes, same numbers; its own contents list says which § to read

## 1. Directory layout

```
AGENTS.md  CLAUDE.md(@AGENTS.md)  README.md  LICENSE(MIT)      editing rules; CLAUDE.md imports AGENTS.md (FM.rules)
framework.yaml                    framework-repo config (contracts[framework-repo-config]): plugin name, U3, U4
package.json  package-lock.json   version + changesets (the only Node use)        .changeset/   one file per change
requirements-dev.txt              PyYAML, jsonschema: generator and tests only (never at run time)
design/framework.yaml             canonical design            design/schemas/*.schema.json   22 contract schemas
tools/generate.py                 generator CLI (§2)          tools/bundles.yaml   plugin output layout
tools/selftest.py                 self-test runner (§6)       tools/map/gen.py + tpl.html   map page renderer
tools/design.py                   design lookup: get, find, index (DESIGNINDEX)
docs/IMPLEMENTATION.md            this file (sections 1 to 9)   docs/impl/decisions-log.md   dated notes, sections 10 to 35
tools/schemas/units.schema.json   schema of src/units/*.yaml
src/units/<area>.yaml             unit lists: foundation (A), plugin (B), content (C), review (D)
src/repo-layer/                   templates installed into user repos, at their escaped install path (§3)
src/engine/                       init/update/reprove apply engine (python3 stdlib), bundled in every payload
src/plugin/claude-code/           Claude Code plugin files (skills/init|update|reprove, hooks/) without the manifest
src/plugin/codex/                 Codex plugin files (skills/agentive-init|agentive-update) without the manifest
tests/<area>/test_*.py            component tests       tests/design/   schema validation      tests/sample_repo/ (D)
tests/budget/                     size limits of installed text (size_budget step)
fixtures/{ts,py,go,java}/         sample repos for sample_repo_tests (D), dot paths escaped like src/
GENERATED (never edit):  plugins/claude-code/agentive/   plugins/codex/agentive/   .claude-plugin/marketplace.json
                         .agents/plugins/marketplace.json   docs/map/index.html   docs/DECISIONS.md
                         design/INDEX.md   AGENTS.md install-sequence region
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
   (tools/map/gen.py, unchanged renderer of the design page), `docs/DECISIONS.md`, `design/INDEX.md` (tools/design.py
   `render_index`: id -> line from the YAML parse tree, DESIGNINDEX), root AGENTS.md install region.
6. generate-time placeholders `%%agentive:gen.version%%`, `%%agentive:gen.plugin_name%%`,
   `%%agentive:gen.marketplace_name%%` and `%%agentive:gen.headless_cmd%%` (the design's
   `spec.context_management.headless.standard_cmd`, HEADLESSBINS; since HEADLESSHINT it carries
   `--settings '{"sandbox":{"autoAllowBashIfSandboxed":false}}'`) are replaced in every copied text file.
   `%%agentive:gen.install_skill%%` is a per-install value: in a single-file unit with an install list under
   `.agents/skills/<skill>/` it makes one template per install path with that skill's name (SKILLPATHS: stage-check.sh).

Write mode writes changed outputs, fixes exec bits, deletes ORPHANED files under `generated_roots`
(`plugins/`, `docs/map/`) and empty dirs. `--check` builds the same set, writes nothing, prints one line per
problem and exits 1:

- `ERROR <where>: <what>. NEXT: <fix>`: a source or unit problem (nothing is compared until these are fixed)
- `STALE <path>` (bytes differ), `MISSING <path>`, `ORPHANED <path>` (a file under a generated root no unit
  makes), `MODE <path>` (exec bit), then `generate --check: N ... NEXT: python3 tools/generate.py`

The output is deterministic: no timestamps except `skills.lock verified_at`, which changes only when that skill's
hash changes. `--list` prints every output and its maker (unit id or `builtin:*`).

YAML speed [설계 판단, 2026-11-03]: the generator, `tools/design.py`, `tools/map/gen.py` and the tests that read `design/framework.yaml`
(1 MB) load it with PyYAML's libyaml loader (`yaml.CSafeLoader`, and `CSafeDumper` for the dumps in `validate_schemas.py` and
`design.py get`) when PyYAML was built with it, and with the pure Python `SafeLoader` otherwise. The data is the same (checked:
`generate.py` writes 0 changes, `design.py get/find/index` print the same bytes); a parse takes 0.1 s instead of 1.1 s.
`tools/design.py` parses the tree once per process (`_tree`), so `render_index` no longer composes the file twice. The installed
runtime does not import PyYAML (`ayaml`), so nothing installed changed. `validate_schemas.py` dumps with `width=10**9` so a phrase is
never folded over two lines before its substring check.

## 3. Units: how to add a template

Adding a file = (1) put it under the right source root, (2) add one entry to the matching `src/units/<area>.yaml` (§7),
(3) `python3 tools/generate.py`, (4) `python3 tools/selftest.py`. Field reference: `tools/schemas/units.schema.json`.

```yaml
owner: C                                  # area label (§7); required by the schema, copied into payload/index.json
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
  markers and the constant notice line are written by the engine (no date, no sha: REGIONNOSHA).
- One source, several install paths: `install: [a, b, c]` on a single-file unit (for example `stage-check.sh`
  into `chain-intent`, `chain-spec`, `chain-plan`; design: "rendered from one framework source"). A source that holds
  `%%agentive:gen.install_skill%%` gets one template per install path, at the escaped install path (§2 item 6).
- `confirm_scope`: U2 categories (`hooks`, `permissions`, `ci`, `claude_settings`, `codex_dir`, `review_config`).
  Informational since INITWRITE (2026-10-29): the engine no longer asks before writing them; the category still
  names the files that `--no-hooks` skips (`hooks`) and the files the commit-time U2 rule guards.
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
| everything installed into user repos (hooks, `.agents/bin/*`, skill scripts, engine) | POSIX `sh` for thin wrappers and git plumbing; `python3` >= 3.13 standard library for JSON/YAML/hash logic; nothing else | stack-agnostic (W1): no Node/Go/Java assumption about the user's stack; python3 is present on macOS (Command Line Tools), common Linux distributions and GitHub-hosted runners (미검증 for every user machine, so scripts fail with a NEXT line when it is missing); hook input is JSON, which `sh` cannot parse safely without `jq` (not guaranteed) [설계 판단] |
| YAML at run time | `.agents/lib/agentive/ayaml.py` (area A): the agentive YAML subset, YAML 1.2 core scalars, loud `ayaml: line N:` errors | no PyYAML at run time; tested against PyYAML (tests/lib) and on all of design/framework.yaml |
| framework repo tools | python3 + PyYAML + jsonschema (requirements-dev.txt); Node only for `@changesets/cli` | dev-time only; changesets is a constant of the design (F4) |
| network | none at run time; `gh api` only where the design calls it (PR skills, janitor gate) | design: no network allowlist in the repo |
| Windows | Git Bash or WSL (design `verb_wrapper.windows`); no `.ps1` port in v1 | design |

Run-time python scripts start with `#!/usr/bin/env python3`, import only the standard library and
`agentive.*` from `.agents/lib` (`sys.path.insert(0, os.path.join(repo_root, ".agents", "lib"))`), and print
`AGENTIVE_FAIL ...; NEXT=install python3 >= 3.13` style lines when a prerequisite is missing. Shell scripts are
`#!/bin/sh` (POSIX, `set -eu`), no bashisms unless the file is `#!/usr/bin/env bash` and the design needs it.
CI checks the python floor: job `runtime-python-floor` runs tests/lib and tests/review on Python 3.13 (ubuntu-latest) and job
`runtime-python-latest` runs the same on 3.15, the newest Python on ubuntu-latest (decisions-log §28). The floor is 3.13 by user decision
2026-11-01 (decisions.accepted[PYFLOOR]); the code does not use 3.10+ features yet, only the promise and its checks changed. Every installed entry point checks the floor itself and prints the engine's `NEXT=install python3 >= 3.13` (decisions-log §29, E2E-46).

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
   explicitly. Skill scripts sit in `<skill>/scripts/<slug>.<ext>` and are called from the repository root,
   repo-root relative with an interpreter (`sh .agents/skills/<skill>/scripts/<x>.sh`), never through `..`; every
   SKILL.md says so in one line under its heading (SKILLPATHS, 2026-10-29); no `${CLAUDE_SKILL_DIR}`, no `allowed-tools`
   in skills shared with Codex (design `spec.skill_scripts`). SKILL.md <= 500 lines.
7. Naming: unit ids `<area>.<thing>` (`rl.*` repo layer, `eng.*` engine, `pc.*` Claude plugin, `px.*` Codex
   plugin); test files `tests/<area>/test_<thing>.py`; area dirs = `lib`, `generator`, `design` (A), `engine`,
   `plugin`, `hooks` (B), `content` (C), `review`, `ci`, `sample_repo` (D), `budget` (A: size limits of installed text, run by its own self-test step).
8. Terminology in every file: runs with no human present are only "headless runs (`claude -p`)" / "Routines runs" (tests reject other names),
   "agentive manifest" for `.agents/manifest.yaml`, "package files" for package.json-type files.
9. Changes: one changeset per change (`.changeset/README.md`). The initial import is 0.1.0 without one.

Rules that still hold from the dated notes (copied from `docs/impl/decisions-log.md`; the § says where the reasons are):

10. JSON files that users and other tools also edit (`.claude/settings.json`, `.codex/hooks.json`): the keys agentive owns are
    listed in `OWNED` of `src/engine/jsonmerge.py`; a template key outside that list is added only when absent and never
    updated. A new hook script lives under `.agents/hooks/` (that path is the ownership marker) and exits 0 on the smoke input
    (`Engine.SMOKE_INPUT`), otherwise `/agentive:init` leaves settings.json unwritten. The engine writes every unit file without
    asking: put a risky behavior behind `--no-hooks` (`confirm_scope: [hooks]`), not behind a prompt (decisions-log §10).
11. Managed-region budget: about 20 generated lines in the managed regions, 200 lines per AGENTS.md or CLAUDE.md, and an
    `agentive-lint size` warning at 24 KiB of AGENTS.md files on one path. `render.orientation` and the region templates carry
    commands, test locations and pointers to documents that exist; never a generated list of directories or modules (decisions-log §10).
    `python3 tools/selftest.py size_budget` checks the installed result (§6).
12. A python entry point under `src/repo-layer/` or `src/engine/` sets `sys.dont_write_bytecode = True` before it imports
    `agentive`; a sh shim runs `python3 -B`, so a user's `.agents/` never gets a `__pycache__` (decisions-log §13).
13. A line a hook adds to the agent's context ends with `NEXT=`; `systemMessage` is for the human only. A hook that runs on every
    tool call stays silent unless it has something to say, and a hint adds `additionalContext`, never a `permissionDecision` (decisions-log §13, §23).
14. RV3 tier-0 classes come only from the mechanical checks in `review/rv3.py` (`whitespace_only_texts`, `_is_doc`,
    `_manifest_class`, `NOT_DOCS`); the local estimate in `prbody.py` calls the same functions (decisions-log §13).
15. A fact about Codex taken from the official openai/codex GitHub source is marked with the file and commit; source code is not
    documentation, so a release can change it (decisions-log §13).
16. The project's own tools never check `.agents`, `.claude`, `.codex`: a derived repo-wide command carries the tool's additive
    exclude (`evidence.OWNED_DIRS`, `evidence.owned_exclude`) and `lintscope.select_files` never hands a file under those
    directories to the project's linter (decisions-log §15).
17. The text of the AI step's execution file is read only inside `llmstate.classify_claude` and never returned, stored or printed;
    the tests plant a secret-shaped string and grep every output for it (decisions-log §16).
18. To change the security-path floor edit `config.DEFAULT_RULES` and `src/repo-layer/code_review.md.regions/rules.md` together
    (a test compares them); `/agentive:update` refreshes the managed region and the report names the entries that changed (decisions-log §12).
19. No private working-file names in `design/`, `src/`, `docs/`, `tests/` or `tools/` (checked by
    `tests/design/validate_schemas.py`); a public GitHub URL that contains `context/<file>.md` is allowed (decisions-log §11).
20. A skill step becomes a skill script when its failure changes the outcome or a model-written string goes through shell quoting;
    judgment steps stay prose. Scripts are POSIX `sh` plus `gh api`, sit in `<skill>/scripts/`, are called repo-root relative and
    need no unit entry; skill text carries no dates (decisions-log §25).
21. Git calls made by the installed layer need neither a user identity nor a signing key (the backup commit uses the fixed
    `verb.BACKUP_IDENT`), and `tests/hooks` run in an isolated git environment (`no_identity_env`) so CI without a git identity is
    covered locally (decisions-log §28).
22. Labels added with `GITHUB_TOKEN` start no workflow run, so a token that has to start one (the janitor label) is a different
    token (decisions-log §11).
23. Regression tests are named after the decision or finding id (`test_e2e_44_*`, `test_v2_01_*`, `test_reviewrules_*`) and the
    design record names where they are.
24. Section references: `docs/IMPLEMENTATION.md §N` means a section of this file (1 to 9), `docs/impl/decisions-log.md §N` one of
    the log (10 to 35). `tests/generator/test_doc_refs.py` fails on a reference to a section that does not exist and on a
    contents list that misses a heading.

## 6. Self-tests

`python3 tools/selftest.py` runs every step in order and is what CI does (job `selftest` in `.github/workflows/ci.yml`, one step per
row below, on pull_request and push to main, never scheduled). `python3 tools/selftest.py <step> ...` runs the named steps. Every step
line ends with its elapsed time and the last line carries the total (a full run is about eight minutes on two cores: 15m37s with the areas one after the other, 7m31s with them side by side, measured 2026-11-03; the longest area, engine, bounds it).

| step | what |
|---|---|
| generate_check | `tools/generate.py --check` |
| schema_validation | `tests/design/validate_schemas.py design/schemas` (port of the design workspace validate.py: meta-validity, sample instances, design regression checks) |
| size_budget [설계 판단] | `tests/budget/`: installs the four sample repos and checks the installed AGENTS.md after `@`-import expansion (at most 200 lines and 32 KiB), the skill descriptions (1,536 characters each, 8,000 together per tool) and this repo's AGENTS.md (200 lines); the numbers and their pages are in `decisions.accepted[SIZEBUDGET]`; it prints the measured values (`SIZE ...` lines); `tests/budget/test_deny_coverage.py` [설계 판단] fails when a generated output (`python3 tools/generate.py --list`, plus `CHANGELOG.md` and `package-lock.json`) has no `Edit(...)` rule in `permissions.deny` of `.claude/settings.json`, and names the line to add. The list stays hand-maintained; Edit rules also cover Write and NotebookEdit (Claude Code permissions page), so there are no `Write(...)` rules. `AGENTS.md` is the one exception (only its install-sequence region is generated; `PARTLY_GENERATED` in the test). The design does not list the deny entries, so no design text changed |
| component_tests [설계 판단] | `unittest discover` in every `tests/<area>/` except `design`, `sample_repo` and `budget` (they have their own steps). The areas run as separate processes at the same time, at most `os.cpu_count()` at once, the slowest first (`SLOWEST_FIRST`); the step's lines list the areas in sorted order, each with its time, whichever finished first. Areas share no state: every test makes its own `tempfile.mkdtemp` directories and none writes into the repo. A failing area prints the tail of its output; the other areas still finish and report. Same step id, same exit code, same `--fast`/`--plan` |
| sample_repo_tests | deterministic init/apply on `fixtures/{ts,py,go,java}` (design FM.selftests): generated files, idempotent re-run changes nothing, user files preserved; `REQUIRE_SAMPLE_REPO_TESTS = True` in tools/selftest.py makes a missing test directory a failure, not a skip |
| version_sync_check | package.json version = both plugin manifests = Codex skills.lock pinned_ref (= CHANGELOG.md head) [S50] |

Before a commit run `python3 tools/selftest.py --fast` (SELFTESTFAST, 2026-11-02) [설계 판단 for the mapping]: it runs generate_check,
schema_validation, size_budget and version_sync_check always, component_tests only for the `tests/<area>` directories that the changed
files (staged, unstaged and untracked against HEAD) belong to, and sample_repo_tests only when `fixtures/` or `tests/sample_repo/`
changed. The mapping is `FAST_RULES` in tools/selftest.py. It runs the full set instead when a changed path is under `src/repo-layer/`,
`src/engine/`, `src/plugin/`, `src/units/` or `design/` (the engine, review, content and hooks tests install the templates into
temporary copies, so an area boundary does not hold there), when a path matches no rule, or when git cannot list the files. Generated
outputs, versions and changesets add nothing: generate_check and version_sync_check cover them. `--plan` prints the plan and the reason
and runs nothing. Run the full set before opening a PR and for a release; CI runs it on every PR, so a mapping that is too narrow is
caught there. When you add a path that belongs to one area, add its rule to `FAST_RULES` (a test lists the cases).

Shared test fixtures [설계 판단, 2026-11-03]: a test that only reads or edits its own copy builds the expensive part once and copies it.
`tests/generator/test_generate.py` generates one tree per class (`setUpClass`) and copies it for each test; `tests/content/helpers.py`
`Sandbox` builds the origin + clone + installed skills once per distinct `files` and copies that (the copy's `origin` is pointed at its
own `origin.git`); `tests/budget` asks `generate.py --list` once per process and runs the four sample-repo installs side by side. Every
test keeps its own directory and every assertion; a test that must watch a first generate or a first init (the engine, sample_repo and
`StaleIndex` tests) still runs it itself. The reviewer-eval flows (`tests/review/test_lint_eval.py`) were measured and left as they are:
building the repository is 0.15 s of a 7.2 s flow, the rest is the two `run --side` calls and the stand-in tool.

The agent-run half of sample_repo_tests (fixture tasks through the headless standard command, only when
init/apply paths change, 1-2 stacks) needs an API key; it is the separate optional workflow job
`.github/workflows/selftest-agent-run.yml`.

## 7. Areas of the source tree

The sources were built in four areas. The letters are labels, not people or edit rights: they appear as `owner:` in
`src/units/*.yaml` (required by the units schema, copied into `payload/index.json`), in `docs/impl/<letter>.md` and in a few
comments. One editing agent works on the whole repo under AGENTS.md; any file in any area may be edited by it. New python
modules for everyone go in `src/repo-layer/dot-agents/lib/agentive/<module>.py`.
All of `src/repo-layer/dot-agents/lib/agentive/` is ONE unit, `lib.agentive` (in `src/units/foundation.yaml`): the modules of
areas B, C and D added there (for example `review/**` and `lint/**`) need no unit entry of their own; their headers name
`agentive-unit: lib.agentive` and the area.

Design judgments index (every `[설계 판단]` made while implementing; read the area file before changing that area):

| area | file | sections |
|---|---|---|
| A foundation | this file | §1 to §4, §8, §9 (marked inline) |
| B plugin layer and enforcement | [docs/impl/B.md](impl/B.md) | §1 placeholders and `when` grammar, §2 engine CLI, §4 design judgments |
| C repo-layer content and work-loop skills | [docs/impl/C.md](impl/C.md) | §5 design judgments (13 items) |
| D review pipeline, CI and tests | [docs/impl/D.md](impl/D.md) | §2 contracts, §3 design judgments, §5 미검증 |

### Area B: plugin layer + enforcement (units file `src/units/plugin.yaml`)
- `src/plugin/claude-code/**`: skills `init`, `update`, `reprove` (SKILL.md frontmatter per spec.plugin.invocation,
  flags spec.plugin.command_flags), plugin `hooks/hooks.json` + hook scripts (SessionStart notify for an
  uninitialized repo, spec.init_apply.session_start).
- `src/plugin/codex/**`: skills `agentive-init`, `agentive-update` (SKILL.md + `agents/openai.yaml`, allow_implicit_invocation: false).
- `src/engine/**`: the apply engine (evidence reading, slot derive/prove harness, render, managed regions with
  provenance and region hashing, `manifest.generated` baseline, drift report, `--force`, `--status`,
  `.agents/custom/` merge, stale-line check, symlink-or-copy of `.claude/skills|rules|agents` (T1),
  rendering `.agents/agents/*.md` -> `.claude/agents/*.md` and `.codex/agents/*.toml`, `.agents/skills.lock`,
  headless init/update + `pending-confirmation.json` (written but not yet effective), compat check, key-level JSON merge (jsonmerge.py), hook smoke check, final report lines).
- `src/repo-layer/`: `dot-agents/bin/verb`; `dot-agents/hooks/**` (installed hook scripts [설계 판단 path]:
  PreToolUse guard + context gate + test rewrite, PostToolUse lint, Stop + stop-state + NO-PROGRESS handoff,
  FileChanged -> dirty.json, SessionEnd harvest, SubagentStart/SubagentStop observation (`subagent-observe.py`), SessionStart ready/compact/handoff/pending notices; the compact re-inject is five capped sections, decisions-log §34);
  `dot-agents/guard.yaml`; `dot-agents/manifest.yaml` seed; `dot-claude/settings.json` (hooks, permissions incl.
  DENYEDIT, sandbox, env); `dot-codex/{hooks.json,config.toml,rules/agentive.rules}` + execpolicy fixture;
  lib modules `agentive/{engine_api,guard,handoff,nohuman,state}.py` as needed.
- tests: `tests/engine/`, `tests/plugin/`, `tests/hooks/`.
- Engine CLI contract (the sample_repo_tests of area D call it; keep it stable, document changes in docs/impl/B.md):
  `python3 <payload>/engine/apply.py {init|update|status|reprove} --repo <dir> [--managed-review-copy |
  --no-managed-review-copy] [--force] [--headless] [--no-hooks] [--values <json file>]`.
  Deterministic, no LLM, no network. Exit 0 = done (prints the changed-file list, one path per line after
  `AGENTIVE_CHANGED:`), 1 = entry check stop (init on an initialized repo / update on an uninitialized one, with
  `NEXT=`), 2 = error. The skills run it and do the LLM parts (deriving ambiguous slots into `--values`).

### Area C: repo-layer content + work-loop skills (units file `src/units/content.yaml`)
- Shared-file regions: `src/repo-layer/AGENTS.md.regions/{context,path-rules}.md`,
  `CLAUDE.md.regions/imports.md`, `code_review.md.regions/rules.md` (default `agentive-review-rules` block, schema
  review-rules; D consumes it), `dot-gitignore.regions/state.txt`; the REVIEW.md copy rule (`when`).
- `src/repo-layer/dot-agents/rules/**` rule packs (`.claude/rules/` rendering is B's engine).
- Repo skills under `src/repo-layer/dot-agents/skills/`: `chain-intent`, `chain-spec`, `chain-plan` (+ one
  `stage-check.sh` source installed three times, templates/intent|spec|plan.md), `pr` (+ `scripts/pr-body.sh`,
  Explainer, agentive-review block), `pr-feedback` (+ `scripts/pr-state.sh`, `scripts/pr-reply.sh`), `distill` (+ `scripts/queue-read.sh`),
  `verify`, `precommit-review`, `janitor` (+ `scripts/gate.sh`, `scripts/label-pr.sh`); Codex `agents/openai.yaml` for each.
- `dot-agents/rubric.yaml` (preset agentive:rubric@2, @1 pinnable, decisions-log §16; the former `decisions.yaml` is gone: DEFAULTS, decisions-log §11),
  `docs/adr/` template (f-adr), docs dirs.
- lib modules `agentive/{chain,prbody}.py` if needed. tests: `tests/content/`.
- `/pr` draft tier-2 reasons (E2E-58, 2026-11-02) [설계 판단]: up to 3 reasons print as they are; more are grouped by code
  (`trust_boundary:<why>`, `check_config`, `dependency_change`, ...) with 3 examples per group and `and N more`, at most 6 groups
  then `and K more groups`, in the Explainer's first bullet and the `REASONS` line (`prbody.summarize_reasons`). The full list is
  written to `.agents/state/pr-tier2-reasons.txt` (printed as `REASONS_FILE=`). The design fixes no format for the bullet
  (`work_loop.pr_explainer.contents`: "and the tier 2 reason"); `check`, `merge_lines` (takes the code before the first colon) and CI
  (recomputes the estimate, report lists every `trust_boundary_hits` path) do not read the bullet. The limits are choices, not design.

### Area D: review pipeline + CI + tests (units file `src/units/review.yaml`)
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
  `.github/workflows/selftest-agent-run.yml`.

Split rationale: B covers everything that writes or enforces at tool level (engine, hooks, permissions), C covers
what agents read and run in the work loop (instructions and skills), D covers everything that judges a PR (review
pipeline, CI, eval) plus the tests that exercise B's engine end to end. Seams between areas: index.json (A->B),
engine CLI (B->D), `render.*` keys (B->C), review-rules block (C->D), `.agents/agents/*.md` (D->B render),
`agentive-review` PR-body block (C writes via /pr, D reads; schema pr-body-agentive-review).

## 8. Installed layout in a user repo (where things land)

Design-given paths are in `spec.repo_layer.files`. Paths the design leaves open, chosen here [설계 판단]:
`.agents/hooks/<event>-<purpose>.{sh,py}` (hook scripts called from `.claude/settings.json` and `.codex/hooks.json`),
`.agents/lib/agentive/` (run-time library), `.agents/bin/agentive-reviewer-eval`, `.agents/skills.lock` and the
Codex release lock as JSON (valid YAML too; schema skills-lock), and the tree hash `agentive.common.tree_sha256`
(sorted `<relpath>\0<sha256>\n` lines, `__pycache__` directories skipped since V2-10) for both locks. The root AGENTS.md
install region prints a standalone one-line check of the same hash for the Codex-only bootstrap (V2-24) [설계 판단].

### CI tool follows the user's agent (CITOOL, 2026-10-29) [설계 판단 for the key names]

The framework assigns no roles to tools. The CI AI review runs on the effective tool: `review.pipeline.ci_tool`
(`claude` | `codex`, optional) in `.agents/framework.yaml`, else the agentive manifest `framework.init_tool` (the tool
that ran init: `/agentive:init` -> claude, `$agentive-init` -> codex; a manifest without it counts as claude), else
claude. One function decides everywhere: `agentive.review.config.ci_tool` (engine rendering, `agentive-reviewer-eval`,
`agentive-review` records). The engine renders the ai-review LLM step of `.github/workflows/agentive-review.yml`
(sources `src/engine/ci/llm-step-{claude,codex}.yml`) and the install and secret lines of `reviewer-eval.yml`;
details in docs/impl/B.md (render.ci_* keys, `--tool`) and docs/impl/D.md (reviewer eval, 미검증 list).

### Claude CI credential (CIAUTH, E2E-12, 2026-10-30) [설계 판단 for the key name and the default]

The claude CI path takes both Claude credentials (user "1번으로 하자", then "둘 다 지원하게 해줘"; code.claude.com/docs/en/github-actions,
"Add an authentication secret"): the secret `ANTHROPIC_API_KEY` (Claude Console API key, action input `anthropic_api_key`) and
`CLAUDE_CODE_OAUTH_TOKEN` (subscription token from `claude setup-token`, input `claude_code_oauth_token`). Optional key
`review.pipeline.ci_auth: both | api_key | oauth_token`, default `both`; one table decides everywhere:
`agentive.review.config.CI_CREDENTIALS`, `ci_auth`, `ci_credentials`, `ci_input_expr` (engine rendering of `src/engine/ci/llm-step-claude.yml`
and the reviewer-eval secret lines, `settings --effective`, config validation). OAuth token first (E2E-12, user "1번으로 (OAuth token 우선)",
2026-10-30): with both secrets set only the token is passed: `anthropic_api_key` and the env line `AGENTIVE_CI_API_KEY` get
`${{ secrets.CLAUDE_CODE_OAUTH_TOKEN == '' && secrets.ANTHROPIC_API_KEY || '' }}`, because Claude Code itself would use the API key
(Authentication precedence, code.claude.com/docs/en/authentication), and `agentive-reviewer-eval` maps no key when a token is set;
`api_key` or `oauth_token` renders only that input and env line, with the secret as is.
`agentive-reviewer-eval` maps the step env `AGENTIVE_CI_OAUTH_TOKEN` to `CLAUDE_CODE_OAUTH_TOKEN` for the one `claude -p` run (never
`--bare`, which does not read it). The init/update report names both secrets and when to use which (subscription: the token; a secret
shared across repositories: the API key) and says "OAuth token first". The codex path ignores the key. Tests: `test_e2e_12_*` in tests/engine/test_engine.py,
tests/ci/test_workflows.py, tests/review/test_stages.py, and the E2E-12 block of tests/design/validate_schemas.py; the OAuth mapping in
tests/review/test_lint_eval.py. 미검증: which credential claude-code-action uses when both inputs are non-empty (the docs are silent; the
rule follows the Claude Code precedence because the action exports both variables, read from its source), and a reviewer-eval run
with the OAuth token on a real repository; GitHub's evaluation of the `== '' && ... || ''` expression with secrets in `with:` (taken from the
expressions documentation, not observed in a run with both secrets).

### CI split, bot and human PRs, toolchain steps, independence in CI (CISPLIT, BOTPR, CISETUP, INDEPCI, 2026-10-29) [설계 판단 for the names and values]

- CISPLIT: PR code runs only in `outer-verbs` (read-only token, no secret, no persisted credentials). `ai-review` reads its
  results (artifact `agentive-outer-verbs-<head sha>`) and has no execution tool on either tool path; `id-token: write`
  is rendered only for claude-code-action; the gate verifies a sha256 of the LLM task outputs. Job outputs reach scripts
  through `env:`.
- BOTPR: bot PRs (author type Bot or login `[bot]`) and human PRs without an `agentive-review` block skip the independence
  and heading check, are raised to tier 1 and always ask a human (`bot_pr_exempt`, `human_pr_exempt`); a skipped
  `agentive-independence` job counts as failed in the gate (`checks --must-run`); `.github/pull_request_template.md` has the
  three headings. Dependabot and fork PRs have no Actions secrets, so RV8 ends human_required.
- CISETUP: `actions/setup-*` steps per ecosystem (`src/engine/cisetup.py`, versions from the repo's version files, step ids
  `toolchain-*`); `.agents/custom/` list items may use `before:` / `after:` to insert next to a generated step.
- INDEPCI: independence is enforced in CI (only a review the CI job produced for the head sha counts) and attested locally
  (honest, advisory record fields; `isolated` only when a SubagentStart/SubagentStop hook saw the agent).
- The local rule that picked the review tool for the user (`both_tools`) is removed (CITOOL revision, user "지워").

Details: docs/impl/D.md §3 items 17-21, docs/impl/B.md §4 items 16-17.

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
4. Design UV items that implementation must not assume: UV-3 (Codex ignores Claude-only frontmatter keys; answered
   2026-10-29 from the official openai/codex source, codex-rs/skills/src/parser.rs at 23934d31, no probe run), UV-20/UV-27 (region hashing stability), UV-23 (Codex skill argument reading), UV-24 (override merge on
   workflow yaml), UV-25 (Codex and HTML comment markers), UV-39, UV-40.
5. `claude plugin validate .` passes for the marketplace and the Claude plugin (Claude Code 2.1.312,
   2026-10-28). Re-run it after adding skills and hooks.
6. The design's `u3_confirm_paths` did not cover template sources under `src/`; AGENTS.md adds `src/**/hooks/**`,
   `**/dot-agents/hooks/**`, `**/dot-codex/**`, `**/guard.yaml` [설계 판단].
7. User-repo review-rules confirm_scope globs (design example: `**/hooks.json`, `plugins/**/hooks/**`) do not match
   `.agents/hooks/**`; C must include `.agents/hooks/**` in the hooks category of the default block.
8. `framework.yaml` (root, framework-repo-config) and `design/framework.yaml` (design) share a name because the
   design names both so; AGENTS.md states which is which.
9. CITOOL codex path (2026-10-29), all 미검증 on a real repository: `codex exec` inside `openai/codex-action` loading the
   project `.codex/agents/*.toml` custom agents with fresh contexts (UV-15), `sandbox: workspace-write` versus the newer
   `permission-profile`, the action's failure behavior on a blocking finding (UV-6). Full list: docs/impl/D.md §5.
10. CISPLIT / INDEPCI (2026-10-29), 미검증 on a real repository: the SubagentStart/SubagentStop hook input fields, the action
    `session_id` outputs, the `github_token` path without `id-token: write`, `python-version-file: pyproject.toml`. List:
    docs/impl/D.md §5.
11. Resolved 2026-11-01 (user, "README에 3.13으로 변경하고 우리 최소 버전을 3.13으로 정하자"): the run-time floor is `python3 >= 3.13`
    (§4, decisions-log §28); Python 3.9 was past upstream end of life. 미검증: that every user machine has python3 >= 3.13 (macOS Command Line Tools
    ship an older python3; the scripts then stop with `NEXT=install python3 >= 3.13`).
