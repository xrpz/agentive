#!/usr/bin/env python3
# agentive-unit: eng.apply
# design: spec.init_apply (entry_rule, update, managed_regions, no_preview, headless_init_update, report, reprove_on_change, probes.T1), spec.repo_layer.files[f-manifest], spec.repo_layer.files[f-skills], spec.repo_layer.files[f-reviewers], contracts[manifest], contracts[drift-report], contracts[pending-confirmation], contracts[skills-lock], docs/impl/B.md §2
"""agentive apply engine: init, update, status and reprove of the repo layer (python3 >= 3.9 stdlib, no network).

  python3 <payload>/engine/apply.py {init|update|status|reprove} --repo <dir>
      [--managed-review-copy | --no-managed-review-copy] [--force] [--headless]
      [--confirm-all | --confirm <path>...] [--values <json>] [--accept <id>...] [--tool claude|codex]
      [--prove] [--today YYYY-MM-DD] [--print-evidence]

Exit 0 = done (`AGENTIVE_CHANGED:` then one changed path per line), 1 = entry check stop (`AGENTIVE_STOP ...; NEXT=`),
2 = error (`AGENTIVE_ERROR ...; NEXT=`). Deterministic: the LLM parts (ambiguous slots, seeds for proofs, the
REVIEW.md question, the U2 confirmation) are done by the plugin skills, which pass the results as flags.
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

HERE = os.path.dirname(os.path.abspath(__file__))
PAYLOAD = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(PAYLOAD, "repo-layer", "dot-agents", "lib"))

if sys.version_info < (3, 9):
    print("AGENTIVE_ERROR python3 < 3.9; NEXT=install python3 >= 3.9")
    sys.exit(2)

from agentive import ayaml, common, guard as gd, nohuman  # noqa: E402
import evidence  # noqa: E402
import prove  # noqa: E402
import regions  # noqa: E402
import render  # noqa: E402

VERB_ORDER = evidence.VERB_ORDER
CATEGORIES = ["hooks", "permissions", "ci", "claude_settings", "codex_dir", "review_config"]
DENY_READ = ["Read(.env)", "Read(.env.*)", "Read(./secrets/**)", "Read(~/.ssh/**)", "Read(~/.aws/**)", "Read(~/.gnupg/**)",
             "Read(~/.netrc)", "Read(~/.config/gh/**)", "Read(~/.kube/**)"]
DENY_EDIT_CREDENTIALS = ["Edit(~/.ssh/**)"]
FIRST_REGION = {"AGENTS.md": "context", "CLAUDE.md": "imports", "code_review.md": "rules", ".gitignore": "state"}
COMMANDS = {("init", "claude"): "/agentive:init", ("update", "claude"): "/agentive:update", ("reprove", "claude"): "/agentive:reprove",
            ("init", "codex"): "$agentive-init", ("update", "codex"): "$agentive-update", ("status", "claude"): "/agentive:update --status",
            ("status", "codex"): "$agentive-update --status", ("reprove", "codex"): "$agentive-update"}
MANIFEST = ".agents/manifest.yaml"
CONFIG = ".agents/framework.yaml"
PENDING = ".agents/state/pending-confirmation.json"
DRIFT = ".agents/state/drift-report.json"
MANIFEST_HEADER = ("# agentive manifest (.agents/manifest.yaml): written by /agentive:init, /agentive:update and /agentive:reprove\n"
                   "# (schema manifest). Do not edit by hand except pinned values; state a value to the agent instead.\n")


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


def toml_string(s, multiline=False):
    if multiline:
        if "'''" not in s:
            return "'''\n" + s + "\n'''"
        return '"""\n' + s.replace("\\", "\\\\").replace('"""', '\\"\\"\\"') + '\n"""'
    return json.dumps(s, ensure_ascii=False)


