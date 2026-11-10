#!/usr/bin/env python3
# agentive-unit: eng.apply
# design: spec.init_apply (entry_rule, update, managed_regions, no_preview, headless_init_update, report, reprove_on_change, probes.T1), spec.repo_layer.files[f-manifest], spec.repo_layer.files[f-skills], spec.repo_layer.files[f-reviewers], contracts[manifest], contracts[drift-report], contracts[pending-confirmation], contracts[skills-lock], decisions.accepted[DEPHUMAN], docs/impl/B.md §2
"""agentive apply engine: init, update, status and reprove of the repo layer (python3 >= 3.13 stdlib, no network).

  python3 <payload>/engine/apply.py {init|update|status|reprove} --repo <dir>
      [--managed-review-copy | --no-managed-review-copy] [--force] [--headless] [--no-hooks]
      [--values <json>] [--accept <id>...] [--tool claude|codex]
      [--prove] [--today YYYY-MM-DD] [--print-evidence]
(--today is accepted and unused: no tracked file holds a date since REGIONNOSHA, E2E-09.)

Exit 0 = done (`AGENTIVE_CHANGED:` then one changed path per line), 1 = entry check stop (`AGENTIVE_STOP ...; NEXT=`),
2 = error (`AGENTIVE_ERROR ...; NEXT=`). Deterministic: the LLM parts (ambiguous slots, seeds for proofs, the
REVIEW.md question) are done by the plugin skills, which pass the results as flags.
The engine installs exactly the entries of <payload>/index.json; it never walks repo-layer/ itself.
"""
import argparse
import datetime
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True  # no __pycache__ in the user's repo (V1-08)
HERE = os.path.dirname(os.path.abspath(__file__))
PAYLOAD = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(PAYLOAD, "repo-layer", "dot-agents", "lib"))

if sys.version_info < (3, 13):
    print("AGENTIVE_ERROR python3 < 3.13; NEXT=install python3 >= 3.13")
    sys.exit(2)

from agentive import ayaml, common, guard as gd, nohuman, rulesync  # noqa: E402
from agentive.review import config as reviewcfg  # noqa: E402
import cisetup  # noqa: E402
import evidence  # noqa: E402
import jsonmerge  # noqa: E402
import prove  # noqa: E402
import regions  # noqa: E402
import render  # noqa: E402
import secscan  # noqa: E402

VERB_ORDER = evidence.VERB_ORDER
CATEGORIES = ["hooks", "permissions", "ci", "claude_settings", "codex_dir", "review_config"]
DENY_READ = ["Read(.env)", "Read(.env.*)", "Read(./secrets/**)", "Read(~/.ssh/**)", "Read(~/.aws/**)", "Read(~/.gnupg/**)",
             "Read(~/.netrc)", "Read(~/.config/gh/**)", "Read(~/.kube/**)"]
DENY_EDIT_CREDENTIALS = ["Edit(~/.ssh/**)"]
# E2E-48 [design judgment]: build-output patterns of the stack found by evidence, written into the `.gitignore#state` region next to
# the state directories (github/gitignore Python.gitignore: `__pycache__/`, `*.egg-info/`). Nothing a project commits on purpose;
# `dist/` and `build/` stay out on purpose (they are generated-path evidence, evidence._paths).
GITIGNORE_STACK = {"python": "__pycache__/\n*.egg-info/"}
FIRST_REGION = {"AGENTS.md": "context", "CLAUDE.md": "imports", "code_review.md": "rules", ".gitignore": "state"}
COMMANDS = {("init", "claude"): "/agentive:init", ("update", "claude"): "/agentive:update", ("reprove", "claude"): "/agentive:reprove",
            ("init", "codex"): "$agentive-init", ("update", "codex"): "$agentive-update", ("status", "claude"): "/agentive:update --status",
            ("status", "codex"): "$agentive-update --status", ("reprove", "codex"): "$agentive-update"}
# CITOOL (decisions.accepted, user 2026-10-29): the CI AI review tool follows the user's agent. Per tool: the npm package of
# the CLI (reviewer-eval installs it), the repository secret, and the engine-side source of the ai-review LLM step.
CI_TOOLS = {
    "claude": {"name": "Claude Code", "package": "@anthropic-ai/claude-code", "step": "ci/llm-step-claude.yml"},
    "codex": {"name": "Codex", "package": "@openai/codex", "step": "ci/llm-step-codex.yml"},
}
# CIAUTH (decisions.accepted, user 2026-10-30): the step env name the reviewer-eval runner maps to the variable its tool reads
CI_EVAL_ENV = {"api_key": "AGENTIVE_CI_API_KEY", "oauth_token": "AGENTIVE_CI_OAUTH_TOKEN"}


def ci_llm_step_text(tool, auth=None):
    """The ai-review LLM step (one YAML list item) for the CI tool, without the header comments of its source file.
    CIAUTH: the source passes every credential input of the tool; the inputs of a kind that review.pipeline.ci_auth
    leaves out are removed (default both: all stay), and every kept input gets the value of reviewcfg.ci_input_expr
    (E2E-12: with both, the API key only when the OAuth token secret is empty)."""
    with open(os.path.join(HERE, CI_TOOLS[tool]["step"]), encoding="utf-8", newline="") as f:
        lines = f.read().splitlines()
    while lines and lines[0].startswith("#"):
        lines.pop(0)
    auth = auth or reviewcfg.ci_auth({}, tool)
    keep = {c["input"]: k for k, c in reviewcfg.ci_credentials(tool, auth)}
    drop = {c["input"] for c in reviewcfg.CI_CREDENTIALS[tool].values()} - set(keep)
    out = []
    for ln in lines:
        name = ln.strip().split(":", 1)[0]
        if name in drop:
            continue
        if name in keep:
            ln = ln[:len(ln) - len(ln.lstrip())] + f"{name}: {reviewcfg.ci_input_expr(tool, auth, keep[name])}"
        out.append(ln)
    return "\n".join(out)


def ci_secret_blocks(tool, auth=None):
    """render.ci_secrets (the secret names, for prose), render.ci_secret_check (the expression of the reviewer-eval
    HAS_KEY env: any of the secrets is set) and render.ci_secret_env (the step env lines, one per credential kind, with the
    values of reviewcfg.ci_input_expr: OAuth token first, E2E-12) [CIAUTH]."""
    auth = auth or reviewcfg.ci_auth({}, tool)
    creds = reviewcfg.ci_credentials(tool, auth)
    return {"render.ci_secrets": " or ".join(c["secret"] for _, c in creds),
            "render.ci_secret_check": " || ".join(f"secrets.{c['secret']} != ''" for _, c in creds),
            "render.ci_secret_env": "\n".join(f"{CI_EVAL_ENV[k]}: {reviewcfg.ci_input_expr(tool, auth, k)}" for k, c in creds)}


MANIFEST = ".agents/manifest.yaml"
CONFIG = ".agents/framework.yaml"
RUBRIC = ".agents/rubric.yaml"
RUBRIC_OVERRIDE = ".agents/custom/.agents/rubric.yaml"
PENDING = ".agents/state/pending-confirmation.json"
DRIFT = ".agents/state/drift-report.json"
MANIFEST_HEADER = ("# agentive manifest (.agents/manifest.yaml): written by /agentive:init, /agentive:update and /agentive:reprove\n"
                   "# (schema manifest). Do not edit by hand except pinned values; state a value to the agent instead.\n")


def run_from_framework_src():
    """E2E-56 [design judgment]: true when this engine runs from the framework repo's template source (<repo>/src/engine with
    tools/generate.py next to src/), where no generated index.json exists by design; false in any installed payload."""
    root = os.path.dirname(PAYLOAD)
    return (os.path.basename(HERE) == "engine" and os.path.basename(PAYLOAD) == "src"
            and os.path.isfile(os.path.join(root, "tools", "generate.py")))


def index_missing_next():
    """NEXT of an unreadable payload index: from the framework src/ run the generated engine; elsewhere the plugin is broken."""
    if run_from_framework_src():
        return ("this is the template source, which has no generated index.json; run the generated engine instead: "
                "python3 plugins/claude-code/agentive/payload/engine/apply.py <same arguments> "
                "(run python3 tools/generate.py first if plugins/ is missing)")
    return "reinstall the agentive plugin (claude plugin update agentive@<name>)"


class EngineError(Exception):
    def __init__(self, msg, nxt):
        super().__init__(msg)
        self.nxt = nxt


class Stop(Exception):
    def __init__(self, msg, nxt):
        super().__init__(msg)
        self.nxt = nxt


