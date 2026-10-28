#!/usr/bin/env python3
# agentive-unit: gen.generate
# design: spec.framework_repo (source = framework.yaml + generator, FM.generate), decisions.defaults[F1]
"""Build every generated output of the agentive framework repo from its sources.

  python3 tools/generate.py           write all outputs, delete orphaned generated files
  python3 tools/generate.py --check   write nothing; exit 1 naming every STALE, MISSING or ORPHANED file
  python3 tools/generate.py --list    print every output path and the unit or builtin that makes it

Inputs (read-only here): framework.yaml (framework-repo-config), design/framework.yaml, design/schemas/,
package.json (version, repository), tools/bundles.yaml, src/units/*.yaml and the files under src/.
Outputs: the Claude Code plugin, the Codex plugin, both marketplace files, docs/map/index.html,
docs/DECISIONS.md. docs/IMPLEMENTATION.md describes the model; this file holds logic only.

Output lines are for agents: `ERROR <where>: <what>. NEXT: <fix>` for source problems,
`STALE|MISSING|ORPHANED|MODE <path>` for output drift, and one final summary line.
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile

try:
    import yaml
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource
except ImportError as e:  # dev-time dependencies of the framework repo only
    print(f"ERROR tools/generate.py: missing dev dependency ({e.name}). NEXT: python3 -m pip install -r requirements-dev.txt")
    sys.exit(2)

SRC_ROOTS = {
    "repo_layer": "src/repo-layer/",
    "engine": "src/engine/",
    "plugin_claude": "src/plugin/claude-code/",
    "plugin_codex": "src/plugin/codex/",
}
U2_CATEGORIES = ["hooks", "permissions", "ci", "claude_settings", "codex_dir", "review_config"]
HEADER_EXT = {".sh", ".py", ".yaml", ".yml", ".toml", ".rules", ".bash"}
GEN_PLACEHOLDER = re.compile(r"%%agentive:gen\.([a-z_]+)%%")
ANY_PLACEHOLDER = re.compile(r"%%agentive:[A-Za-z0-9_.<>\[\]-]+%%")
FORBIDDEN_SRC_NAMES = {"AGENTS.md", "CLAUDE.md"}  # loaded as instructions when an agent reads that directory
IGNORED = {"__pycache__", ".DS_Store"}


class Problems:
    def __init__(self):
        self.items = []

    def error(self, where, what, fix):
        self.items.append(f"ERROR {where}: {what}. NEXT: {fix}")

    def __bool__(self):
        return bool(self.items)


# ---------------------------------------------------------------- inputs
def load_yaml(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def schema_registry(root):
    reg, schemas = Registry(), {}
    d = os.path.join(root, "design", "schemas")
    for name in sorted(os.listdir(d)):
        if name.endswith(".schema.json"):
            with open(os.path.join(d, name), encoding="utf-8") as f:
                s = json.load(f)
            schemas[name[:-len(".schema.json")]] = s
            reg = reg.with_resource(s["$id"], Resource.from_contents(s))
    return reg, schemas


def validate(instance, schema, reg):
    return [f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}"
            for e in Draft202012Validator(schema, registry=reg).iter_errors(instance)]


def design_lookup(design, ref):
    """Resolve `a.b[c].d` in design/framework.yaml; [c] picks the list item whose id is c (or the dict key c)."""
    node = design
    for part in re.findall(r"[^.\[\]]+|\[[^\]]+\]", ref):
        if part.startswith("["):
            key = part[1:-1]
            if isinstance(node, list):
                hit = [x for x in node if isinstance(x, dict) and str(x.get("id")) == key]
                if not hit:
                    return False, None
                node = hit[0]
            elif isinstance(node, dict) and key in node:
                node = node[key]
            else:
                return False, None
        else:
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                return False, None
    return True, node


def unescape(rel):
    """src/repo-layer path -> install path: a component 'dot-x' becomes '.x'."""
    return "/".join(("." + c[4:]) if c.startswith("dot-") else c for c in rel.split("/"))


def walk_files(root, rel_dir):
    """Sorted repo-relative POSIX paths of regular files under rel_dir (ignores __pycache__ and *.pyc)."""
    out = []
    base = os.path.join(root, rel_dir)
    for d, dirs, files in os.walk(base):
        dirs[:] = sorted(x for x in dirs if x not in IGNORED)
        for name in sorted(files):
            if name in IGNORED or name.endswith(".pyc"):
                continue
            out.append(os.path.relpath(os.path.join(d, name), root).replace(os.sep, "/"))
    return out


def read_bytes(root, rel):
    with open(os.path.join(root, rel), "rb") as f:
        return f.read()


def is_exec(root, rel):
    return bool(os.stat(os.path.join(root, rel)).st_mode & stat.S_IXUSR)


# ---------------------------------------------------------------- model
class Ctx:
    def __init__(self, root):
        self.root = root
        self.p = Problems()
        self.outputs = {}   # path -> (bytes, executable, maker)
        self.cfg = load_yaml(os.path.join(root, "framework.yaml"))
        self.design = load_yaml(os.path.join(root, "design", "framework.yaml"))
        self.bundles = load_yaml(os.path.join(root, "tools", "bundles.yaml"))
        with open(os.path.join(root, "package.json"), encoding="utf-8") as f:
            self.pkg = json.load(f)
        self.reg, self.schemas = schema_registry(root)
        for e in validate(self.cfg, self.schemas["framework-repo-config"], self.reg):
            self.p.error("framework.yaml", f"not valid framework-repo-config ({e})", "fix framework.yaml against design/schemas/framework-repo-config.schema.json")
        self.plugin_name = (self.cfg.get("plugin") or {}).get("name", "agentive")
        self.marketplace_name = (self.cfg.get("plugin") or {}).get("marketplace_name", self.plugin_name)
        if self.design["spec"]["plugin"]["name"] != self.plugin_name:
            self.p.error("framework.yaml", f"plugin.name {self.plugin_name!r} differs from design spec.plugin.name {self.design['spec']['plugin']['name']!r}",
                         "change both in the same edit (decision D14)")
        self.version = self.pkg.get("version", "")
        if not re.fullmatch(r"\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?", self.version):
            self.p.error("package.json", f"version {self.version!r} is not semver", "let the changesets version step set it (npm run version)")
        repo = self.pkg.get("repository")
        self.repo_url = repo.get("url") if isinstance(repo, dict) else repo
        self.repo_url = re.sub(r"^git\+|\.git$", "", self.repo_url or "")
        self.gen_values = {"version": self.version, "plugin_name": self.plugin_name, "marketplace_name": self.marketplace_name}

    def emit(self, path, data, executable=False, maker="builtin"):
        if isinstance(data, str):
            data = data.encode("utf-8")
        if path in self.outputs:
            self.p.error(path, f"produced twice (by {self.outputs[path][2]} and {maker})", "give each output exactly one unit")
            return
        self.outputs[path] = (data, executable, maker)


def substitute(ctx, data, where):
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return data
    def rep(m):
        k = m.group(1)
        if k not in ctx.gen_values:
            ctx.p.error(where, f"unknown generate-time placeholder %%agentive:gen.{k}%%", f"use one of {sorted(ctx.gen_values)} or an install-time key")
            return m.group(0)
        return ctx.gen_values[k]
    return GEN_PLACEHOLDER.sub(rep, text).encode("utf-8")


# ---------------------------------------------------------------- units
def load_units(ctx):
    schema = json.load(open(os.path.join(ctx.root, "tools", "schemas", "units.schema.json"), encoding="utf-8"))
    units = []
    udir = os.path.join(ctx.root, "src", "units")
    for name in sorted(os.listdir(udir)) if os.path.isdir(udir) else []:
        if not name.endswith(".yaml"):
            continue
        rel = f"src/units/{name}"
        try:
            doc = load_yaml(os.path.join(udir, name)) or {}
        except yaml.YAMLError as e:
            ctx.p.error(rel, f"YAML syntax error ({str(e).splitlines()[-1].strip()})", "fix the syntax; quote list items that contain [ ] (refs like \"spec.x[f-y]\")")
            continue
        errs = validate(doc, schema, Registry())
        for e in errs:
            ctx.p.error(rel, f"not valid ({e})", "fix it against tools/schemas/units.schema.json")
        if errs:
            continue
        for u in doc.get("units") or []:
            u = dict(u)
            u["_file"], u["owner"] = rel, doc["owner"]
            units.append(u)
    seen = {}
    for u in units:
        if u["id"] in seen:
            ctx.p.error(u["_file"], f"unit id {u['id']} also defined in {seen[u['id']]}", "rename one of them")
        seen[u["id"]] = u["_file"]
    return units


def check_unit_refs(ctx, u):
    where = f"{u['_file']} [{u['id']}]"
    for ref in u["refs"]:
        if ref.startswith("docs/"):
            if not os.path.exists(os.path.join(ctx.root, ref.split(" ")[0].split("#")[0])):
                ctx.p.error(where, f"ref {ref!r} names a missing file", "point refs at design/framework.yaml paths or existing docs")
            continue
        ok, _ = design_lookup(ctx.design, ref)
        if not ok:
            ctx.p.error(where, f"ref {ref!r} does not resolve in design/framework.yaml", "use a dotted design path, list items by id in brackets")
    if u["kind"] != "repo_layer":
        for k in ("install", "f", "class", "regions", "confirm_scope", "when", "generator"):
            if k in u:
                ctx.p.error(where, f"field {k} is only for kind repo_layer", "remove it")
        return
    if "f" in u:
        ok, _ = design_lookup(ctx.design, f"spec.repo_layer.files[{u['f']}]")
        if not ok:
            ctx.p.error(where, f"f {u['f']!r} is not in design spec.repo_layer.files", "use an existing f-* id or leave f out")
    if "schema" in u and u["schema"] not in ctx.schemas:
        ctx.p.error(where, f"schema {u['schema']!r} is not in design/schemas", "use a schema id from design/schemas/")
    cls = u.get("class", "whole")
    if cls == "shared":
        if "regions" not in u or "src" not in u:
            ctx.p.error(where, "a shared unit needs src '<escaped path>.regions/' and regions", "see docs/IMPLEMENTATION.md §3")
            return
        install = u.get("install") or unescape(u["src"][len(SRC_ROOTS['repo_layer']):].rstrip("/"))[:-len(".regions")]
        known = [r["id"] for r in ctx.design["spec"]["init_apply"]["managed_regions"]["regions"]]
        for r in u["regions"]:
            if f"{install}#{r}" not in known:
                ctx.p.error(where, f"region {install}#{r} is not in design init_apply.managed_regions.regions", "use a designed region id (or apply a design change first)")
    elif "regions" in u:
        ctx.p.error(where, "regions given on a whole-file unit", "set class: shared or drop regions")


def unit_files(ctx, u):
    """[(src file, path relative to the unit's src)] for a src unit; validates placement and names."""
    where = f"{u['_file']} [{u['id']}]"
    src = u["src"]
    root = SRC_ROOTS[u["kind"]]
    if not src.startswith(root):
        ctx.p.error(where, f"src {src} is outside {root}", f"move the source under {root}")
        return []
    full = os.path.join(ctx.root, src)
    if src.endswith("/"):
        if not os.path.isdir(full):
            ctx.p.error(where, f"src directory {src} does not exist", "create it or fix the path")
            return []
        files = walk_files(ctx.root, src)
        if not files:
            ctx.p.error(where, f"src directory {src} is empty", "add files or remove the unit")
        return [(f, f[len(src):]) for f in files]
    if not os.path.isfile(full):
        ctx.p.error(where, f"src file {src} does not exist", "create it or fix the path")
        return []
    return [(src, os.path.basename(src))]


def check_source_file(ctx, u, f):
    where = f"{f} [{u['id']}]"
    for c in f.split("/"):
        if c.startswith("."):
            ctx.p.error(where, f"path component {c!r} starts with '.'", f"store it as 'dot-{c[1:]}' (src/ never holds dot paths, docs/IMPLEMENTATION.md §3)")
        if c in FORBIDDEN_SRC_NAMES:
            ctx.p.error(where, f"source named {c}", "store AGENTS.md / CLAUDE.md content as a shared unit '<name>.regions/<region>.md'")
    if os.path.islink(os.path.join(ctx.root, f)):
        ctx.p.error(where, "symlink in src/", "use a real file (symlinks break on Windows checkouts)")
    data = read_bytes(ctx.root, f)
    ext = os.path.splitext(f)[1]
    shebang = data.startswith(b"#!")
    if shebang and not is_exec(ctx.root, f):
        ctx.p.error(where, "has a shebang but is not executable", f"chmod +x {f}")
    if is_exec(ctx.root, f) and not shebang:
        ctx.p.error(where, "is executable without a shebang", f"add '#!/bin/sh' (or python3) or chmod -x {f}")
    if u.get("header", True) and (ext in HEADER_EXT or (shebang and ext == "")) and ".regions/" not in f:
        head = data.split(b"\n", 4)[:4]
        if not any(b"agentive-unit: " + u["id"].encode() in ln for ln in head):
            ctx.p.error(where, f"no header line 'agentive-unit: {u['id']}' in the first 3 lines (after a shebang)",
                        "add '# agentive-unit: <unit id>' and '# design: <design refs>' (docs/IMPLEMENTATION.md §5)")
    try:
        data.decode("utf-8")
        binary = False
    except UnicodeDecodeError:
        binary = True
    if b"\r\n" in data and not binary:
        ctx.p.error(where, "CRLF line endings", "convert to LF")
    if "schema" in u and ext in (".yaml", ".yml", ".json") and not ANY_PLACEHOLDER.search(data.decode("utf-8", "replace")):
        try:
            inst = json.loads(data) if ext == ".json" else yaml.safe_load(data)
            for e in validate(inst, ctx.schemas[u["schema"]], ctx.reg):
                ctx.p.error(where, f"not valid {u['schema']} ({e})", f"fix it against design/schemas/{u['schema']}.schema.json")
        except Exception as e:  # noqa: BLE001
            ctx.p.error(where, f"does not parse ({e})", "fix the syntax")


def check_orphan_sources(ctx, units):
    covered = {}
    for u in units:
        if "src" not in u:
            continue
        s = u["src"]
        for other, oid in covered.items():
            if s == other or (s.startswith(other) and other.endswith("/")) or (other.startswith(s) and s.endswith("/")):
                ctx.p.error(f"{u['_file']} [{u['id']}]", f"src {s} overlaps unit {oid} ({other})", "make unit sources disjoint")
        covered[s] = u["id"]
    for root in SRC_ROOTS.values():
        for f in walk_files(ctx.root, root):
            if not any(f == s or (s.endswith("/") and f.startswith(s)) for s in covered):
                ctx.p.error(f, "source file belongs to no unit (orphaned source)", "add a unit entry in your src/units/<area>.yaml or delete the file")
    for f in walk_files(ctx.root, "src"):
        if not f.startswith("src/units/") and not any(f.startswith(r) for r in SRC_ROOTS.values()):
            ctx.p.error(f, "file outside the known source roots", f"move it under one of {sorted(SRC_ROOTS.values())}")


# ---------------------------------------------------------------- builtin generators
def gen_compat(ctx):
    c = ctx.design["spec"]["compat"]
    doc = {"claude_code": {"min": c["claude_code"]["min"], "features": dict(c["claude_code"]["features"])},
           "codex": {"min": c["codex"]["min"], "features": dict(c["codex"]["features"])}}
    for e in validate(doc, ctx.schemas["version-compat"], ctx.reg):
        ctx.p.error("design spec.compat", f"renders an invalid .agents/compat.yaml ({e})", "fix spec.compat in design/framework.yaml")
    lines = ["# agentive-unit: rl.compat",
             "# design: spec.compat (generated by tools/generate.py; SessionStart compares it with the installed tool versions)"]
    text = "\n".join(lines) + "\n" + ayaml().dumps(doc)
    if ayaml().loads(text) != doc:
        ctx.p.error("rl.compat", "ayaml round trip changed .agents/compat.yaml", "fix src/repo-layer/dot-agents/lib/agentive/ayaml.py")
    return text


def ayaml():
    """The run-time YAML subset module (outputs that user-repo scripts read are written with it)."""
    lib = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src", "repo-layer", "dot-agents", "lib")
    if lib not in sys.path:
        sys.path.insert(0, lib)
    from agentive import ayaml as m
    return m


GENERATORS = {"compat": gen_compat}


def build_payload(ctx, units):
    """{payload-relative path: (bytes, executable, maker)} + the payload index entries."""
    files, index = {}, []
    for u in units:
        if u["kind"] not in ("repo_layer", "engine"):
            continue
        if u["kind"] == "engine":
            for f, rel in unit_files(ctx, u):
                check_source_file(ctx, u, f)
                files[f"engine/{f[len(SRC_ROOTS['engine']):]}"] = (substitute(ctx, read_bytes(ctx.root, f), f), is_exec(ctx.root, f), u["id"])
            continue
        base = {"unit": u["id"], "owner": u["owner"], "class": u.get("class", "whole"),
                "confirm_scope": u.get("confirm_scope", [])}
        for k in ("f", "when", "schema"):
            if k in u:
                base[k] = u[k]
        if "generator" in u:
            install = u.get("install")
            if not install:
                ctx.p.error(f"{u['_file']} [{u['id']}]", "a generator unit needs install", "add install: <path in the user repo>")
                continue
            tpl = "repo-layer/" + "/".join(("dot-" + c[1:]) if c.startswith(".") else c for c in install.split("/"))
            files[tpl] = (GENERATORS[u["generator"]](ctx).encode("utf-8"), False, u["id"])
            index.append(dict(base, install=install, template=tpl))
            continue
        src_rel = u["src"][len(SRC_ROOTS["repo_layer"]):]
        if u.get("class") == "shared":
            install = u.get("install") or unescape(src_rel.rstrip("/"))[:-len(".regions")]
            found = {}
            for f, rel in unit_files(ctx, u):
                check_source_file(ctx, u, f)
                region = os.path.splitext(rel)[0]
                found[region] = f
                files["repo-layer/" + f[len(SRC_ROOTS['repo_layer']):]] = (substitute(ctx, read_bytes(ctx.root, f), f), False, u["id"])
            for r in u.get("regions", []):
                if r not in found:
                    ctx.p.error(f"{u['_file']} [{u['id']}]", f"region {r} has no file in {u['src']}", f"add {u['src']}{r}.md (or .txt)")
                    continue
                index.append(dict(base, install=install, region=r, template="repo-layer/" + found[r][len(SRC_ROOTS['repo_layer']):]))
            for r in found:
                if r not in u.get("regions", []):
                    ctx.p.error(found[r], "region file not listed in regions", "add it to regions or delete it")
            continue
        for f, rel in unit_files(ctx, u):
            check_source_file(ctx, u, f)
            tpl_rel = f[len(SRC_ROOTS['repo_layer']):]
            if u["src"].endswith("/"):
                inst_dir = u.get("install") or unescape(src_rel)
                if not isinstance(inst_dir, str) or not inst_dir.endswith("/"):
                    ctx.p.error(f"{u['_file']} [{u['id']}]", "install of a directory unit must be one path ending with '/'", "use a single path with a trailing '/'")
                    break
                install = inst_dir + unescape(rel)
            else:
                install = u.get("install") or unescape(src_rel)
            if isinstance(install, list) and u["src"].endswith("/"):
                ctx.p.error(f"{u['_file']} [{u['id']}]", "install list on a directory unit", "use a list only for a single-file src")
                continue
            files["repo-layer/" + tpl_rel] = (substitute(ctx, read_bytes(ctx.root, f), f), is_exec(ctx.root, f), u["id"])
            for inst in (install if isinstance(install, list) else [install]):
                index.append(dict(base, install=inst, template="repo-layer/" + tpl_rel, executable=is_exec(ctx.root, f)))
    seen = {}
    for e in index:
        key = e["install"] + ("#" + e["region"] if "region" in e else "")
        if key in seen:
            ctx.p.error(key, f"installed by two units ({seen[key]} and {e['unit']})", "give each installed file one unit")
        seen[key] = e["unit"]
    index.sort(key=lambda e: (e["install"], e.get("region", "")))
    idx = {"generated_by": "tools/generate.py", "plugin": ctx.plugin_name, "version": ctx.version,
           "escape_rule": "template paths keep src/ escaping: a component 'dot-x' is '.x' in the user repo; install is the real path",
           "entries": index}
    files["index.json"] = (json.dumps(idx, ensure_ascii=False, indent=2).encode() + b"\n", False, "builtin:payload-index")
    return files


def gen_plugins(ctx, units, payload):
    b = ctx.bundles
    common = {"name": ctx.plugin_name, "version": ctx.version, "description": b["description"]}
    # Claude Code plugin
    croot = b["claude"]["root"]
    cman = dict(common, author={"name": b["owner"]["name"]}, license=ctx.pkg.get("license", "MIT"), keywords=b.get("keywords", []))
    if b["claude"].get("icon"):
        cman["icon"] = b["claude"]["icon"]
    if ctx.repo_url:
        cman["homepage"] = ctx.repo_url
        cman["repository"] = ctx.repo_url
    ctx.emit(f"{croot}/.claude-plugin/plugin.json", json.dumps(cman, ensure_ascii=False, indent=2) + "\n", maker="builtin:claude-manifest")
    # Codex plugin (portable plugin.json at the plugin root [S14])
    xroot = b["codex"]["root"]
    xman = dict(common, author={"name": b["owner"]["name"]}, license=ctx.pkg.get("license", "MIT"), keywords=b.get("keywords", []))
    if ctx.repo_url:
        xman["homepage"] = ctx.repo_url
        xman["repository"] = ctx.repo_url
    ctx.emit(f"{xroot}/plugin.json", json.dumps(xman, ensure_ascii=False, indent=2) + "\n", maker="builtin:codex-manifest")
    # plugin files from units
    for u in units:
        if u["kind"] not in ("plugin_claude", "plugin_codex"):
            continue
        broot = croot if u["kind"] == "plugin_claude" else xroot
        for f, _rel in unit_files(ctx, u):
            check_source_file(ctx, u, f)
            rel = f[len(SRC_ROOTS[u["kind"]]):]
            if rel.split("/")[0] in (".claude-plugin", "plugin.json", "payload", "skills.lock"):
                ctx.p.error(f, "collides with a generated plugin file", "rename it; manifests, payload/ and skills.lock are generated")
            if u["kind"] == "plugin_claude" and rel.split("/")[0] == "bin":
                ctx.p.error(f, "Claude plugin bin/ directory", "move it (claude.ai and Cowork do not install a plugin with bin/; IMPLEMENTATION.md §9.1)")
            ctx.emit(f"{broot}/{rel}", substitute(ctx, read_bytes(ctx.root, f), f), is_exec(ctx.root, f), u["id"])
    # payload copies
    for key in ("claude", "codex"):
        root = b[key]["root"]
        for pdir in b[key]["payload"]:
            for rel, (data, ex, maker) in sorted(payload.items()):
                ctx.emit(f"{root}/{pdir}/{rel}", data, ex, maker)
    # Codex bootstrap lock (spec.distribution.codex.codex_only_bootstrap)
    lock_path = f"{xroot}/skills.lock"
    old = {}
    try:
        with open(os.path.join(ctx.root, lock_path), encoding="utf-8") as f:
            old = json.load(f).get("skills", {})
    except (FileNotFoundError, ValueError):
        pass
    skills = {}
    for name in b["codex"]["lock_skills"]:
        prefix = f"{xroot}/skills/{name}/"
        lines = sorted(f"{p[len(prefix):]}\0{hashlib.sha256(d).hexdigest()}\n" for p, (d, _e, _m) in ctx.outputs.items() if p.startswith(prefix))
        if not lines:
            ctx.p.error(f"tools/bundles.yaml codex.lock_skills", f"skill {name} has no files under {prefix}", "add the Codex skill source (owner B) or drop it from lock_skills")
            continue
        sha = hashlib.sha256("".join(lines).encode()).hexdigest()
        prev = old.get(name, {})
        verified = prev.get("verified_at") if prev.get("sha256") == sha else datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        entry = {"path": f"skills/{name}", "sha256": sha, "verified_at": verified, "pinned_ref": f"v{ctx.version}"}
        if ctx.repo_url:
            entry["source_url"] = ctx.repo_url
        skills[name] = entry
    lock = {"skills": skills}
    for e in validate(lock, ctx.schemas["skills-lock"], ctx.reg):
        ctx.p.error(lock_path, f"not valid skills-lock ({e})", "fix tools/generate.py gen_plugins")
    ctx.emit(lock_path, json.dumps(lock, indent=2) + "\n", maker="builtin:codex-skills-lock")


def gen_marketplaces(ctx):
    b = ctx.bundles
    claude = {"name": ctx.marketplace_name, "description": b["marketplace_description"], "owner": {"name": b["owner"]["name"]},
              "plugins": [{"name": ctx.plugin_name, "source": "./" + b["claude"]["root"], "description": b["description"]}]}
    ctx.emit(".claude-plugin/marketplace.json", json.dumps(claude, ensure_ascii=False, indent=2) + "\n", maker="builtin:claude-marketplace")
    codex = {"name": ctx.marketplace_name, "interface": {"displayName": ctx.marketplace_name},
             "plugins": [{"name": ctx.plugin_name, "source": {"source": "local", "path": "./" + b["codex"]["root"]},
                          "policy": dict(b["codex"]["policy"]), "category": b["codex"]["category"]}]}
    ctx.emit(".agents/plugins/marketplace.json", json.dumps(codex, ensure_ascii=False, indent=2) + "\n", maker="builtin:codex-marketplace")


def gen_map(ctx):
    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, "index.html")
        r = subprocess.run([sys.executable, os.path.join(ctx.root, "tools", "map", "gen.py"),
                            os.path.join(ctx.root, "design", "framework.yaml"), os.path.join(ctx.root, "tools", "map", "tpl.html"), out],
                           capture_output=True, text=True)
        if r.returncode != 0:
            ctx.p.error("tools/map/gen.py", f"failed: {r.stderr.strip()[-500:]}", "fix design/framework.yaml view/layout or tools/map/gen.py")
            return
        for w in r.stderr.strip().splitlines():
            print(f"WARN docs/map/index.html: {w}")
        ctx.emit("docs/map/index.html", read_bytes(td, "index.html"), maker="builtin:map")


def md_cell(s):
    return str(s if s is not None else "").replace("|", "\\|").replace("\n", " ")


def gen_decisions(ctx):
    d = ctx.design["decisions"]
    out = ["<!-- Generated by tools/generate.py from design/framework.yaml decisions. Do not edit: change the design and run the generator. -->",
           "# agentive decisions",
           "",
           "Index of the decision register in `design/framework.yaml` (`decisions.*`). Each id is stable and never reused.",
           "Read the full entry in the design file (`grep -n \"id: <ID>\" design/framework.yaml`); the map page is `docs/map/index.html`.",
           ""]
    groups = [("accepted", "Accepted design decisions", ["id", "title", "decided_at", "revised_at", "spec_ref"]),
              ("decided", "Decided user choices (U items)", ["id", "title", "value", "decided_at", "spec_ref"]),
              ("defaults", "Defaults and constants", ["id", "title", "key"]),
              ("probes", "Probes", ["id", "title"]),
              ("resolved", "Resolved (obsolete) items", ["id", "title", "resolved_by", "resolved_at"]),
              ("removed", "Removed by the user (never reintroduce)", ["id", "title", "decided_at"])]
    for key, title, cols in groups:
        items = d.get(key) or []
        out += [f"## {title} ({len(items)})", "", "| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
        for it in items:
            out.append("| " + " | ".join(md_cell(it.get(c)) for c in cols) + " |")
        out.append("")
    ctx.emit("docs/DECISIONS.md", "\n".join(out), maker="builtin:decisions")


REGION_START = "<!-- AGENTIVE-FRAMEWORK START install-sequence -->"
REGION_END = "<!-- AGENTIVE-FRAMEWORK END install-sequence -->"


def gen_install_region(ctx):
    """Root AGENTS.md keeps the install_sequence verbatim (FM.install): the generator owns one marked region."""
    dist = ctx.design["spec"]["distribution"]
    cc, cx = dist["claude_code"], dist["codex"]
    out = [REGION_START,
           "<!-- generated by tools/generate.py from design spec.distribution; edit the design, not this region -->",
           "Claude Code (`spec.distribution.claude_code.install_sequence`): run exactly these steps and check each expected output.",
           ""]
    for st in cc["install_sequence"]:
        line = f"{st['step']}. "
        if "cmd" in st:
            line += f"`{st['cmd']}`"
        if "action" in st:
            line += st["action"]
        if "expect" in st:
            line += f" -> expect `{st['expect']}`"
        if "verify" in st:
            line += " -> verify: " + "; ".join(f"`{v}`" for v in st["verify"])
        out.append(line)
    alt = cc["one_command_alt"]
    out += ["",
            f"- One-command alternative (Claude Code >= {alt['min_version']}): `{alt['cmd']}` ({alt['note']}).",
            f"- Collaborators: {cc['collaborators']}.",
            f"- Update: `{cc['auto_update']['update_cmd']}`, then {cc['auto_update']['apply']}, then {cc['auto_update']['then']}.",
            "",
            f"Codex (`spec.distribution.codex`, policy {cx['policy']}): marketplace file `{cx['marketplace_file']}`; plugin manifest {cx['plugin_manifest']}.",
            "",
            "- Agent may run: " + "; ".join(f"`{c}`" for c in cx["agent_runnable"]) + ".",
            "- Human only: " + "; ".join(cx["human_only"]) + ".",
            f"- Update: {cx['update']}.",
            f"- Codex-only bootstrap: {cx['codex_only_bootstrap']}.",
            "",
            f"Values for this repo: `<owner/repo>` = `{ctx.bundles['repo_slug']}`, `<name>` = `{ctx.marketplace_name}` (marketplace name), "
            f"Codex skill sources `{ctx.bundles['codex']['root']}/skills/agentive-init/` and `.../agentive-update/`, release lock `{ctx.bundles['codex']['root']}/skills.lock`.",
            REGION_END]
    body = "\n".join(out)
    path = os.path.join(ctx.root, "AGENTS.md")
    if not os.path.exists(path):
        ctx.p.error("AGENTS.md", "missing", "restore the root AGENTS.md (editing rules + install-sequence region)")
        return
    text = read_bytes(ctx.root, "AGENTS.md").decode("utf-8")
    if text.count(REGION_START) != 1 or text.count(REGION_END) != 1 or text.index(REGION_START) > text.index(REGION_END):
        ctx.p.error("AGENTS.md", "install-sequence region markers missing or malformed", f"keep exactly one {REGION_START} ... {REGION_END} pair")
        return
    a, b = text.index(REGION_START), text.index(REGION_END) + len(REGION_END)
    ctx.emit("AGENTS.md", text[:a] + body + text[b:], maker="builtin:agents-md-install-region")


# ---------------------------------------------------------------- compare / write
def generated_roots(ctx):
    return [r.rstrip("/") for r in ctx.bundles["generated_roots"]]


def on_disk_generated(ctx):
    found = set()
    for r in generated_roots(ctx):
        if os.path.isdir(os.path.join(ctx.root, r)):
            found.update(walk_files(ctx.root, r))
    return found


def build(root):
    ctx = Ctx(root)
    units = load_units(ctx)
    for u in units:
        check_unit_refs(ctx, u)
    check_orphan_sources(ctx, units)
    payload = build_payload(ctx, units)
    gen_plugins(ctx, units, payload)
    gen_marketplaces(ctx)
    gen_map(ctx)
    gen_decisions(ctx)
    gen_install_region(ctx)
    for path in ctx.outputs:
        if not any(path == r or path.startswith(r + "/") for r in generated_roots(ctx)) and path not in ctx.bundles["generated_files"]:
            ctx.p.error(path, "output outside tools/bundles.yaml generated_roots and generated_files", "list it there so --check can find orphans")
    return ctx


def compare(ctx):
    drift = []
    for path, (data, ex, _m) in sorted(ctx.outputs.items()):
        full = os.path.join(ctx.root, path)
        if not os.path.isfile(full):
            drift.append(f"MISSING {path}")
            continue
        if read_bytes(ctx.root, path) != data:
            drift.append(f"STALE {path}")
        elif is_exec(ctx.root, path) != ex:
            drift.append(f"MODE {path} (executable should be {str(ex).lower()})")
    for path in sorted(on_disk_generated(ctx) - set(ctx.outputs)):
        drift.append(f"ORPHANED {path}")
    return drift


def write(ctx):
    changed = 0
    for path, (data, ex, _m) in sorted(ctx.outputs.items()):
        full = os.path.join(ctx.root, path)
        if not os.path.isfile(full) or read_bytes(ctx.root, path) != data:
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "wb") as f:
                f.write(data)
            changed += 1
        mode = os.stat(full).st_mode
        want = (mode | 0o111) if ex else (mode & ~0o111)
        if mode != want:
            os.chmod(full, want)
            changed += 1
    for path in sorted(on_disk_generated(ctx) - set(ctx.outputs)):
        os.unlink(os.path.join(ctx.root, path))
        print(f"DELETED {path}")
        changed += 1
    for r in generated_roots(ctx):
        for d, dirs, files in sorted(os.walk(os.path.join(ctx.root, r)), reverse=True):
            if not dirs and not files and not os.listdir(d):
                os.rmdir(d)
    return changed


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true", help="write nothing; fail on stale, missing or orphaned outputs")
    ap.add_argument("--list", action="store_true", help="print every output and its maker")
    ap.add_argument("--root", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))), help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    ctx = build(a.root)
    if ctx.p:
        for line in ctx.p.items:
            print(line)
        print(f"generate: {len(ctx.p.items)} source problem(s); nothing written. NEXT: fix the ERROR lines above, then rerun")
        return 1
    if a.list:
        for path, (_d, _e, maker) in sorted(ctx.outputs.items()):
            print(f"{path}\t{maker}")
        return 0
    if a.check:
        drift = compare(ctx)
        for line in drift:
            print(line)
        if drift:
            print(f"generate --check: {len(drift)} generated file(s) out of date. NEXT: python3 tools/generate.py, then commit the result")
            return 1
        print(f"generate --check: ok ({len(ctx.outputs)} generated files current)")
        return 0
    n = write(ctx)
    print(f"generate: ok ({len(ctx.outputs)} outputs, {n} change(s) written)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