class Engine:
    def __init__(self, a):
        self.a = a
        self.cmd = a.command
        self.tool = a.tool
        self.repo = os.path.realpath(a.repo)
        self.lines = []
        self.changed = set()
        self.index = self._load_index()
        self.version = self.index.get("version", "0.0.0")
        m = re.match(r"^(\d+\.\d+\.\d+)", self.version)
        self.version = m.group(1) if m else "0.0.0"
        self.today = a.today or datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
        self.headless = bool(a.headless) or nohuman.no_human()
        self.confirm = set(a.confirm or [])
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
            raise EngineError(f"payload index {path} unreadable ({e})", "reinstall the agentive plugin (claude plugin update agentive@<name>)")

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

    def confirmed(self, path):
        return (not self.headless) and (self.a.confirm_all or path in self.confirm)

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
            if initialized and not self._continuation():
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

    def _continuation(self):
        pc = common.read_json(self.p(PENDING), None) if os.path.isfile(self.p(PENDING)) else None
        return bool(pc and str(pc.get("command", "")).endswith("init") and (self.a.confirm_all or self.confirm) and not self.headless)

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
                order = ["cmd", "tier", "evidence", "pinned", "proof", "fail_count", "coverage_report"]
                spec = {k: spec[k] for k in order if k in spec}
            verbs[v] = spec
        paths = dict(ev["paths"])
        for k, val in (self.values.get("paths") or {}).items():
            paths[k] = val
        if old.get("paths", {}).get("public_api") and "public_api" not in paths:
            paths["public_api"] = old["paths"]["public_api"]
        m = {"schema_version": 1, "framework": {"plugin": "agentive", "version": self.version}, "verbs": verbs,
             "paths": {k: paths.get(k, []) for k in ("src", "tests", "generated", "vendor")} | ({"public_api": paths["public_api"]} if paths.get("public_api") else {}),
             "capabilities": dict(old.get("capabilities") or {}), "tools": self.tool_versions(old.get("tools")),
             "stop_conditions": old.get("stop_conditions") or seed.get("stop_conditions") or {"max_blocks": 8, "no_progress_rounds": 2},
             "context": old.get("context") or seed.get("context") or {"read_limit_bytes": 65536}}
        if "budget_usd" in (old.get("stop_conditions") or {}):
            m["stop_conditions"]["budget_usd"] = old["stop_conditions"]["budget_usd"]
        m["janitor"] = old.get("janitor") or seed.get("janitor") or {"kinds": []}
        return m

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
        order = ["schema_version", "framework", "verbs", "paths", "capabilities", "tools", "stop_conditions", "context",
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
            src = ", ".join(x["path"] for x in manifest["paths"]["src"])
            tst = ", ".join(x["path"] for x in manifest["paths"]["tests"])
            if src or tst:
                o.append(f"Source: {src or 'none found'} · tests: {tst or 'none found'}.")
        else:
            o = ["Stack: 근거 없음 · 대기 (no stack evidence yet). Scaffold the project with your own agent, then run /agentive:update (Codex: $agentive-update)."]
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
                vl.append(f"- `{v}`: pending (근거 없음 · 대기)" + (f" TODO: {t['stack']} ({t['source']})" if t else ""))
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
        b["render.boundary_line"] = ""
        b["render.service_facts"] = ""
        b["render.stack"] = ev["primary"] or "none"
        b["render.ecosystems"] = ", ".join(ev["ecosystems"]) or "none"
        db = git(self.repo, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD")
        b["render.default_branch"] = db.split("/", 1)[1] if db and "/" in db else "main"
        b["render.framework_version"] = self.version
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

    def path_rules(self, rulepacks):
        if not rulepacks:
            return "No path rules for this stack yet."
        agents_lines = 0
        if os.path.isfile(self.p("AGENTS.md")):
            agents_lines = len(read_text(self.p("AGENTS.md")).splitlines())
        merged, total_lines, total_bytes = [], 0, 0
        for name, text in rulepacks:
            fm, body = split_frontmatter(text)
            paths = fm.get("paths") or []
            paths = paths if isinstance(paths, list) else [paths]
            body = re.sub(r"(?m)^(#+)", r"\1##", body.strip())
            chunk = f"### {name} (paths: {', '.join(str(x) for x in paths) or 'all'})\n{body}"
            merged.append(chunk)
            total_lines += chunk.count("\n") + 2
            total_bytes += len(chunk.encode("utf-8"))
        if agents_lines + total_lines + 40 <= 200 and total_bytes + 8192 <= 32768:
            return "\n\n".join(merged)
        out = []
        for name, text in rulepacks:
            fm, _ = split_frontmatter(text)
            paths = fm.get("paths") or []
            paths = paths if isinstance(paths, list) else [paths]
            out.append(f"- {name}: read .agents/rules/{name}.md (paths: {', '.join(str(x) for x in paths) or 'all'})")
        return "\n".join(out)

    # ---------------------------------------------------------------- units
    def render_entry(self, e, ctx):
        text = self.template(e["template"])
        try:
            text = render.render(e["install"] if "region" not in e else e["install"] + ".md", text, ctx)
        except render.RenderError as err:
            raise EngineError(f"template {e['template']} (unit {e['unit']}): {err}", "report it to the framework repo (owner of the unit); placeholder keys: docs/impl/B.md §1")
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
            if over and e["install"] != ".agents/guard.yaml":
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

    def mirror_units(self, mode, active, unitmap):
        """.claude/skills|rules|agents mirrors (T1) and .codex/agents/<id>.toml, from the files as they are on disk."""
        out = []
        skills = sorted({e["install"].split("/")[2] for e in active if e["install"].startswith(".agents/skills/") and e["install"].count("/") >= 3})
        rules = sorted(e["install"] for e in active if re.match(r"^\.agents/rules/[^/]+\.md$", e["install"]))
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
                    f"developer_instructions = {toml_string(body.strip(), multiline=True)}"]
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

    def gate(self, u, new_sha, why):
        """True when u may be written now; otherwise records it as pending confirmation."""
        if not u["cats"] or self.confirmed(u["path"]):
            return True
        if u["path"] not in {x["path"] for x in self.pending}:
            self.pending.append({"path": u["path"], "category": u["cats"][0], "rendered_sha256": new_sha})
        return False

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
        if not self.gate(u, new_sha, action):
            if rec:
                self.gen[key] = rec
            return
        if u["kind"] == "symlink":
            os.makedirs(os.path.dirname(path), exist_ok=True)
            if os.path.islink(path) or os.path.isfile(path):
                os.unlink(path)
            elif os.path.isdir(path):
                shutil.rmtree(path)
            os.symlink(u["target"], path)
        else:
            write_text(path, u["content"], u.get("executable", False))
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
        head = git(self.repo, "rev-parse", "HEAD")
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
            rr = {"kind": "region", "sha256": new_hash, "framework_version": self.version, "verified_at": self.today, "verified_commit": head}
            if u.get("overrides"):
                rr["overrides"] = sorted(u["overrides"])
            ref = {"id": key, "path": path_rel, "section": u["region"]}
            if span is None:
                if rec is None or self.a.force or key in self.accept:
                    block = regions.render_region(style, u["region"], u["content"], self.today, head)
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
                if rec and rec.get("verified_commit") == head and rec.get("sha256") == new_hash:
                    new_recs[key] = rec
                    continue
                d, c = regions.current_provenance(text, span)
                if c == head and d and rec is None:
                    rr["verified_at"] = d
                    new_recs[key] = rr
                    continue
                text = regions.splice(text, span, regions.render_region(style, u["region"], u["content"], self.today, head, nl))
                new_recs[key] = rr
                continue
            if rec and cur_hash == rec.get("sha256"):
                text = regions.splice(text, span, regions.render_region(style, u["region"], u["content"], self.today, head, nl))
                new_recs[key] = rr
                self.overwritten.append(ref)
                for s in stale_hits.get(key, []):
                    s["resolution"] = "refreshed"
                continue
            reason = "customized" if rec else "no_baseline"
            if (reason == "customized" and self.a.force) or key in self.accept:
                text = regions.splice(text, span, regions.render_region(style, u["region"], u["content"], self.today, head, nl))
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
            cats = sorted({c for u in units for c in u["cats"]}, key=CATEGORIES.index)
            if cats and not self.confirmed(path_rel):
                self.gate({"path": path_rel, "cats": cats}, common.sha256_text(text), "regions")
                for u in units:
                    if old_gen.get(u["key"]):
                        self.gen[u["key"]] = old_gen[u["key"]]
                self.created = [x for x in self.created if x["path"] != path_rel]
                self.overwritten = [x for x in self.overwritten if x["path"] != path_rel]
                return
            write_text(path, text)
            self.changed.add(path_rel)
        for k, r in new_recs.items():
            if r:
                self.gen[k] = r

    def stale_check(self, old_gen, region_units):
        """git log -M --diff-filter=DR --name-status <verified_commit>..HEAD against the paths named in each region."""
        hits = {}
        if not old_gen:
            return None, hits
        if not is_git(self.repo):
            return {"status": "skipped", "reason": "not_a_git_repository"}, hits
        head = git(self.repo, "rev-parse", "HEAD")
        commits = {k: r.get("verified_commit") for k, r in old_gen.items() if r.get("kind") == "region"}
        if not commits or not any(commits.values()) or not head:
            return {"status": "skipped", "reason": "no_verified_commit"}, hits
        since = None
        for key, c in sorted(commits.items()):
            if not c:
                continue
            if git(self.repo, "cat-file", "-e", c + "^{commit}") is None:
                return {"status": "skipped", "reason": "commit_not_found", "since": c, "until": head}, hits
            log = git(self.repo, "log", "-M", "--diff-filter=DR", "--name-status", "--format=@%H", f"{c}..HEAD")
            since = since or c
            path_rel = key.split("#")[0]
            try:
                text = read_text(self.p(path_rel))
                span = regions.find(text, regions.style_for(path_rel), key.split("#")[1])
                body = regions.current_body(text, span) if span else ""
            except (OSError, regions.Malformed, UnicodeDecodeError):
                body = ""
            commit, seen = None, set()
            for ln in (log or "").splitlines():
                if ln.startswith("@"):
                    commit = ln[1:]
                    continue
                parts = ln.split("\t")
                if len(parts) < 2 or not parts[0]:
                    continue
                old_p, new_p = parts[1], (parts[2] if parts[0].startswith("R") and len(parts) >= 3 else None)
                cands = [old_p]
                d = os.path.dirname(old_p)
                while d:
                    cands.append(d)   # a directory named in the region is stale only when it is gone at HEAD
                    d = os.path.dirname(d)
                for cand in cands:
                    if (key, cand) in seen or not re.search(r"(?<![\w./-])" + re.escape(cand) + r"(?![\w-])", body):
                        continue
                    if cand != old_p and os.path.exists(self.p(cand)):
                        continue
                    seen.add((key, cand))
                    item = {"id": key, "path": path_rel, "named_path": cand, "change": "renamed" if new_p else "deleted",
                            "commit": commit or head, "resolution": "reported"}
                    if new_p:
                        item["renamed_to"] = new_p if cand == old_p else new_p[:len(new_p) - (len(old_p) - len(cand))] if new_p.endswith(old_p[len(cand):]) else new_p
                    hits.setdefault(key, []).append(item)
        return {"status": "done", "since": since, "until": head}, hits

    def remove_unproduced(self, old_gen, inactive):
        cats_of = {e["install"]: [c for c in CATEGORIES if c in (e.get("confirm_scope") or [])] for e in inactive}
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
                u = {"path": key, "cats": cats_of.get(key, [])}
                if not self.gate(u, common.sha256_text(""), "remove"):
                    self.gen[key] = rec
                    continue
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

    # ---------------------------------------------------------------- apply
    def apply(self):
        old = self.load_yaml(MANIFEST, None)
        old_gen = dict((old or {}).get("generated") or {})
        cfg = self.load_yaml(CONFIG, {}) or {}
        flag = True if self.a.managed_review_copy else (False if self.a.no_managed_review_copy else None)
        if flag is not None and self.cmd != "init":
            self.out("AGENTIVE_WARN --managed-review-copy flags are read by init only; update follows .agents/framework.yaml")
            flag = None
        eff = json.loads(json.dumps(cfg))
        if flag is not None:
            eff.setdefault("review", {})["managed_code_review_copy"] = flag
        ev = evidence.scan(self.repo)
        manifest = self.derive_manifest(ev, old)
        mode = self.probe_t1(manifest)
        self.pending, self.drift, self.overwritten, self.created, self.forced = [], [], [], [], []
        self.errors, self.conflicts, self.gen, self.produced, self.deferred = [], [], {}, set(), []
        ctx = {"blocks": {}, "manifest": manifest, "config": eff, "ecosystems": ev["ecosystems"]}
        entries = self.index.get("entries", [])
        active = [e for e in entries if render.eval_when(e.get("when"), ctx)]
        inactive = [e for e in entries if e not in active]
        # pass 1: rule packs and guard rules (inputs of the render.* blocks); blocks without them first
        ctx["blocks"] = self.blocks(manifest, ev, [], [])
        rulepacks = [(os.path.basename(e["install"])[:-3], self.render_entry(e, ctx)) for e in active if re.match(r"^\.agents/rules/[^/]+\.md$", e["install"])]
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
        region_units = [u for u in units if u["kind"] == "region"]
        stale_status, stale_hits = (self.stale_check(old_gen, region_units) if self.cmd == "update" else (None, {}))
        # removals first (mode switches, units gone, when turned false), then whole files, regions, mirrors
        mirrors_preview = {u["key"] for u in self.mirror_units(mode, active, {})} | {e["install"] for e in self.deferred}
        self.produced |= {u["key"] for u in units} | mirrors_preview
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
        # project config (init only, from the flag; user-owned file, not in manifest.generated)
        if self.cmd == "init" and flag is not None and (cfg.get("review") or {}).get("managed_code_review_copy") != flag:
            newcfg = json.loads(json.dumps(cfg))
            newcfg.setdefault("review", {})["managed_code_review_copy"] = flag
            text = ayaml.dumps(newcfg)
            if self.gate({"path": CONFIG, "cats": ["review_config"]}, common.sha256_text(text), "config"):
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

    def write_pending(self):
        path = self.p(PENDING)
        if not self.pending:
            if os.path.isfile(path) and (self.a.confirm_all or self.confirm or self.cmd == "update"):
                os.unlink(path)
            return
        files = sorted(self.pending, key=lambda x: x["path"])
        old = common.read_json(path, None) if os.path.isfile(path) else None
        doc = {"created_at": old["created_at"] if old and old.get("files") == files and old.get("command") == self.command_name() else now(),
               "command": self.command_name() if self.cmd in ("init", "update") else self.command_name("update"),
               "framework_version": self.version, "files": files}
        if old != doc:
            common.write_json(path, doc)
        for f in files:
            self.out(f"AGENTIVE_CONFIRM category={f['category']} path={f['path']} sha256={f['rendered_sha256']}")

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
        pend = [v for v in VERB_ORDER if manifest["verbs"].get(v, {}).get("status") == "pending"]
        for v in pend:
            self.out(f"AGENTIVE_PENDING verb={v} reason=no_evidence")
        for v in VERB_ORDER:
            s = manifest["verbs"].get(v, {})
            if s.get("cmd") and (s.get("proof") or {}).get("pass_run_id") == "unproved":
                self.out(f"AGENTIVE_UNPROVED verb={v} cmd={s['cmd']}")
        for r in gd.not_expressible(rules):
            self.out(f"AGENTIVE_WARN not-expressible ask item {r['id']} is denied (no permission rule can express it): {r['reason']}")
        rep = [
            "codex: a human trusts the agentive hooks once in Codex /hooks (untrusted project hooks do not run) and trusts the project .codex/ layer",
            "managed-settings (org admin, optional; agentive writes nothing): allowManagedModsOnly, or strictKnownMarketplaces + disableSideloadFlags, keeps permissions.deny above user mods",
            "github: turn on 'Dismiss stale pull request approvals when new commits are pushed' and 'Require approval of the most recent reviewable push'",
            "github: keep 'Allow Copilot approvals to count toward merge requirements' off",
            "github: turn on secret scanning push protection (organization-owned private and internal repositories need GitHub Secret Protection on GitHub Team or Enterprise Cloud)",
            "github: on GitHub Free private repositories branch protection and CODEOWNERS are unavailable and dependency review needs GitHub Team with Code Security, so those gate parts are not enforced there",
        ]
        for r in rep:
            self.out("AGENTIVE_REPORT " + r)
        if ev["secret_scanner"]:
            self.out("AGENTIVE_REPORT secret scanner configuration found: its staged or diff scan is wired into the verify verb")
        nxt = []
        if self.pending:
            nxt.append(f"show the AGENTIVE_CONFIRM files to the human; after a yes rerun with --confirm-all (or --confirm <path>...)" if not self.headless
                       else "the next interactive session shows the skipped confirm_scope files (.agents/state/pending-confirmation.json)")
        if self.drift:
            nxt.append("show the drift report (.agents/state/drift-report.json) and apply the items the human accepts with --accept <id>")
        if pend and not ev["ecosystems"]:
            nxt.append("scaffold the project with your own agent, then run " + self.command_name("update"))
        if any((manifest["verbs"].get(v, {}).get("proof") or {}).get("pass_run_id") == "unproved" for v in VERB_ORDER if manifest["verbs"].get(v, {}).get("cmd")):
            nxt.append("prove the slots (rerun with --prove, or /agentive:reprove)")
        nxt.append("review the changed files and commit them through the normal commit procedure (init and update never commit)")
        self.out("AGENTIVE_NEXT " + "; then ".join(nxt))

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
        self.out(f"AGENTIVE_STATUS summary modified={len(modified)} missing={len(missing)} malformed={len(malformed)} unchanged={unchanged} "
                 f"custom_overrides={'yes (' + str(len(custom)) + ')' if custom else 'no'}")
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
    ap.add_argument("--confirm-all", action="store_true")
    ap.add_argument("--confirm", nargs="+", action="extend", default=[])
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