def git(repo, *args):
    try:
        r = subprocess.run(["git", "-C", repo] + list(args), capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def is_git(repo):
    return git(repo, "rev-parse", "--is-inside-work-tree") == "true"


def now():
    return common.utc_now()


def read_text(path):
    with open(path, encoding="utf-8", newline="") as f:
        return f.read()


def write_text(path, text, executable=False):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    if os.path.islink(path):
        os.unlink(path)
    elif os.path.isdir(path):
        shutil.rmtree(path)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    mode = os.stat(path).st_mode
    os.chmod(path, (mode | 0o755) if executable else (mode & ~0o111))


def path_state(path):
    """(kind, sha) of what is at path: kind file|symlink|dir|None."""
    if os.path.islink(path):
        return "symlink", common.sha256_text("symlink:" + os.readlink(path))
    if os.path.isfile(path):
        return "file", common.sha256_file(path)
    if os.path.isdir(path):
        return "dir", common.sha256_text("dir:" + common.tree_sha256(path))
    return None, None


def content_sha(u):
    if u["kind"] == "symlink":
        return common.sha256_text("symlink:" + u["target"])
    return common.sha256_text(u["content"])


def split_frontmatter(text):
    if text.startswith("---\n") or text.startswith("---\r\n"):
        m = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n?", text, re.S)
        if m:
            try:
                fm = ayaml.loads(m.group(1)) or {}
            except Exception:  # noqa: BLE001
                fm = {}
            return (fm if isinstance(fm, dict) else {}), text[m.end():]
    return {}, text


def codex_tool_limit(tools):
    """The tool limit of a Claude Code role as an instruction line for its Codex custom agent (V2-11). A Codex role file has
    no tool list and no sandbox key (openai/codex official GitHub, codex-rs/core/src/agent/role.rs at 23934d31, 2026-10-29:
    a role overrides developer_instructions, model and reasoning settings and can only turn features off; the sandbox
    is the parent's), so the instructions are the only place to carry it [design judgment]."""
    names = {t.strip() for t in (tools.split(",") if isinstance(tools, str) else (tools or [])) if str(t).strip()}
    if not names or {"Edit", "Write", "MultiEdit", "NotebookEdit"} & names:
        return ""
    line = ("Read-only role: never edit, create, move or delete a repository file yourself, never stage, commit or push, and never "
            "install anything (a test command you run may write its usual caches and logs); reply with your answer, the caller writes it. ")
    if "Bash" not in names:
        line += "Run shell commands only to read (cat, sed -n, head, rg, ls, git show, git diff, git log). "
    return line + "\n\n"


def toml_string(s, multiline=False):
    if multiline:
        if "'''" not in s:
            return "'''\n" + s + "\n'''"
        return '"""\n' + s.replace("\\", "\\\\").replace('"""', '\\"\\"\\"') + '\n"""'
    return json.dumps(s, ensure_ascii=False)


COOLDOWNS = [("Node.js, npm", "npm: min-release-age=<days> in .npmrc"),
             ("Node.js, pnpm", "pnpm: minimumReleaseAge: <minutes> in pnpm-workspace.yaml"),
             ("Python (uv)", 'uv: exclude-newer = "7 days" under [tool.uv] in pyproject.toml')]


def dependency_report(labels):
    """DEPHUMAN (E2E-20, user 2026-10-31): the AGENTIVE_REPORT dependencies line of init and update. The gate sends every
    dependency or lockfile change to a human, but an install runs a new version's install scripts before any merge gate; a
    package-manager cooldown keeps a version published minutes ago out of installs. agentive writes none of these settings
    (a package file change the human decides). [design judgment: the wording; the settings named follow the ecosystem labels,
    all three when none matches]"""
    tips = [t for k, t in COOLDOWNS if any(k in lb for lb in labels or [])] or [t for _, t in COOLDOWNS]
    return ("dependencies: every PR that changes a dependency list or a lockfile goes to a human (tier 2, never auto-merged); "
            "the merge gate cannot stop install scripts of a version published minutes ago, a package-manager cooldown can: "
            + "; ".join(tips) + ". agentive writes none of these; tell your agent if you want one (it changes a package file, a human confirms)")


CONFIG_HEADER = ("# agentive settings you own. A key that is not here uses the agentive default.\n"
                 "# Show what is in force: .agents/bin/agentive-lint settings --effective\n")
DECISIONS = ".agents/decisions.yaml"


def add_auto_merge_key(text, value):
    """The text of .agents/framework.yaml with `review.auto_merge: <value>` added, edited as text so the user's comments and
    layout stay. None when the file cannot be edited safely (flow-style review mapping, or a result that does not parse)."""
    if not text.strip():
        out = CONFIG_HEADER + f"review:\n  auto_merge: {value}\n"
    else:
        lines = text.splitlines(keepends=True)
        out = None
        for i, ln in enumerate(lines):
            bare = ln.rstrip("\r\n")
            if re.match(r"^review:\s*(#.*)?$", bare):
                indent = "  "
                for nxt in lines[i + 1:]:
                    m = re.match(r"^(\s+)\S", nxt)
                    if m:
                        indent = m.group(1)
                        break
                    if nxt.strip() and not nxt.startswith((" ", "\t", "#")):
                        break
                lines.insert(i + 1, f"{indent}auto_merge: {value}\n")
                out = "".join(lines)
                break
            if re.match(r"^review:\s*\S", bare):
                return None
        if out is None:
            out = text + ("" if text.endswith("\n") else "\n") + f"review:\n  auto_merge: {value}\n"
    try:
        doc = ayaml.loads(out) or {}
    except ayaml.YAMLError:
        return None
    return out if ((doc.get("review") or {}).get("auto_merge") == value) else None


class Engine:
    def __init__(self, a):
        self.a = a
        self.cmd = a.command
        self.tool = a.tool
        self.ci_tool, self.ci_via = "claude", "the tool that ran init"   # set by apply() from the config and the manifest
        self.ci_auth = reviewcfg.CI_AUTH_DEFAULT                            # CIAUTH; set by apply() with ci_tool
        self.repo = os.path.realpath(a.repo)
        self.lines = []
        self.changed = set()
        self.index = self._load_index()
        self.version = self.index.get("version", "0.0.0")
        m = re.match(r"^(\d+\.\d+\.\d+)", self.version)
        self.version = m.group(1) if m else "0.0.0"
        self.today = a.today or datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
        self.headless = bool(a.headless) or nohuman.no_human()
        self.accept = set(a.accept or [])
        self.values = self._load_values()

    # ---------------------------------------------------------------- io
    def out(self, line):
        self.lines.append(line)

    def p(self, rel):
        return os.path.join(self.repo, rel)

    def _load_index(self):
        path = os.path.join(PAYLOAD, "index.json")
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError) as e:
            raise EngineError(f"payload index {path} unreadable ({e})", index_missing_next())

    def _load_values(self):
        if not self.a.values:
            return {}
        try:
            with open(self.a.values, encoding="utf-8") as f:
                v = json.load(f)
        except (OSError, ValueError) as e:
            raise EngineError(f"--values {self.a.values} unreadable ({e})", "write a JSON object (docs/impl/B.md §2)")
        if not isinstance(v, dict):
            raise EngineError("--values is not a JSON object", "write a JSON object (docs/impl/B.md §2)")
        return v

    def command_name(self, cmd=None):
        return COMMANDS.get((cmd or self.cmd, self.tool), f"/agentive:{cmd or self.cmd}")

    def template(self, rel):
        with open(os.path.join(PAYLOAD, rel), encoding="utf-8", newline="") as f:
            return f.read()

    # ---------------------------------------------------------------- entry
    def run(self):
        if not os.path.isdir(self.repo):
            raise EngineError(f"--repo {self.a.repo} is not a directory", "pass the project directory")
        initialized = os.path.isfile(self.p(MANIFEST))
        if self.a.print_evidence:
            print("AGENTIVE_EVIDENCE " + json.dumps(evidence.scan(self.repo), ensure_ascii=False, sort_keys=True))
            print("AGENTIVE_CHANGED:")
            return 0
        if self.cmd == "status":
            if not initialized:
                raise Stop("no agentive manifest (.agents/manifest.yaml)", "ask the user to run " + self.command_name("init"))
            self.status()
            return self.finish()
        if self.cmd == "init":
            if self.a.managed_review_copy and self.a.no_managed_review_copy:
                raise EngineError("both --managed-review-copy and --no-managed-review-copy given; nothing changed", "pass one of them")
            if initialized:
                self.status()
                for ln in self.lines:
                    print(ln)
                self.lines = []
                raise Stop("already initialized (.agents/manifest.yaml exists); init changes nothing",
                           "ask the user to run " + self.command_name("update"))
        elif not initialized:
            raise Stop("no agentive manifest (.agents/manifest.yaml)", "ask the user to run " + self.command_name("init"))
        if self.cmd == "reprove":
            self.reprove()
        else:
            self.apply()
        return self.finish()

    def finish(self):
        for ln in self.lines:
            print(ln)
        print("AGENTIVE_CHANGED:")
        for c in sorted(self.changed):
            print(c)
        return 0

    # ---------------------------------------------------------------- manifest
    def load_yaml(self, rel, default=None):
        path = self.p(rel)
        if not os.path.isfile(path):
            return default
        try:
            return ayaml.load(path)
        except Exception as e:  # noqa: BLE001
            raise EngineError(f"{rel} does not parse ({e})", f"fix {rel} by hand (it is yaml)")

    def tool_versions(self, old):
        tools = {}
        if "tools" in self.values:   # the caller states the versions (tests, skills); nothing is probed
            for key in ("claude_code", "codex"):
                v = ((self.values.get("tools") or {}).get(key) or {}).get("version")
                if v:
                    tools[key] = {"version": v}
            if "claude_code" in tools:
                tools["claude_code"]["plugin"] = {"name": "agentive", "version": self.version}
            return tools
        for key, exe in (("claude_code", "claude"), ("codex", "codex")):
            v = ((self.values.get("tools") or {}).get(key) or {}).get("version")
            if not v:
                try:
                    r = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=10)
                    m = re.search(r"(\d+\.\d+\.\d+)", r.stdout + r.stderr) if r.returncode == 0 else None
                    v = m.group(1) if m else None
                except (OSError, subprocess.SubprocessError):
                    v = None
            if not v:
                v = ((old or {}).get(key) or {}).get("version")
            if v:
                tools[key] = {"version": v}
        if "claude_code" in tools:
            tools["claude_code"]["plugin"] = {"name": "agentive", "version": self.version}
        return tools

    def derive_manifest(self, ev, old):
        seed = ayaml.loads(self.template(self._entry_template(".agents/manifest.yaml") or "")) if self._entry_template(".agents/manifest.yaml") else {}
        old = old or {}
        oldverbs = old.get("verbs") or {}
        vals = self.values.get("verbs") or {}
        verbs = {}
        for v in VERB_ORDER:
            prev = oldverbs.get(v) if isinstance(oldverbs.get(v), dict) else None
            if prev and prev.get("pinned"):
                verbs[v] = prev
                continue
            if v in vals:
                spec = dict(vals[v])
                if spec.get("status") != "pending":
                    spec.setdefault("tier", evidence.TIERS[v])
                    spec.setdefault("evidence", {"rank": 1, "source": "--values (derived by the agent)", "files": []})
            elif v in ev["verbs"]:
                spec = dict(ev["verbs"][v])
            else:
                spec = {"status": "pending", "reason": "no_evidence"}
                if ev.get("stated"):
                    spec["todo"] = dict(ev["stated"])
            if spec.get("status") != "pending":
                if "proof" not in spec:
                    if prev and prev.get("cmd") == spec["cmd"] and isinstance(prev.get("proof"), dict):
                        spec["proof"] = prev["proof"]
                    else:
                        spec["proof"] = {"pass_run_id": "unproved", "at": now()}
                if v == "lint":
                    self.lint_scope_into(spec, prev, ev, derived=v not in vals)
                order = ["cmd", "tier", "evidence", "exts", "file_args", "pinned", "proof", "fail_count", "coverage_report"]
                spec = {k: spec[k] for k in order if k in spec}
            verbs[v] = spec
        paths = dict(ev["paths"])
        for k, val in (self.values.get("paths") or {}).items():
            paths[k] = val
        if old.get("paths", {}).get("public_api") and "public_api" not in paths:
            paths["public_api"] = old["paths"]["public_api"]
        m = {"schema_version": 1, "framework": {"plugin": "agentive", "version": self.version, "init_tool": self.init_tool(old)}, "verbs": verbs,
             "paths": {k: paths.get(k, []) for k in ("src", "tests", "generated", "vendor")} | ({"public_api": paths["public_api"]} if paths.get("public_api") else {}),
             "capabilities": dict(old.get("capabilities") or {}), "tools": self.tool_versions(old.get("tools")),
             "stop_conditions": old.get("stop_conditions") or seed.get("stop_conditions") or {"max_blocks": 8, "no_progress_rounds": 2},
             "context": old.get("context") or seed.get("context") or {"read_limit_bytes": 65536}}
        if "budget_usd" in (old.get("stop_conditions") or {}):
            m["stop_conditions"]["budget_usd"] = old["stop_conditions"]["budget_usd"]
        m["janitor"] = old.get("janitor") or seed.get("janitor") or {"kinds": []}
        if ev.get("security", {}).get("tools"):
            m["security_checks"] = [dict(t) for t in ev["security"]["tools"]]     # SECPATHS L1: tools the repo already runs
        return m

    def lint_scope_into(self, spec, prev, ev, derived):
        """verbs.lint.exts / file_args (V1-13 gap) [design judgment]. A value the agent gave through --values stays. For a slot derived
        from evidence, a value already in the manifest for the same command wins (a person may have corrected it by hand);
        otherwise the evidence derivation (evidence.lint_scope) fills what is missing."""
        same = bool(prev) and prev.get("cmd") == spec.get("cmd")
        fresh = evidence.lint_scope(spec["cmd"], ev.get("primary"))
        for k in ("exts", "file_args"):
            if derived and same and k in prev:
                spec[k] = prev[k]
            elif k not in spec and k in fresh:
                spec[k] = fresh[k]

    def init_tool(self, old):
        """framework.init_tool: the tool that ran init. init records --tool; update keeps the recorded value; a manifest
        without it (written before CITOOL, when CI always ran claude) counts as claude [design judgment]."""
        if old:
            t = ((old.get("framework") or {}).get("init_tool"))
            return t if t in CI_TOOLS else "claude"
        return self.tool if self.tool in CI_TOOLS else "claude"

    def ci_llm_step(self):
        try:
            return ci_llm_step_text(self.ci_tool, self.ci_auth)
        except OSError as e:
            raise EngineError(f"CI step source for {self.ci_tool} unreadable ({e})", "reinstall the agentive plugin (claude plugin update agentive@<name>)")

    def _entry_template(self, install):
        for e in self.index.get("entries", []):
            if e["install"] == install and "region" not in e:
                return e["template"]
        return None

    def probe_t1(self, manifest):
        copy = os.name == "nt" or (git(self.repo, "config", "--get", "core.symlinks") or "").lower() == "false"
        old = (manifest.get("capabilities") or {}).get("T1")
        ver = (manifest.get("tools").get("claude_code") or {}).get("version")
        if ver:
            if not (old and old.get("fallback_applied") == copy and (old.get("tool") or {}).get("version") == ver):
                manifest["capabilities"]["T1"] = {"result": "skipped", "measured": {"mode": "copy" if copy else "symlink",
                                                  "rule": "core.symlinks=false or Windows -> copy without probing"},
                                                  "fallback_applied": copy, "tool": {"name": "claude_code", "version": ver}, "at": now()}
        return "copy" if copy else "symlink"

    def write_manifest(self, m):
        order = ["schema_version", "framework", "verbs", "paths", "security_checks", "capabilities", "tools", "stop_conditions", "context",
                 "conflicts", "generated", "janitor"]
        doc = {k: m[k] for k in order if k in m and (m[k] or k in ("capabilities", "tools", "verbs", "paths"))}
        if "generated" in m:
            doc["generated"] = {k: m["generated"][k] for k in sorted(m["generated"])}
        text = MANIFEST_HEADER + ayaml.dumps(doc)
        path = self.p(MANIFEST)
        if not os.path.isfile(path) or read_text(path) != text:
            write_text(path, text)
            self.changed.add(MANIFEST)

    # ---------------------------------------------------------------- blocks
    def blocks(self, manifest, ev, rules, rulepacks):
        b = {}
        if ev["ecosystems"]:
            line = f"Stack (from evidence): {', '.join(ev['labels'])} ({', '.join(ev['files'])})."
            o = [line]
            tst = ", ".join(x["path"] for x in manifest["paths"]["tests"])
            if tst:    # evidence only; no source-directory map is written (NOMAP)
                o.append(f"Tests: {tst}.")
        else:
            o = ["Stack: pending (no evidence): no stack evidence yet. Scaffold the project with your own agent, then run /agentive:update (Codex: $agentive-update)."]
            if ev.get("stated"):
                o.append(f"TODO: {ev['stated']['stack']} named in {ev['stated']['source']}; no command until evidence exists.")
        b["render.orientation"] = "\n".join(o)
        vl = []
        for v in VERB_ORDER:
            s = manifest["verbs"].get(v)
            if not s:
                continue
            if s.get("status") == "pending":
                t = s.get("todo")
                vl.append(f"- `{v}`: pending (no evidence)" + (f" TODO: {t['stack']} ({t['source']})" if t else ""))
            elif v == "verify" and s["cmd"].strip() == prove.SELF:
                inner = [x for x in ("lint", "typecheck", "test") if manifest["verbs"].get(x, {}).get("cmd")]
                vl.append(f"- `verify`: `.agents/bin/verb verify` runs {', '.join(inner)} (the inner verbs)")
            else:
                vl.append(f"- `{v}`: `.agents/bin/verb {v}` runs `{s['cmd']}` (evidence: {s['evidence']['source']})")
        b["render.verbs"] = "\n".join(vl)
        b["render.verb_list"] = " ".join(v for v in VERB_ORDER if manifest["verbs"].get(v, {}).get("cmd"))
        pend = [v for v in VERB_ORDER if manifest["verbs"].get(v, {}).get("status") == "pending"]
        b["render.pending_verbs"] = " ".join(pend) or "none"
        b["render.path_rules"] = self.path_rules(rulepacks)
        b["render.doc_pointers"] = self.doc_pointers()
        b["render.gitignore_stack"] = "\n".join(GITIGNORE_STACK.get(e, "") for e in ev["ecosystems"] if GITIGNORE_STACK.get(e))
        b["render.stack"] = ev["primary"] or "none"
        b["render.ecosystems"] = ", ".join(ev["ecosystems"]) or "none"
        db = git(self.repo, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD")
        b["render.default_branch"] = db.split("/", 1)[1] if db and "/" in db else "main"
        b["render.framework_version"] = self.version
        b["render.ci_tool"] = self.ci_tool
        b["render.ci_llm_step"] = self.ci_llm_step()
        b["render.ci_cli_name"] = CI_TOOLS[self.ci_tool]["name"]
        b["render.ci_cli_package"] = CI_TOOLS[self.ci_tool]["package"]
        b.update(ci_secret_blocks(self.ci_tool, self.ci_auth))   # CIAUTH: the secret lines of reviewer-eval.yml
        b["render.ci_setup_steps"] = cisetup.render(self.repo, ev["ecosystems"])      # CISETUP: toolchain steps of the outer-verbs job
        # claude-code-action's default GitHub App authentication needs the OIDC token (action FAQ); the codex path does not [CISPLIT]
        b["render.ci_id_token"] = "id-token: write" if self.ci_tool == "claude" else ""
        b["render.rules_managed"], b["render.rules_content"] = secscan.block_text(ev["security"])      # SECPATHS: CODEOWNERS snapshot, stack presets
        b["render.code_review_copy"] = None   # filled after code_review.md is written
        b["render.read_limit_bytes"] = str((manifest.get("context") or {}).get("read_limit_bytes", 65536))
        gv = [x["path"].rstrip("/") for k in ("generated", "vendor") for x in manifest["paths"][k]]
        deny_bash, ask_bash = gd.claude_permissions(rules)
        b["render.permissions_deny"] = (DENY_READ + [f"Read(./{x}/**)" for x in gv] + DENY_EDIT_CREDENTIALS
                                        + [f"Edit(./{x}/**)" for x in gv] + deny_bash)
        b["render.permissions_ask"] = ask_bash
        b["render.permissions_allow"] = []
        mb = int((manifest.get("stop_conditions") or {}).get("max_blocks", 8))
        b["render.settings_env"] = {"CLAUDE_CODE_STOP_HOOK_BLOCK_CAP": str(mb)} if mb > 8 else {}
        files = set()
        for v in manifest["verbs"].values():
            for f in (v.get("evidence") or {}).get("files") or [] if isinstance(v, dict) else []:
                if not f.startswith(".agents/"):
                    files.add(os.path.basename(f))
        if pend or not files:
            files |= set(evidence.EVIDENCE_FILES)
        b["render.filechanged_matcher"] = "|".join(sorted(files))
        b["render.codex_rules"] = gd.codex_rules(rules)
        b["render.codex_rules_check"] = gd.codex_check_lines(rules)
        return b

    DOC_CANDIDATES = ["README.md", "ARCHITECTURE.md", "CONTRIBUTING.md", "docs/architecture.md", "docs/adr/"]

    def doc_pointers(self):
        """One line that points at documents that EXIST in this repo (NOMAP); never a generated structure map."""
        found = []
        for rel in self.DOC_CANDIDATES:
            if rel.endswith("/"):
                if os.path.isdir(self.p(rel)) or any(e["install"].startswith(rel) for e in self.index.get("entries", [])):
                    found.append(rel)
            else:
                d, n = os.path.split(rel)
                try:
                    if n in os.listdir(self.p(d) if d else self.repo):
                        found.append(rel)
                except OSError:
                    pass
        return ("Docs (pointers to files that exist): " + ", ".join(found) + ". Read the one that matches your task before guessing.") if found else ""

    def path_rules(self, rulepacks):
        """AGENTS.md#path-rules block and the pointer-mode .claude/rules copies from one renderer (RULESNEUTRAL):
        agentive.rulesync, the same code `.agents/bin/agentive-lint rules --write|--check` runs."""
        agents = read_text(self.p("AGENTS.md")) if os.path.isfile(self.p("AGENTS.md")) else ""
        self.rules_plan = rulesync.plan(rulepacks, agents)
        return self.rules_plan["block"]

    # ---------------------------------------------------------------- units
    def render_entry(self, e, ctx):
        text = self.template(e["template"])
        try:
            text = render.render(e["install"] if "region" not in e else e["install"] + ".md", text, ctx)
        except render.RenderError as err:
            raise EngineError(f"template {e['template']} (unit {e['unit']}): {err}", "report it to the framework repo (owner of the unit); placeholder keys: docs/impl/B.md §1")
        return text

    def pack_text(self, e, ctx):
        """A framework rule pack as it will be on disk (template + .agents/custom/ override), the text agentive-lint reads."""
        text = self.render_entry(e, ctx)
        over = self.override_for(e["install"])
        if over:
            try:
                text = render.apply_override(e["install"], text, read_text(self.p(over)))
            except render.InvalidOverride:
                pass      # reported when the unit itself is built
        return text

    def override_for(self, install):
        rel = f".agents/custom/{install}"
        return rel if os.path.isfile(self.p(rel)) else None

    def build_units(self, ctx, active):
        units, errors = [], []
        for e in active:
            if e.get("f") == "f-manifest" or e["install"] == MANIFEST:
                continue
            if ctx["blocks"].get("render.code_review_copy") is None and "%%agentive:render.code_review_copy%%" in self.template(e["template"]):
                self.deferred.append(e)   # rendered after code_review.md is written (docs/impl/B.md §1)
                continue
            cats = [c for c in (e.get("confirm_scope") or []) if c in CATEGORIES]   # first listed = the category reported
            text = self.render_entry(e, ctx)
            over = self.override_for(e["install"])
            overrides = []
            if "region" in e:
                if over and e["region"] == FIRST_REGION.get(os.path.basename(e["install"]), e["region"]):
                    try:
                        otext = read_text(self.p(over))
                        text = render.apply_override(e["install"] + ".md", text, otext)
                        overrides = [over]
                    except render.InvalidOverride as err:
                        errors.append({"id": f"{e['install']}#{e['region']}", "path": e["install"], "section": e["region"],
                                       "code": "invalid_override", "message": f"{over}: {err}"})
                        continue
                units.append({"key": f"{e['install']}#{e['region']}", "path": e["install"], "region": e["region"], "kind": "region",
                              "content": text, "cats": cats, "overrides": overrides, "entry": e})
                continue
            # guard.yaml merges its override itself; the rubric override is applied at run time by
            # agentive.review.config.effective_rubric (U6 layers), never into the preset file: merged here it put an
            # `extends` key into .agents/rubric.yaml, which then failed validation (E2E-13 [design judgment: bug fix])
            if over and e["install"] not in (".agents/guard.yaml", RUBRIC):
                try:
                    text = render.apply_override(e["install"], text, read_text(self.p(over)))
                    overrides = [over]
                except render.InvalidOverride as err:
                    errors.append({"id": e["install"], "path": e["install"], "code": "invalid_override", "message": f"{over}: {err}"})
                    continue
            units.append({"key": e["install"], "path": e["install"], "kind": "file", "content": text, "cats": cats,
                          "executable": bool(e.get("executable")), "overrides": overrides, "entry": e})
        return units, errors

    def guard_rules(self, ctx, active, errors):
        e = next((x for x in active if x["install"] == ".agents/guard.yaml"), None)
        if e is None:
            return [], None
        text = self.render_entry(e, ctx)
        base = ayaml.loads(text)
        rules = base.get("rules") or []
        over = self.override_for(".agents/guard.yaml")
        if over:
            try:
                odoc = ayaml.load(self.p(over)) or {}
                orules = odoc.get("rules") or []
                problems = gd.validate_override(rules, orules)
                if problems:
                    raise render.InvalidOverride("; ".join(problems))
                merged = gd.merge_rules(rules, orules)
                text = render._header(text) + ayaml.dumps({"version": 1, "rules": merged})
                rules = merged
            except (render.InvalidOverride, Exception) as err:  # noqa: BLE001
                errors.append({"id": ".agents/guard.yaml", "path": ".agents/guard.yaml", "code": "invalid_override", "message": f"{over}: {err}"})
                over = "invalid"
        for r in rules:
            r.setdefault("expressible", True)
            r.setdefault("tools", ["claude_code", "codex"])
        return rules, (text, over)

    def find_user_rules(self, entries, old_gen):
        """Rule files the project wrote itself: `.agents/rules/<name>.md` on disk that no payload entry installs and no
        manifest record names (spec.repo_layer.files[f-rules]: every `.agents/rules/<name>.md` is rendered into AGENTS.md
        ## Path rules, and copied to `.claude/rules/<name>.md` in pointer mode only; V1-05, RULESNEUTRAL). The file stays the
        user's: it is read, never written."""
        d = self.p(".agents/rules")
        if not os.path.isdir(d):
            return []
        known = {e["install"] for e in entries} | set(old_gen)
        out = []
        for n in sorted(os.listdir(d)):
            rel = f".agents/rules/{n}"
            if n.endswith(".md") and rel not in known and os.path.isfile(self.p(rel)) and not os.path.islink(self.p(rel)):
                out.append(rel)
        return out

    def mirror_units(self, mode, active, unitmap):
        """.claude/skills|rules|agents mirrors (T1) and .codex/agents/<id>.toml, from the files as they are on disk."""
        out = []
        skills = sorted({e["install"].split("/")[2] for e in active if e["install"].startswith(".agents/skills/") and e["install"].count("/") >= 3})
        # RULESNEUTRAL: a rule merged into AGENTS.md gets no .claude/rules copy (no double read); pointer mode copies every rule
        rules = sorted(f".agents/rules/{n}.md" for n in (getattr(self, "rules_plan", None) or {}).get("mirrors", []))
        agents = sorted(e["install"] for e in active if re.match(r"^\.agents/agents/[^/]+\.md$", e["install"]))

        def disk(rel):
            try:
                return read_text(self.p(rel))
            except OSError:
                return unitmap.get(rel, {}).get("content")
        for name in skills:
            if mode == "symlink":
                out.append({"key": f".claude/skills/{name}", "path": f".claude/skills/{name}", "kind": "symlink",
                            "target": f"../../.agents/skills/{name}", "cats": [], "overrides": []})
            else:
                for e in active:
                    if e["install"].startswith(f".agents/skills/{name}/"):
                        rel = ".claude/" + e["install"][len(".agents/"):]
                        c = disk(e["install"])
                        if c is not None:
                            out.append({"key": rel, "path": rel, "kind": "file", "content": c, "cats": [], "overrides": [],
                                        "executable": bool(e.get("executable"))})
        for src in rules + agents:
            rel = ".claude/" + src[len(".agents/"):]
            if mode == "symlink":
                out.append({"key": rel, "path": rel, "kind": "symlink", "target": "../../" + src, "cats": [], "overrides": []})
            else:
                c = disk(src)
                if c is not None:
                    out.append({"key": rel, "path": rel, "kind": "file", "content": c, "cats": [], "overrides": []})
        for src in agents:
            c = disk(src)
            if c is None:
                continue
            fm, body = split_frontmatter(c)
            rid = os.path.basename(src)[:-3]
            toml = ["# agentive: rendered from " + src + " by /agentive:init and /agentive:update (spec.repo_layer.files[f-reviewers]); edit the source",
                    f"name = {toml_string(str(fm.get('name') or rid))}",
                    f"description = {toml_string(str(fm.get('description') or rid))}",
                    f"developer_instructions = {toml_string((codex_tool_limit(fm.get('tools')) + body.strip()), multiline=True)}"]
            rel = f".codex/agents/{rid}.toml"
            out.append({"key": rel, "path": rel, "kind": "file", "content": "\n".join(toml) + "\n", "cats": ["codex_dir"], "overrides": []})
        return out

    # ---------------------------------------------------------------- apply helpers
    def rec_for(self, u, sha):
        r = {"kind": "file", "sha256": sha, "framework_version": self.version}
        if u.get("overrides"):
            r["overrides"] = sorted(u["overrides"])
        return r

    def diff_items(self, cur, new, label):
        a, b = (cur or "").splitlines(), (new or "").splitlines()
        d = list(difflib.unified_diff(a, b, "current", "template", lineterm="", n=1))
        added = sum(1 for x in d if x.startswith("+") and not x.startswith("+++"))
        removed = sum(1 for x in d if x.startswith("-") and not x.startswith("---"))
        kind = "changed" if added and removed else ("added" if added else "removed")
        summary = f"{label}: {removed} line(s) differ from the template, {added} line(s) of the template missing" if (added or removed) else f"{label}: differs (whitespace or line endings)"
        return [{"kind": kind if (added or removed) else "changed", "summary": summary, "detail": "\n".join(d)[:4000]}]

    def apply_file(self, u, old_gen):
        path, key = self.p(u["path"]), u["key"]
        rec = old_gen.get(key)
        self.produced.add(key)
        parent = os.path.dirname(path)
        while parent.startswith(self.repo + os.sep):
            if os.path.islink(parent):
                self.out(f"AGENTIVE_WARN {key}: parent {os.path.relpath(parent, self.repo)} is a symlink; not written through it")
                if rec:
                    self.gen[key] = rec
                return
            parent = os.path.dirname(parent)
        kind, cur = path_state(path)
        new_sha = content_sha(u)
        action = None
        if kind is not None and cur == new_sha:
            self.gen[key] = self.rec_for(u, new_sha)
            return
        if kind is None:
            if rec is None:
                action = "create"
            elif self.a.force or key in self.accept:
                action = "force"
            else:
                self.drift.append({"id": key, "path": u["path"], "reason": "customized", "differences": [{"kind": "removed", "summary": "the generated file was deleted"}],
                                   "template_sha256": new_sha, "current_sha256": common.sha256_text(""), "override_path": f".agents/custom/{u['path']}"})
                self.gen[key] = rec
                return
        elif rec and cur == rec.get("sha256"):
            action = "overwrite"
        elif rec:
            action = "force" if (self.a.force or key in self.accept) else None
            if action is None:
                self._drift_file(u, path, kind, cur, new_sha, "customized")
                self.gen[key] = rec
                return
        else:
            if key in self.accept:
                action = "overwrite"
            else:
                self._drift_file(u, path, kind, cur, new_sha, "no_baseline")
                return
        try:
            if u["kind"] == "symlink":
                os.makedirs(os.path.dirname(path), exist_ok=True)
                if os.path.islink(path) or os.path.isfile(path):
                    os.unlink(path)
                elif os.path.isdir(path):
                    shutil.rmtree(path)
                os.symlink(u["target"], path)
            else:
                write_text(path, u["content"], u.get("executable", False))
        except OSError as e:
            if not u["path"].startswith(".codex/"):
                raise
            # Codex workspace-write keeps an existing .codex/ read-only (learn.chatgpt.com/docs/agent-approvals-security)
            self.pending.append({"path": u["path"], "category": "codex_dir", "reason": "write_failed", "rendered_sha256": new_sha,
                                 "next": "rerun with write access to .codex/ (a sandbox that allows it), or copy the file from the framework template by hand"})
            self.out(f"AGENTIVE_WARN {u['path']} could not be written ({e.strerror or e}); recorded in {PENDING}")
            if rec:
                self.gen[key] = rec
            return
        self.changed.add(u["path"])
        self.gen[key] = self.rec_for(u, new_sha)
        ref = {"id": key, "path": u["path"]}
        if action == "create":
            self.created.append(ref)
        elif action == "overwrite":
            self.overwritten.append(ref)
        else:
            if rec and key not in self.accept:
                self.forced.append(dict(ref, reason="customized", previous_sha256=cur if kind else common.sha256_text("")))
            else:
                self.overwritten.append(ref)

    def _drift_file(self, u, path, kind, cur, new_sha, reason):
        cur_text = ""
        if kind == "file":
            try:
                cur_text = read_text(path)
            except (OSError, UnicodeDecodeError):
                cur_text = ""
        elif kind == "symlink":
            cur_text = "symlink -> " + os.readlink(path)
        elif kind == "dir":
            cur_text = "(a directory)"
        new_text = ("symlink -> " + u["target"]) if u["kind"] == "symlink" else u["content"]
        item = {"id": u["key"], "path": u["path"], "reason": reason, "differences": self.diff_items(cur_text, new_text, u["path"]),
                "template_sha256": new_sha, "current_sha256": cur, "override_path": f".agents/custom/{u['path']}"}
        self.drift.append(item)
        if reason == "no_baseline":
            prop = ".agents/state/conflicts/" + u["path"].replace("/", "__") + ".diff"
            d = difflib.unified_diff(cur_text.splitlines(keepends=True), new_text.splitlines(keepends=True), u["path"], u["path"] + " (agentive)")
            os.makedirs(os.path.dirname(self.p(prop)), exist_ok=True)
            with open(self.p(prop), "w", encoding="utf-8", newline="\n") as f:
                f.write("".join(d))
            self.conflicts.append({"path": u["path"], "strategy": "keep_existing_propose_diff", "proposal": prop})

    # ---------------------------------------------------------------- JSON settings: key-level merge (JSONMERGE)
    SMOKE_INPUT = {"session_id": "agentive-smoke", "transcript_path": "", "permission_mode": "default", "source": "startup",
                   "tool_name": "Bash", "tool_input": {"command": "echo agentive-smoke", "file_path": "agentive-smoke.txt"},
                   "tool_response": {}, "stop_hook_active": False, "reason": "other", "file_path": "agentive-smoke.txt", "event": "change"}

    def smoke_check(self, tpl, manifest):
        """Run every agentive hook handler of `tpl` once with a sample input in a scratch copy of the hook layer
        (all verbs pending, so nothing of the project runs). Returns [] or one problem text per failing script."""
        scripts = []
        for ev, items in sorted(jsonmerge.owned_handlers(tpl).items()):
            for it in items:
                text = jsonmerge.describe_handler(it["handler"])
                m = re.search(r"\.agents/hooks/([A-Za-z0-9_.-]+\.py)((?:\s+--tool\s+\w+)?)", text)
                if m and (ev, m.group(1), m.group(2).split()) not in scripts:
                    scripts.append((ev, m.group(1), m.group(2).split()))
        if not scripts:
            return []
        problems = []
        tmp = tempfile.mkdtemp(prefix="agentive-smoke-")
        try:
            for sub in ("hooks", "lib", "bin"):
                if os.path.isdir(self.p(".agents/" + sub)):
                    shutil.copytree(self.p(".agents/" + sub), os.path.join(tmp, ".agents", sub), symlinks=True)
            for f in ("guard.yaml", "rubric.yaml"):
                if os.path.isfile(self.p(".agents/" + f)):
                    shutil.copy2(self.p(".agents/" + f), os.path.join(tmp, ".agents", f))
            probe = json.loads(json.dumps(manifest))
            for v, spec in (probe.get("verbs") or {}).items():
                probe["verbs"][v] = {"status": "pending", "reason": "no_evidence"}
            os.makedirs(os.path.join(tmp, ".agents"), exist_ok=True)
            write_text(os.path.join(tmp, MANIFEST), MANIFEST_HEADER + ayaml.dumps({k: probe[k] for k in ("schema_version", "framework", "verbs", "paths") if k in probe}))
            subprocess.run(["git", "-C", tmp, "init", "-q"], capture_output=True, timeout=20)
            env = {k: v for k, v in os.environ.items() if k not in ("AGENTIVE_HEADLESS", "CI", "GITHUB_ACTIONS")}
            env["CLAUDE_PROJECT_DIR"] = tmp
            for ev, name, extra in scripts:
                script = os.path.join(tmp, ".agents", "hooks", name)
                if not os.path.isfile(script):
                    problems.append(f".agents/hooks/{name} is missing")
                    continue
                inp = dict(self.SMOKE_INPUT, hook_event_name=ev, cwd=tmp)
                try:
                    r = subprocess.run([sys.executable, script] + extra, input=json.dumps(inp), capture_output=True, text=True,
                                       timeout=30, cwd=tmp, env=env)
                except (OSError, subprocess.SubprocessError) as e:
                    problems.append(f".agents/hooks/{name} ({ev}): {e}")
                    continue
                if r.returncode != 0:
                    problems.append(f".agents/hooks/{name} ({ev}) exited {r.returncode}: " + (r.stderr or r.stdout).strip().replace("\n", " ")[:200])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        return problems

    def set_hooks(self, tool, status, reason="", nxt=""):
        self.hooks_state[tool] = {"status": status, "reason": reason, "next": nxt}

    def apply_json(self, u, old_gen, manifest):
        """Install or merge a JSON settings file by key (decisions.accepted[JSONMERGE]); the file is written last."""
        path_rel, key = u["path"], u["key"]
        path = self.p(path_rel)
        tool = "codex" if path_rel.startswith(".codex/") else "claude"
        rec = old_gen.get(key)
        self.produced.add(key)
        owned = jsonmerge.OWNED[path_rel]
        try:
            tpl = json.loads(u["content"])
        except ValueError as e:
            raise EngineError(f"template {path_rel} (unit {u['entry']['unit']}) is not JSON after rendering ({e})", "report it to the framework repo")
        tpl_sha = jsonmerge.owned_sha(tpl)
        parent = os.path.dirname(path)
        while parent.startswith(self.repo + os.sep):
            if os.path.islink(parent):
                self.out(f"AGENTIVE_WARN {key}: parent {os.path.relpath(parent, self.repo)} is a symlink; not written through it")
                if rec:
                    self.gen[key] = rec
                self.set_hooks(tool, "off", "symlinked_parent", f"replace the symlink {os.path.relpath(parent, self.repo)} by a directory, then run {self.command_name('update')}")
                return
            parent = os.path.dirname(parent)

        def record(doc):
            r = {"kind": "file", "sha256": jsonmerge.owned_sha(doc), "framework_version": self.version, "owned": list(owned)}
            if u.get("overrides"):
                r["overrides"] = sorted(u["overrides"])
            return r
        kind, cur = path_state(path)
        doc, action, mode, drift_reason = None, None, "replace", None
        cur_o = common.sha256_text("")
        if kind is None:
            if rec is None or self.a.force or key in self.accept:
                action = "create" if rec is None else "force"
                doc = {}
            else:
                self.drift.append({"id": key, "path": path_rel, "reason": "customized", "differences": [{"kind": "removed", "summary": "the generated file was deleted"}],
                                   "template_sha256": tpl_sha, "current_sha256": common.sha256_text(""), "override_path": f".agents/custom/{path_rel}"})
                self.gen[key] = rec
                self.set_hooks(tool, "off", "file_deleted", f"run {self.command_name('update')} --accept {key} to write it again")
                return
        elif kind != "file":
            self._drift_file(u, path, kind, cur, tpl_sha, "no_baseline")
            self.set_hooks(tool, "off", "not_a_file", f"move {path_rel} away, then run {self.command_name('update')}")
            return
        else:
            try:
                doc = jsonmerge.parse(read_text(path))
            except (jsonmerge.Unparsable, UnicodeDecodeError) as e:
                self.errors.append({"id": key, "path": path_rel, "code": "unparsable_json",
                                    "message": f"{e}. {path_rel} was not written (JSON with comments or a syntax error cannot be merged safely)"})
                if rec:
                    self.gen[key] = rec
                self.set_hooks(tool, "off", "unparsable_json", f"fix the JSON syntax of {path_rel} (remove comments and trailing commas), then run {self.command_name('update')}")
                return
            cur_o = jsonmerge.owned_sha(doc)
            if cur_o == tpl_sha:
                action = "sync"
            elif rec and (cur_o == rec.get("sha256") or ("owned" not in rec and cur == rec.get("sha256"))):
                action = "overwrite"      # untouched since agentive wrote it (a 0.1.0 record holds the whole-file hash)
            elif rec:
                if self.a.force or key in self.accept:
                    action = "force"
                else:
                    mode, drift_reason = "keep", "customized"
            elif not jsonmerge.owned_handlers(doc) or key in self.accept:
                action = "create" if not jsonmerge.owned_handlers(doc) else "overwrite"
            else:
                mode, drift_reason = "keep", "no_baseline"
        if drift_reason:
            item = {"id": key, "path": path_rel, "reason": drift_reason,
                    "differences": self.diff_items(jsonmerge.handlers_text(doc), jsonmerge.handlers_text(tpl), key + " (agentive hook handlers)"),
                    "template_sha256": tpl_sha, "current_sha256": cur_o, "override_path": f".agents/custom/{path_rel}"}
            self.drift.append(item)
            if drift_reason == "no_baseline":
                prop = ".agents/state/conflicts/" + path_rel.replace("/", "__") + ".diff"
                d = difflib.unified_diff(jsonmerge.handlers_text(doc).splitlines(keepends=True), jsonmerge.handlers_text(tpl).splitlines(keepends=True),
                                         path_rel, path_rel + " (agentive)")
                os.makedirs(os.path.dirname(self.p(prop)), exist_ok=True)
                with open(self.p(prop), "w", encoding="utf-8", newline="\n") as f:
                    f.write("".join(d))
                self.conflicts.append({"path": path_rel, "strategy": "keep_existing_propose_diff", "proposal": prop})
        merged, confs = jsonmerge.merge(path_rel, doc, tpl, mode)
        for c in confs:
            self.conflicts.append({"path": path_rel, "strategy": "keep_existing_value", "key": c["key"], "existing": c["existing"], "proposed": c["proposed"]})
        if mode == "keep" and rec:
            self.gen[key] = rec
        if merged == doc and kind == "file":
            if mode != "keep":
                self.gen[key] = record(merged)
            self.set_hooks(tool, "installed" if mode != "keep" else "installed_modified", "" if mode != "keep" else "agentive hook entries were edited by hand",
                           "" if mode != "keep" else f"show .agents/state/drift-report.json; accept with {self.command_name('update')} --accept {key}")
            return
        if mode != "keep":
            problems = self.smoke_check(tpl, manifest)
            if problems:
                self.errors.append({"id": key, "path": path_rel, "code": "hook_smoke_failed", "message": "; ".join(problems) + f". {path_rel} was not written"})
                if rec:
                    self.gen[key] = rec
                self.set_hooks(tool, "off", "hook_smoke_failed", f"fix the failing hook script ({problems[0].split(' ')[0]}), then run {self.command_name('update')}")
                return
        try:
            write_text(path, jsonmerge.dumps(merged))
        except OSError as e:
            if tool != "codex":
                raise
            self.pending.append({"path": path_rel, "category": "codex_dir", "reason": "write_failed", "rendered_sha256": common.sha256_text(jsonmerge.dumps(merged)),
                                 "next": "rerun with write access to .codex/ (a sandbox that allows it), or copy the file from the framework template by hand"})
            self.out(f"AGENTIVE_WARN {path_rel} could not be written ({e.strerror or e}); recorded in {PENDING}")
            if rec:
                self.gen[key] = rec
            self.set_hooks(tool, "off", "write_failed", "rerun with write access to .codex/")
            return
        self.changed.add(path_rel)
        if mode != "keep":
            self.gen[key] = record(merged)
            ref = {"id": key, "path": path_rel}
            if action == "create":
                self.created.append(ref)
            elif action == "force" and rec:
                self.forced.append(dict(ref, reason="customized", previous_sha256=cur_o))
            else:
                self.overwritten.append(ref)
            self.set_hooks(tool, "installed")
        else:
            self.set_hooks(tool, "installed_modified", "agentive hook entries were edited by hand",
                           f"show .agents/state/drift-report.json; accept with {self.command_name('update')} --accept {key}")

    def apply_regions(self, path_rel, units, old_gen, stale_hits):
        path = self.p(path_rel)
        style = regions.style_for(path_rel)
        if os.path.basename(path_rel) == "CLAUDE.md" and os.path.islink(path) and os.path.realpath(path) == os.path.realpath(self.p("AGENTS.md")):
            for u in units:
                self.produced.add(u["key"])
                if old_gen.get(u["key"]):
                    self.gen[u["key"]] = old_gen[u["key"]]
            return
        existed = os.path.isfile(path)
        try:
            original = read_text(path) if existed else ""
        except UnicodeDecodeError:
            original = None
        text = original
        new_recs, refs = {}, []
        for u in units:
            self.produced.add(u["key"])
        if text is None:
            for u in units:
                self.errors.append({"id": u["key"], "path": path_rel, "section": u["region"], "code": "malformed_markers", "message": "file is not UTF-8 text; not written"})
                if old_gen.get(u["key"]):
                    self.gen[u["key"]] = old_gen[u["key"]]
            return
        try:
            for u in units:
                regions.find(text, style, u["region"])
        except regions.Malformed as err:
            for u in units:
                self.errors.append({"id": u["key"], "path": path_rel, "section": u["region"], "code": "malformed_markers", "message": str(err)})
                if old_gen.get(u["key"]):
                    self.gen[u["key"]] = old_gen[u["key"]]
            return
        for u in units:
            key, rec = u["key"], old_gen.get(u["key"])
            span = regions.find(text, style, u["region"])
            new_hash = regions.text_hash(u["content"])
            rr = {"kind": "region", "sha256": new_hash, "framework_version": self.version}   # no date, no sha (REGIONNOSHA)
            if u.get("overrides"):
                rr["overrides"] = sorted(u["overrides"])
            ref = {"id": key, "path": path_rel, "section": u["region"]}
            if span is None:
                if rec is None or self.a.force or key in self.accept:
                    block = regions.render_region(style, u["region"], u["content"])
                    text = regions.append(text, block)
                    new_recs[key] = rr
                    (self.created if rec is None else self.forced).append(ref if rec is None else dict(ref, reason="customized", previous_sha256=common.sha256_text("")))
                else:
                    self.drift.append(dict(ref, reason="customized", differences=[{"kind": "removed", "summary": f"region {u['region']} markers were deleted"}],
                                           template_sha256=new_hash, current_sha256=common.sha256_text(""), override_path=f".agents/custom/{path_rel}"))
                    new_recs[key] = rec
                continue
            cur_hash = regions.body_hash(text, span)
            nl = regions.line_ending(text, span)
            if cur_hash == new_hash:
                # same body: nothing to write (E2E-09). Only a region whose first line is not the constant notice (the
                # older dated line, or none) gets it once [design judgment]; the record is the same bytes as before.
                if not regions.notice_current(text, span, style):
                    text = regions.splice(text, span, regions.render_region(style, u["region"], u["content"], nl))
                new_recs[key] = rr
                continue
            if rec and cur_hash == rec.get("sha256"):
                text = regions.splice(text, span, regions.render_region(style, u["region"], u["content"], nl))
                new_recs[key] = rr
                self.overwritten.append(ref)
                for s in stale_hits.get(key, []):
                    s["resolution"] = "refreshed"
                continue
            reason = "customized" if rec else "no_baseline"
            if (reason == "customized" and self.a.force) or key in self.accept:
                text = regions.splice(text, span, regions.render_region(style, u["region"], u["content"], nl))
                new_recs[key] = rr
                if reason == "customized" and key not in self.accept:
                    self.forced.append(dict(ref, reason="customized", previous_sha256=cur_hash))
                else:
                    self.overwritten.append(ref)
                for s in stale_hits.get(key, []):
                    s["resolution"] = "refreshed"
                continue
            self.drift.append(dict(ref, reason=reason, differences=self.diff_items(regions.current_body(text, span), u["content"], key),
                                   template_sha256=new_hash, current_sha256=cur_hash, override_path=f".agents/custom/{path_rel}"))
            if rec:
                new_recs[key] = rec
        if text != original:
            write_text(path, text)
            self.changed.add(path_rel)
        for k, r in new_recs.items():
            if r:
                self.gen[k] = r

    _POINTER_RE = re.compile(r"(?m)^Docs \(pointers to files that exist\): (.*?)\. Read the one")
    _TESTS_RE = re.compile(r"(?m)^Tests: (.*)\.$")

    @classmethod
    def named_paths(cls, body):
        """(pointer paths, other path-like names) of a region body [design judgment, REGIONNOSHA]: the doc-pointer line entries;
        the `Tests:` line entries; and every other token that looks like a repository path (holds a `/` or ends with a
        file extension), stripped of backticks, brackets, a leading `@` and sentence punctuation. URLs, absolute paths
        and version numbers are not names."""
        m = cls._POINTER_RE.search(body)
        pointers = [x.strip() for x in m.group(1).split(",") if x.strip()] if m else []
        t = cls._TESTS_RE.search(body)
        names = [x.strip() for x in t.group(1).split(",") if x.strip()] if t else []
        for raw in re.split(r"[\s,]+", body):
            tok = raw.strip("`'\"()[]{}<>*;:,").lstrip("@").rstrip(".,;:")
            if not tok or "://" in raw or tok.startswith("/") or not re.fullmatch(r"[A-Za-z0-9_.][A-Za-z0-9_./-]*", tok):
                continue
            if re.fullmatch(r"[0-9.]+", tok) or ".." in tok.split("/"):
                continue
            if "/" in tok or re.search(r"[A-Za-z0-9_-]\.[A-Za-z][A-Za-z0-9]*$", tok):
                names.append(tok)
        seen, other = set(pointers), []
        for n in names:
            if n not in seen:
                seen.add(n)
                other.append(n)
        return pointers, other

    def path_history(self, named):
        """The commit that last deleted or renamed `named` (a file or a directory), without a stored sha:
        `git log -1 -M --full-diff --diff-filter=DR --name-status -- <path>` [S73][S74]. (commit, renamed_to | None), or
        None when the history has no such commit."""
        log = git(self.repo, "-c", "core.quotepath=off", "log", "-1", "-M", "--full-diff", "--diff-filter=DR", "--name-status",
                  "--format=@%H", "--", named)
        if not log:
            return None
        base = named.rstrip("/")
        commit = None
        for ln in log.splitlines():
            if ln.startswith("@"):
                commit = ln[1:]
                continue
            parts = ln.split("\t")
            if len(parts) < 2 or not parts[0]:
                continue
            old_p = parts[1]
            if old_p != base and not old_p.startswith(base + "/"):
                continue
            if parts[0].startswith("R") and len(parts) >= 3:
                new_p = parts[2]
                if old_p == base:
                    return commit, new_p
                rest = old_p[len(base):]
                return commit, (new_p[:-len(rest)] if new_p.endswith(rest) else new_p) + ("/" if named.endswith("/") else "")
            return commit, None
        return None

    def stale_check(self, old_gen, region_units):
        """Stale-line check, sha-free (REGIONNOSHA, E2E-09; replaces the <verified_commit>..HEAD range): a path named in a
        region body that is no longer in the working tree is stale when the history shows the commit that deleted or
        renamed it (path_history); a doc-pointer path (NOMAP) that is missing is stale even without such a commit
        (commit null). Runs before any region is rewritten. stale_check.since is the HEAD of the previous update run (from
        the previous, untracked drift report) and until the HEAD of this one; neither is stored in a tracked file."""
        hits = {}
        if not old_gen:
            return None, hits
        head = git(self.repo, "rev-parse", "--verify", "-q", "HEAD") if is_git(self.repo) else None
        history = bool(head)
        for key, rec in sorted(old_gen.items()):
            if rec.get("kind") != "region" or "#" not in key:
                continue
            path_rel, region = key.split("#", 1)
            try:
                text = read_text(self.p(path_rel))
                span = regions.find(text, regions.style_for(path_rel), region)
                body = regions.current_body(text, span) if span else ""
            except (OSError, regions.Malformed, UnicodeDecodeError):
                continue
            pointers, other = self.named_paths(body)
            for named in pointers + other:
                if os.path.lexists(self.p(named.rstrip("/") or named)):
                    continue
                found = self.path_history(named) if history else None
                if found is None and named not in pointers:
                    continue
                commit, renamed_to = found if found else (None, None)
                item = {"id": key, "path": path_rel, "named_path": named, "change": "renamed" if renamed_to else "deleted",
                        "commit": commit, "resolution": "reported"}
                if renamed_to:
                    item["renamed_to"] = renamed_to
                hits.setdefault(key, []).append(item)
        if not is_git(self.repo):
            return {"status": "skipped", "reason": "not_a_git_repository"}, hits
        if not head:
            return {"status": "skipped", "reason": "no_commits"}, hits
        prev = common.read_json(self.p(DRIFT), None) if os.path.isfile(self.p(DRIFT)) else None
        since = ((prev or {}).get("stale_check") or {}).get("until")
        return {"status": "done", "since": since if isinstance(since, str) and re.fullmatch(r"[a-f0-9]{7,64}", since) else None,
                "until": head}, hits

    def remove_unproduced(self, old_gen, inactive):
        for key, rec in sorted(old_gen.items()):
            if key in self.produced or key in self.gen:
                continue
            if "#" in key:
                self.out(f"AGENTIVE_WARN region {key} is no longer generated; its text stays and is no longer tracked")
                continue
            path = self.p(key)
            kind, cur = path_state(path)
            if kind is None:
                continue
            if cur == rec.get("sha256"):
                if kind == "dir":
                    shutil.rmtree(path)
                else:
                    os.unlink(path)
                self.changed.add(key)
                d = os.path.dirname(path)
                while d != self.repo and os.path.isdir(d) and not os.listdir(d):
                    os.rmdir(d)
                    d = os.path.dirname(d)
            else:
                self.out(f"AGENTIVE_WARN {key} is no longer generated and was modified; kept and no longer tracked")

    def migrate_decisions(self, cfg):
        """V1-18 (DEFAULTS): .agents/decisions.yaml of v0.1.0 is no longer written or read. Its values that differ from the
        framework defaults move to .agents/framework.yaml as overrides (review.auto_merge); the file is then removed by
        remove_unproduced when it is unmodified, else kept and reported. Returns the (possibly rewritten) config."""
        path = self.p(DECISIONS)
        pipe = ((cfg.get("review") or {}).get("pipeline")) if isinstance(cfg.get("review"), dict) else None
        has_tier1 = isinstance(pipe, dict) and isinstance(pipe.get("auto_merge"), dict) and "tier1" in pipe["auto_merge"]
        if not os.path.isfile(path):
            if not has_tier1:
                return cfg
            doc = {"U1": {"value": "C"}}         # no decisions.yaml: the old fallback was U1=C, so only tier1: on changes anything
        else:
            try:
                doc = ayaml.load(path)
            except Exception as e:  # noqa: BLE001
                self.out(f"AGENTIVE_WARN {DECISIONS} does not parse ({e}) and is no longer read; NEXT=delete it (git history keeps it); "
                         "review.auto_merge uses the framework default")
                return cfg
        res = reviewcfg.migrate_decisions(doc, pipe)
        value = res["auto_merge"]
        notes = list(res["notes"])
        if value is not None and isinstance(cfg.get("review"), dict) and "auto_merge" in cfg["review"]:
            notes.append(f"{CONFIG} already sets review.auto_merge={cfg['review']['auto_merge']}: the decisions.yaml value was not applied")
            value = None
        if value is not None:
            try:
                text = read_text(self.p(CONFIG)) if os.path.isfile(self.p(CONFIG)) else ""
            except OSError:
                text = ""
            new = add_auto_merge_key(text, value)
            if new is None:
                self.out(f"AGENTIVE_WARN {CONFIG} could not be edited safely; NEXT=add review.auto_merge: {value} to it by hand "
                         f"(the value {DECISIONS} had)")
            else:
                write_text(self.p(CONFIG), new)
                self.changed.add(CONFIG)
                cfg = self.load_yaml(CONFIG, {}) or {}
        src = DECISIONS if os.path.isfile(path) else "review.pipeline.auto_merge.tier1"
        self.out(f"AGENTIVE_MIGRATED {src} -> {CONFIG}: review.auto_merge="
                 f"{value if value is not None else 'unchanged (the framework default applies)'}; {src} is no longer read")
        for n in notes:
            self.out(f"AGENTIVE_REPORT migration: {n}")
        return cfg

    def rubric_preset(self):
        """The preset name in the installed .agents/rubric.yaml, or None."""
        try:
            doc = ayaml.loads(read_text(self.p(RUBRIC)))
        except Exception:  # noqa: BLE001 (missing or unparsable: no preset)
            return None
        return doc.get("preset") if isinstance(doc, dict) else None

    def migrate_rubric(self, old):
        """E2E-13 (U6 revised to agentive:rubric@2): when this update moved the preset of .agents/rubric.yaml, an override that
        extended the old preset is moved with it (its `extends` line only; the file is the user's), and both moves are
        reported. Without this an override on the old preset would silently stop matching. An override that names another
        framework preset on purpose (a pin) is kept. Returns the AGENTIVE_REPORT lines."""
        new = self.rubric_preset()
        if self.cmd != "update" or not old or not new or old == new:
            return []
        lines = [f"AGENTIVE_REPORT rubric: {RUBRIC} moved from {old} to {new}: {reviewcfg.RUBRIC_CHANGES.get(new, 'see the preset file')}"]
        pin = (f"to stay on {old}, set extends: \"{old}\" in {RUBRIC_OVERRIDE} (alone, or with your changes): the override is then "
               f"merged onto {old}; a rubric change is a review-config change, so a human confirms that commit")
        path = self.p(RUBRIC_OVERRIDE)
        if not os.path.isfile(path):
            return lines + ["AGENTIVE_REPORT rubric: " + pin]
        text = read_text(path)
        try:
            doc = ayaml.loads(text)
        except Exception:  # noqa: BLE001
            doc = None
        ext = doc.get("extends") if isinstance(doc, dict) else None
        if ext != old:
            return lines + [f"AGENTIVE_REPORT rubric: {RUBRIC_OVERRIDE} extends {ext}; kept (it is merged onto that preset)"]
        rx = re.compile(r'(?m)^(extends:[ \t]*)(["\']?)' + re.escape(old) + r'\2([ \t]*(#.*)?)$')
        new_text, n = rx.subn(lambda m: m.group(1) + m.group(2) + new + m.group(2) + m.group(3), text)
        try:
            ok = n == 1 and (ayaml.loads(new_text) or {}).get("extends") == new
        except Exception:  # noqa: BLE001
            ok = False
        if not ok:
            self.out(f"AGENTIVE_WARN {RUBRIC_OVERRIDE} extends {old} and could not be edited safely; it stays on {old} (pinned); "
                     f"NEXT=set extends: \"{new}\" in it by hand to follow the new preset")
            return lines
        write_text(path, new_text)
        self.changed.add(RUBRIC_OVERRIDE)
        return lines + [f"AGENTIVE_REPORT rubric: {RUBRIC_OVERRIDE} extends moved from {old} to {new} together with the preset, so your "
                        f"changes keep applying (its item ids are the same); " + pin]

    # ---------------------------------------------------------------- apply
    def apply(self):
        old = self.load_yaml(MANIFEST, None)
        old_rubric = self.rubric_preset()
        old_gen = dict((old or {}).get("generated") or {})
        for k, r in list(old_gen.items()):   # REGIONNOSHA (E2E-09): region records hold no verified_at / verified_commit any more
            if isinstance(r, dict) and r.get("kind") == "region" and ("verified_at" in r or "verified_commit" in r):
                old_gen[k] = {x: y for x, y in r.items() if x not in ("verified_at", "verified_commit")}
        cfg = self.load_yaml(CONFIG, {}) or {}
        cfg = self.migrate_decisions(cfg)
        flag = True if self.a.managed_review_copy else (False if self.a.no_managed_review_copy else None)
        if flag is not None and self.cmd != "init":
            self.out("AGENTIVE_WARN --managed-review-copy flags are read by init only; update follows .agents/framework.yaml")
            flag = None
        eff = json.loads(json.dumps(cfg))
        if flag is not None:
            eff.setdefault("review", {})["managed_code_review_copy"] = flag
        ev = evidence.scan(self.repo)
        ev["security"] = secscan.scan(self.repo)
        cr_old = read_text(self.p("code_review.md")) if os.path.isfile(self.p("code_review.md")) else ""
        self.sec_old = secscan.snapshot(cr_old)
        manifest = self.derive_manifest(ev, old)
        rv = eff.get("review") if isinstance(eff.get("review"), dict) else {}
        pipe = rv.get("pipeline") if isinstance(rv.get("pipeline"), dict) else {}
        if "ci_tool" in pipe and pipe["ci_tool"] not in CI_TOOLS:
            self.out(f"AGENTIVE_WARN {CONFIG} review.pipeline.ci_tool={pipe['ci_tool']!r} is not claude or codex; using the tool that ran init")
        self.ci_tool = reviewcfg.ci_tool(pipe, manifest)
        self.ci_via = "review.pipeline.ci_tool" if pipe.get("ci_tool") in CI_TOOLS else "the tool that ran init"
        if "ci_auth" in pipe and pipe["ci_auth"] not in reviewcfg.CI_AUTH:
            self.out(f"AGENTIVE_WARN {CONFIG} review.pipeline.ci_auth={pipe['ci_auth']!r} is not " + " | ".join(reviewcfg.CI_AUTH)
                     + f"; using {reviewcfg.CI_AUTH_DEFAULT}")
        elif "ci_auth" in pipe and self.ci_tool != "claude":
            self.out(f"AGENTIVE_WARN {CONFIG} review.pipeline.ci_auth applies to the claude CI path only; {self.ci_tool} uses its API key secret")
        self.ci_auth = reviewcfg.ci_auth(pipe, self.ci_tool)
        mode = self.probe_t1(manifest)
        self.pending, self.drift, self.overwritten, self.created, self.forced = [], [], [], [], []
        self.not_effective = []
        self.errors, self.conflicts, self.gen, self.produced, self.deferred = [], [], {}, set(), []
        self.hooks_state, self.skipped = {}, []
        ctx = {"blocks": {}, "manifest": manifest, "config": eff, "ecosystems": ev["ecosystems"]}
        entries = self.index.get("entries", [])
        active = [e for e in entries if render.eval_when(e.get("when"), ctx)]
        inactive = [e for e in entries if e not in active]
        # pass 1: rule packs and guard rules (inputs of the render.* blocks); blocks without them first
        ctx["blocks"] = self.blocks(manifest, ev, [], [])
        rulepacks = [(os.path.basename(e["install"])[:-3], self.pack_text(e, ctx)) for e in active if re.match(r"^\.agents/rules/[^/]+\.md$", e["install"])]
        self.user_rules = self.find_user_rules(entries, old_gen)
        rulepacks += [(os.path.basename(rel)[:-3], read_text(self.p(rel))) for rel in self.user_rules]
        rulepacks.sort(key=lambda x: x[0])
        rules, guard_out = self.guard_rules(ctx, active, self.errors)
        ctx["blocks"] = self.blocks(manifest, ev, rules, rulepacks)
        units, errs = self.build_units(ctx, active)
        self.errors += errs
        if guard_out and guard_out[1] == "invalid":
            units = [u for u in units if u["path"] != ".agents/guard.yaml"]
            for k in (".agents/guard.yaml",):
                if old_gen.get(k):
                    self.gen[k] = old_gen[k]
        elif guard_out:
            for u in units:
                if u["path"] == ".agents/guard.yaml":
                    u["content"] = guard_out[0]
                    if guard_out[1]:
                        u["overrides"] = [guard_out[1]]
        for e in self.errors:
            self.produced.add(e["id"])
            if old_gen.get(e["id"]):
                self.gen[e["id"]] = old_gen[e["id"]]
        if self.a.no_hooks:   # opt-out (INITWRITE): no hook scripts, no settings/hooks.json wiring; earlier records stay as they are
            for u in [x for x in units if "hooks" in x["cats"]]:
                units.remove(u)
                self.skipped.append(u["path"])
                if old_gen.get(u["key"]):
                    self.gen[u["key"]] = old_gen[u["key"]]
            for tool in ("claude", "codex"):
                self.set_hooks(tool, "off", "no_hooks_flag", f"run {self.command_name('update')} without --no-hooks to install the hooks")
        json_units = [u for u in units if u["kind"] == "file" and u["path"] in jsonmerge.OWNED]
        units = [u for u in units if u not in json_units]
        region_units = [u for u in units if u["kind"] == "region"]
        stale_status, stale_hits = (self.stale_check(old_gen, region_units) if self.cmd == "update" else (None, {}))
        # removals first (mode switches, units gone, when turned false), then whole files, regions, mirrors
        mirrors_preview = {u["key"] for u in self.mirror_units(mode, active, {})} | {e["install"] for e in self.deferred}
        self.produced |= {u["key"] for u in units + json_units} | mirrors_preview
        self.remove_unproduced(old_gen, inactive)
        self.produced = {e["id"] for e in self.errors}
        unitmap = {u["path"]: u for u in units if u["kind"] == "file"}
        for u in units:
            if u["kind"] == "file":
                self.apply_file(u, old_gen)
        byfile = {}
        for u in region_units:
            byfile.setdefault(u["path"], []).append(u)
        for path_rel in sorted(byfile):
            self.apply_regions(path_rel, byfile[path_rel], old_gen, stale_hits)
        if self.deferred:
            cr = self.p("code_review.md")
            ctx["blocks"]["render.code_review_copy"] = read_text(cr) if os.path.isfile(cr) else ""
            more, errs = self.build_units(ctx, self.deferred)
            self.errors += errs
            for u in more:
                self.apply_file(u, old_gen)
        for u in self.mirror_units(mode, active, unitmap):
            self.apply_file(u, old_gen)
        for u in json_units:    # last: settings.json and hooks.json, after the hook scripts exist and passed the smoke check
            self.apply_json(u, old_gen, manifest)
        self.rubric_lines = self.migrate_rubric(old_rubric)
        # project config (init only, from the flag; user-owned file, not in manifest.generated)
        if self.cmd == "init" and flag is not None and (cfg.get("review") or {}).get("managed_code_review_copy") != flag:
            newcfg = json.loads(json.dumps(cfg))
            newcfg.setdefault("review", {})["managed_code_review_copy"] = flag
            text = ayaml.dumps(newcfg)
            write_text(self.p(CONFIG), text)
            self.changed.add(CONFIG)
        manifest["generated"] = self.gen
        if self.conflicts:
            manifest["conflicts"] = sorted(self.conflicts, key=lambda c: c["path"])
        self.write_manifest(manifest)
        if self.a.prove:
            targets = [v for v in VERB_ORDER if manifest["verbs"].get(v, {}).get("cmd")]
            prove.prove_verbs(self.repo, manifest["verbs"], targets, self.values.get("seeds"), self.out)
            self.write_manifest(manifest)
        self.write_skills_lock()
        self.write_pending()
        if self.cmd == "update":
            self.write_drift(old, stale_status, stale_hits)
        self.report(manifest, ev, rules)

    # ---------------------------------------------------------------- state files
    def write_skills_lock(self):
        lock_path = self.p(".agents/skills.lock")
        old = (common.read_json(lock_path, {}) or {}).get("skills", {}) if os.path.isfile(lock_path) else {}
        skills = {}
        d = self.p(".agents/skills")
        if os.path.isdir(d):
            for name in sorted(os.listdir(d)):
                sd = os.path.join(d, name)
                if os.path.isdir(sd) and os.path.isfile(os.path.join(sd, "SKILL.md")):
                    sha = common.tree_sha256(sd)
                    prev = old.get(name) or {}
                    e = {"path": f".agents/skills/{name}", "sha256": sha,
                         "verified_at": prev.get("verified_at") if prev.get("sha256") == sha and prev.get("verified_at") else now()}
                    for k in ("source_url", "pinned_ref"):
                        if prev.get(k) and prev.get("sha256") == sha:
                            e[k] = prev[k]
                    skills[name] = e
        doc = {"skills": skills}
        cur = common.read_json(lock_path, None) if os.path.isfile(lock_path) else None
        if cur != doc:
            common.write_json(lock_path, doc)
            self.changed.add(".agents/skills.lock")

    def collect_pending(self):
        """Items that were WRITTEN but are not yet effective (INITWRITE). Recomputed by every init and update run."""
        def sha(rel):
            try:
                return common.sha256_file(self.p(rel))
            except OSError:
                return None
        have = {x["path"] for x in self.pending}
        if ".codex/hooks.json" in self.changed and ".codex/hooks.json" not in have:
            self.pending.append({"path": ".codex/hooks.json", "category": "codex_dir", "reason": "codex_hooks_untrusted", "rendered_sha256": sha(".codex/hooks.json"),
                                 "next": "a human trusts the new hook hash once in Codex /hooks; untrusted project hooks do not run, and the project .codex/ layer must be trusted"})
        if self.headless and ".claude/settings.json" in self.changed:
            self.pending.append({"path": ".claude/settings.json", "category": "claude_settings", "reason": "untrusted_folder", "rendered_sha256": sha(".claude/settings.json"),
                                 "next": "open this folder once in an interactive Claude Code session and accept the workspace trust dialog: under claude -p in an untrusted folder the hooks run "
                                         "but permissions.allow and additionalDirectories are not used (whether deny and ask rules apply before trust is unverified)"})

    def write_pending(self):
        path = self.p(PENDING)
        self.collect_pending()
        if not self.pending:
            if os.path.isfile(path):
                os.unlink(path)
            return
        files = []
        for x in sorted(self.pending, key=lambda x: x["path"]):
            files.append({k: v for k, v in x.items() if v is not None})
        old = common.read_json(path, None) if os.path.isfile(path) else None
        doc = {"created_at": old["created_at"] if old and old.get("files") == files and old.get("command") == self.command_name() else now(),
               "command": self.command_name() if self.cmd in ("init", "update") else self.command_name("update"),
               "framework_version": self.version, "files": files}
        if old != doc:
            common.write_json(path, doc)
        self.not_effective = files      # printed by report() right above AGENTIVE_NEXT (E2E-27)

    def write_drift(self, old, stale_status, stale_hits):
        doc = {"schema_version": 1, "generated_at": now(),
               "framework": {"from_version": ((old or {}).get("framework") or {}).get("version", self.version), "to_version": self.version},
               "overwritten": sorted(self.overwritten, key=lambda x: x["id"]), "items": sorted(self.drift, key=lambda x: x["id"])}
        if self.a.force:
            doc["force"] = True
            doc["forced"] = sorted(self.forced, key=lambda x: x["id"])
        elif self.forced:
            doc["overwritten"] = sorted(doc["overwritten"] + [{"id": x["id"], "path": x["path"], **({"section": x["section"]} if "section" in x else {})} for x in self.forced], key=lambda x: x["id"])
        if self.created:
            doc["created"] = sorted(self.created, key=lambda x: x["id"])
        if stale_status:
            doc["stale_check"] = stale_status
        hits = [s for v in stale_hits.values() for s in v]
        if hits:
            doc["stale"] = hits
        if self.errors:
            doc["errors"] = sorted(self.errors, key=lambda x: x["id"])
        path = self.p(DRIFT)
        old_doc = common.read_json(path, None) if os.path.isfile(path) else None
        if old_doc and {k: v for k, v in old_doc.items() if k != "generated_at"} == {k: v for k, v in doc.items() if k != "generated_at"}:
            return
        common.write_json(path, doc)

    # ---------------------------------------------------------------- report
    def report(self, manifest, ev, rules):
        for it in self.drift:
            self.out(f"AGENTIVE_DRIFT id={it['id']} reason={it['reason']} override_path={it.get('override_path', '-')}")
        for e in self.errors:
            self.out(f"AGENTIVE_WARN {e['code']} {e['id']}: {e['message']}")
        for c in sorted((x for x in self.conflicts if x["strategy"] == "keep_existing_value"), key=lambda x: (x["path"], x["key"])):
            self.out(f"AGENTIVE_CONFLICT path={c['path']} key={c['key']} kept={json.dumps(c['existing'], ensure_ascii=False)} "
                     f"agentive={json.dumps(c['proposed'], ensure_ascii=False)}; NEXT=decide which value stays and edit {c['path']} (agentive keeps your value on every run)")
        if any(it["path"] == ".codex/config.toml" and it["reason"] == "no_baseline" for it in self.drift):
            self.out("AGENTIVE_WARN .codex/config.toml existed before agentive and TOML is not merged by key: it was kept, so agentive's sandbox_mode and "
                     "approval_policy are NOT applied; NEXT: merge the proposed diff from .agents/state/conflicts/.codex__config.toml.diff by hand")
        for sk in self.skipped:
            self.out(f"AGENTIVE_SKIPPED path={sk} reason=--no-hooks")
        self.hooks_report()
        pend = [v for v in VERB_ORDER if manifest["verbs"].get(v, {}).get("status") == "pending"]
        for v in pend:
            self.out(f"AGENTIVE_PENDING verb={v} reason=no_evidence")
        am = reviewcfg.load_auto_merge(lambda rel: read_text(self.p(rel)) if os.path.isfile(self.p(rel)) else None)
        conf_l = reviewcfg.load_project_config(lambda rel: read_text(self.p(rel)) if os.path.isfile(self.p(rel)) else None)
        rs = reviewcfg.required_state_of(manifest, conf_l)
        # E2E-19 (EFFECTIVECHECKS): the required checks in force are computed by the gate (nothing is written); a pending verb
        # of the default list is only "not required"; the floor and a pending verb the user named keep auto-merge unavailable
        self.out("AGENTIVE_REPORT " + reviewcfg.required_report(rs))
        blocked = rs["pending_required"]
        if blocked and am.value != "off":     # PENDINGNOTICE (V2-23): the gate sends every PR to a human while a required check has no command
            self.out("AGENTIVE_REPORT auto-merge unavailable: " + reviewcfg.unavailable_text(blocked))
            self.out("AGENTIVE_REPORT auto-merge unavailable: " + reviewcfg.REQUIRE_CHECKS_EXIT +
                     "; ask the user for the missing command once, never pick a tool for them")
        elif rs["floor"] and am.value != "off":
            self.out("AGENTIVE_REPORT auto-merge unavailable: " + reviewcfg.floor_text(rs["required"]) +
                     " Ask the user for the missing command once, never pick a tool for them")
        for v in VERB_ORDER:
            s = manifest["verbs"].get(v, {})
            if s.get("cmd") and (s.get("proof") or {}).get("pass_run_id") == "unproved":
                self.out(f"AGENTIVE_UNPROVED verb={v} cmd={s['cmd']}")
        for r in gd.not_expressible(rules):
            self.out(f"AGENTIVE_WARN not-expressible ask item {r['id']} is denied (no permission rule can express it): {r['reason']}")
        self.out(f"AGENTIVE_REPORT auto-merge: {reviewcfg.AUTO_MERGE_SHORT[am.value]} (review.auto_merge={am.value}, {am.source}): "
                 f"{reviewcfg.AUTO_MERGE_MEANING[am.value]}. {reviewcfg.AUTO_MERGE_RULES}. To change it tell your agent or set review.auto_merge in "
                 f"{CONFIG} ({' | '.join(reviewcfg.AUTO_MERGE_VALUES)}); a human confirms that commit. Merging needs the GitHub repository setting "
                 "'Allow auto-merge' (Settings > General > Pull Requests) and branch protection that requires agentive-review-gate. "
                 "Show the settings in force: .agents/bin/agentive-lint settings --effective")
        for ln in getattr(self, "rubric_lines", []):
            self.out(ln)
        rep = [
            "codex: a human trusts the agentive hooks once in Codex /hooks (untrusted project hooks do not run) and trusts the project .codex/ layer",
            "managed-settings (org admin, optional; agentive writes nothing): allowManagedModsOnly, or strictKnownMarketplaces + disableSideloadFlags, keeps permissions.deny above user mods",
            "github: turn on 'Dismiss stale pull request approvals when new commits are pushed' and 'Require approval of the most recent reviewable push'",
            "github: keep 'Allow Copilot approvals to count toward merge requirements' off",
            "github: turn on secret scanning push protection (organization-owned private and internal repositories need GitHub Secret Protection on GitHub Team or Enterprise Cloud)",
            "github: on GitHub Free private repositories branch protection and CODEOWNERS are unavailable, so those gate parts are not enforced there; dependency review needs GitHub Team with Code Security and is information for the human reviewer only (a dependency change goes to a human either way)",
            dependency_report(ev["labels"]),
        ]
        for r in rep:
            self.out("AGENTIVE_REPORT " + r)
        ci = CI_TOOLS[self.ci_tool]
        creds = [c["secret"] for _, c in reviewcfg.ci_credentials(self.ci_tool, self.ci_auth)]
        add = (f"add one repository secret, {' or '.join(reversed(creds))}" if len(creds) > 1 else f"add the repository secret {creds[0]}")
        auth = f" (review.pipeline.ci_auth: {self.ci_auth})" if self.ci_tool == "claude" else ""
        self.out(f"AGENTIVE_REPORT github: the CI AI review (.github/workflows/agentive-review.yml, reviewer-eval.yml) runs on {ci['name']} "
                 f"({self.ci_via}); {add}{auth} (Settings > Secrets and variables > Actions); "
                 f"to run CI on the other tool set review.pipeline.ci_tool: claude|codex in {CONFIG}, then run {self.command_name('update')}")
        if self.ci_tool == "claude":       # CIAUTH (user 2026-10-30): which Claude secret to add, from code.claude.com/docs/en/github-actions
            self.out("AGENTIVE_REPORT github: Claude CI secret: CLAUDE_CODE_OAUTH_TOKEN authenticates with a Claude Pro, Max, Team or Enterprise "
                     "subscription (run `claude setup-token` locally and store the token it prints; runs use the subscription, not API billing); "
                     "ANTHROPIC_API_KEY is a Claude Console API key, billed to that Console account; for a secret shared across repositories "
                     "(an organization secret) use the API key, since an OAuth token is tied to the subscription of the person who ran "
                     "`claude setup-token`; OAuth token first: with both secrets set the CI passes only CLAUDE_CODE_OAUTH_TOKEN "
                     "(ANTHROPIC_API_KEY is passed only when the token secret is empty), so a subscription user who also stored an API key "
                     "is not billed through the API; to pin one credential set "
                     f"review.pipeline.ci_auth: api_key|oauth_token in {CONFIG}, then run {self.command_name('update')}")
        if self.ci_tool == "claude":       # E2E-11 (WFCHANGE, user 2026-10-30): the action's workflow validation skips these PRs
            self.out("AGENTIVE_REPORT github: install PR: the first install PR and every PR that changes .github/workflows/agentive-review.yml "
                     "run without the CI AI review: the Claude Code action runs only when that workflow file is identical to the default "
                     "branch's copy (its workflow validation) and skips otherwise; a human reviews and merges such a PR (the gate ends "
                     "human_required and says why); the action can also skip a PR whose workflow file is unchanged (its token server refuses the "
                     "workflow validation, the rule is not public): the gate then ends human_required the same way; merge the install PR "
                     "first, before other PRs, so later PRs get the AI review")
        self.out("AGENTIVE_REPORT github: a Dependabot or fork PR runs without Actions secrets, so the ai-review LLM stage cannot run and the gate sends it to a human "
                 "(human_required, fail-safe); a bot PR, or a human PR without an agentive-review block, skips the PR-body heading check and is never auto-merged "
                 "(.github/pull_request_template.md is installed for human PRs)")
        if ev["secret_scanner"]:
            self.out("AGENTIVE_REPORT secret scanner configuration found: its staged or diff scan is wired into the verify verb")
        customized = any(d.get("id") == "code_review.md#rules" for d in self.drift)
        for line in secscan.report_lines(ev["security"], self.sec_old[0], self.sec_old[1], self.cmd == "init", customized):
            self.out("AGENTIVE_REPORT " + line)
        nxt = []
        # E2E-27: the AGENTIVE_NOT_EFFECTIVE lines sit right above AGENTIVE_NEXT, and NEXT names them only when there are some
        not_effective = getattr(self, "not_effective", None) or []
        for f in not_effective:
            self.out(f"AGENTIVE_NOT_EFFECTIVE path={f['path']} reason={f['reason']} NEXT={f['next']}")
        if not_effective:
            nxt.append(f"tell the human about the AGENTIVE_NOT_EFFECTIVE line{'s' if len(not_effective) > 1 else ''} just above ("
                       + ", ".join(f"{f['path']}: {f['reason']}" for f in not_effective)
                       + "; written, not yet effective); the next interactive session start shows them too (.agents/state/pending-confirmation.json)")
        if self.drift:
            nxt.append("show the drift report (.agents/state/drift-report.json) and apply the items the human accepts with --accept <id>")
        if pend and not ev["ecosystems"]:
            nxt.append("scaffold the project with your own agent, then run " + self.command_name("update"))
        if any((manifest["verbs"].get(v, {}).get("proof") or {}).get("pass_run_id") == "unproved" for v in VERB_ORDER if manifest["verbs"].get(v, {}).get("cmd")):
            nxt.append("prove the slots (rerun with --prove, or /agentive:reprove)")
        if self.cmd == "init":     # E2E-14 (HUMANMERGE): the install commit is a confirm-scope change and the install PR is tier 2
            nxt.append("review the changed files and commit them through the normal commit procedure on a work branch (init and update "
                       "never commit; the layer changes hooks, settings and CI, so the commit needs the human's yes or, with no human present, a work branch "
                       "only), open the install PR with /pr; a human reviews and merges the install PR first, before "
                       "other PRs; agents never merge it (MERGE=human)")
        else:
            nxt.append("review the changed files and commit them through the normal commit procedure (init and update never commit)")
        self.out("AGENTIVE_NEXT " + "; then ".join(nxt))

    def hooks_report(self):
        """Always state the hooks and guard status, what now runs automatically, and how to revert (INITWRITE)."""
        tools = [("claude", ".claude/settings.json"), ("codex", ".codex/hooks.json")]
        guard_on = False
        for tool, rel in tools:
            st = self.hooks_state.get(tool)
            handlers = {}
            if os.path.isfile(self.p(rel)):
                try:
                    handlers = jsonmerge.owned_handlers(jsonmerge.parse(read_text(self.p(rel))))
                except (jsonmerge.Unparsable, UnicodeDecodeError, OSError):
                    handlers = {}
            if st is None:
                st = {"status": "installed", "reason": "", "next": ""} if handlers else \
                     {"status": "off", "reason": "not_written", "next": f"run {self.command_name('update')}"}
            if st["status"].startswith("installed") and not handlers:
                st = {"status": "off", "reason": "no_agentive_handlers_in_file", "next": f"run {self.command_name('update')}"}
            line = f"AGENTIVE_HOOKS tool={tool} file={rel} status={st['status']}"
            if st["status"] == "off":
                line += f" reason={st['reason']}; NEXT={st['next']}"
            elif st.get("reason"):
                line += f" reason={st['reason']}; NEXT={st['next']}"
            self.out(line)
            for ev, items in handlers.items():
                for it in items:
                    cmd = jsonmerge.describe_handler(it["handler"])
                    self.out(f"AGENTIVE_HOOK tool={tool} event={ev} matcher={it['matcher'] or '-'} runs={cmd}")
                    if "pre-tool-use-guard.py" in cmd:
                        guard_on = True
        self.out(f"AGENTIVE_GUARD status={'on' if guard_on else 'off'}"
                 + ("" if guard_on else f"; NEXT=fix the hooks status above, then run {self.command_name('update')}; until then the guard rules in .agents/guard.yaml are not enforced"))
        ch = sorted(self.changed)
        if not ch:
            return
        tracked = set((git(self.repo, "ls-files") or "").splitlines()) if is_git(self.repo) else None
        if tracked is None:
            self.out("AGENTIVE_REVERT not a git repository: delete the created files by hand (the AGENTIVE_CHANGED list)")
            return
        modified = [c for c in ch if c in tracked]
        created = [c for c in ch if c not in tracked]
        if modified:
            self.out("AGENTIVE_REVERT git checkout -- " + " ".join(modified))
        if created:
            untracked = set((git(self.repo, "ls-files", "--others", "--exclude-standard") or "").splitlines())
            targets, cset = [], set(created)
            for top in sorted({c.split("/")[0] for c in created}):
                under = {u for u in untracked if u == top or u.startswith(top + "/")}
                if "/" in next(c for c in created if c.split("/")[0] == top) and under and under <= cset:
                    targets.append(top + "/")
                else:
                    targets += [c for c in created if c.split("/")[0] == top]
            self.out("AGENTIVE_REVERT git clean -fd -- " + " ".join(targets))

    # ---------------------------------------------------------------- status
    def status(self):
        m = self.load_yaml(MANIFEST, {}) or {}
        gen = m.get("generated") or {}
        modified, missing, malformed, unchanged = [], [], [], 0
        for key, rec in sorted(gen.items()):
            if "#" in key:
                path_rel, region = key.split("#", 1)
                try:
                    text = read_text(self.p(path_rel))
                except (OSError, UnicodeDecodeError):
                    missing.append(key)
                    continue
                try:
                    span = regions.find(text, regions.style_for(path_rel), region)
                except regions.Malformed:
                    malformed.append(key)
                    continue
                if span is None:
                    missing.append(key)
                elif regions.body_hash(text, span) != rec.get("sha256"):
                    modified.append(key)
                else:
                    unchanged += 1
                continue
            kind, cur = path_state(self.p(key))
            if kind == "file" and key in jsonmerge.OWNED and "owned" in rec:    # JSON merge: only the agentive-owned part is compared (JSONMERGE)
                try:
                    cur = jsonmerge.owned_sha(jsonmerge.parse(read_text(self.p(key))))
                except (jsonmerge.Unparsable, UnicodeDecodeError, OSError):
                    cur = None
            if kind is None:
                missing.append(key)
            elif cur != rec.get("sha256"):
                modified.append(key)
            else:
                unchanged += 1
        for k in modified:
            self.out(f"AGENTIVE_STATUS modified {k}")
        for k in missing:
            self.out(f"AGENTIVE_STATUS missing {k}")
        for k in malformed:
            self.out(f"AGENTIVE_STATUS malformed {k}")
        custom = []
        cd = self.p(".agents/custom")
        if os.path.isdir(cd):
            for d, _dirs, files in os.walk(cd):
                custom += [os.path.relpath(os.path.join(d, f), self.repo).replace(os.sep, "/") for f in files]
        pc = common.read_json(self.p(PENDING), None) if os.path.isfile(self.p(PENDING)) else None
        not_eff = pc.get("files") or [] if isinstance(pc, dict) else []
        for f in not_eff:       # V2-21: files written but not yet effective are part of the state too
            self.out(f"AGENTIVE_STATUS not-effective {f.get('path', '?')} reason={f.get('reason', 'pending')}; NEXT={f.get('next') or 'see ' + PENDING}")
        self.out(f"AGENTIVE_STATUS summary modified={len(modified)} missing={len(missing)} malformed={len(malformed)} unchanged={unchanged} "
                 f"not_effective={len(not_eff)} custom_overrides={'yes (' + str(len(custom)) + ')' if custom else 'no'}")
        if self.tool == "claude":
            self.out("AGENTIVE_STATUS prompt audit: a human runs /doctor prompt-audit (Claude Code) after a model change")

    # ---------------------------------------------------------------- reprove
    def reprove(self):
        m = self.load_yaml(MANIFEST, {}) or {}
        dirty_path = self.p(".agents/state/dirty.json")
        dirty = (common.read_json(dirty_path, {}) or {}).get("slots", []) if os.path.isfile(dirty_path) else []
        slots = sorted({d.get("slot") for d in dirty if d.get("slot")})
        ev = evidence.scan(self.repo)
        fresh = self.derive_manifest(ev, m)
        targets = set()
        for s in slots:
            kind, _, name = s.partition(".")
            if kind == "verbs" and name in fresh["verbs"]:
                if not (m.get("verbs", {}).get(name) or {}).get("pinned"):
                    m.setdefault("verbs", {})[name] = fresh["verbs"][name]
                targets.add(name)
            elif kind == "paths" and name in fresh["paths"]:
                m.setdefault("paths", {})[name] = fresh["paths"][name]
        for v in self.values.get("verbs") or {}:
            if v in fresh["verbs"]:
                m["verbs"][v] = fresh["verbs"][v]
                targets.add(v)
        for v, s in (m.get("verbs") or {}).items():
            if isinstance(s, dict) and s.get("cmd") and ((s.get("proof") or {}).get("pass_run_id") == "unproved"):
                targets.add(v)
        if not slots and not targets:
            targets = {v for v, s in (m.get("verbs") or {}).items() if isinstance(s, dict) and s.get("cmd")}
        res = prove.prove_verbs(self.repo, m["verbs"], [v for v in VERB_ORDER if v in targets], self.values.get("seeds"), self.out)
        for v, ok in sorted(res.items()):
            self.out(f"AGENTIVE_PROOF verb={v} result={'pass' if ok else 'fail'}")
        m["framework"] = m.get("framework") or {"plugin": "agentive", "version": self.version}
        self.write_manifest(m)
        if slots:
            proved = {f"verbs.{v}" for v, ok in res.items() if ok} | {s for s in slots if s.startswith("paths.")}
            left = [d for d in dirty if d.get("slot") not in proved]
            common.write_json(dirty_path, {"slots": left})
        self.out("AGENTIVE_NEXT the AGENTS.md region and settings catch up at the next " + self.command_name("update")
                 + "; commit .agents/manifest.yaml through the normal commit procedure")


def parse(argv):
    ap = argparse.ArgumentParser(prog="apply.py", description="agentive apply engine (docs/impl/B.md §2)")
    ap.add_argument("command", choices=["init", "update", "status", "reprove"])
    ap.add_argument("--repo", required=True)
    ap.add_argument("--managed-review-copy", action="store_true")
    ap.add_argument("--no-managed-review-copy", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--no-hooks", action="store_true", help="skip hooks and the settings/hooks.json wiring (decisions.accepted[INITWRITE])")
    # accepted and ignored: older skills passed them after the U2 question, which init and update no longer ask (INITWRITE)
    ap.add_argument("--confirm-all", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--confirm", nargs="+", action="extend", default=[], help=argparse.SUPPRESS)
    ap.add_argument("--accept", nargs="+", action="extend", default=[])
    ap.add_argument("--values")
    ap.add_argument("--tool", choices=["claude", "codex"], default="claude")
    ap.add_argument("--prove", action="store_true")
    ap.add_argument("--today")
    ap.add_argument("--print-evidence", action="store_true")
    return ap.parse_args(argv)


def main(argv=None):
    try:
        a = parse(sys.argv[1:] if argv is None else argv)
    except SystemExit as e:
        if e.code not in (0, None):
            print("AGENTIVE_ERROR bad arguments; NEXT=python3 <payload>/engine/apply.py --help")
            return 2
        return 0
    try:
        return Engine(a).run()
    except Stop as s:
        print(f"AGENTIVE_STOP {s}; NEXT={s.nxt}")
        return 1
    except EngineError as e:
        print(f"AGENTIVE_ERROR {e}; NEXT={e.nxt}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
