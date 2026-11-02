# agentive-unit: eng.apply
# design: decisions.accepted[SECPATHS], spec.init_apply.security_evidence, spec.review_pipeline.stages[RV2]
"""Security-path evidence for init and update (decision SECPATHS). Deterministic, no network, never writes.

Reads what the organization already declared and turns it into entries of the code_review.md `agentive-review-rules` block:
  L1  CODEOWNERS lines whose owners look security related -> `source: codeowners:<line>`; existing security tool configs
      (gitleaks, semgrep, codeql, trufflehog) -> manifest `security_checks` (RV2 consumes their check results; their rules are
      never copied)
  L3  stack presets, from the root package files: Next.js (`proxy.*` for next >= 16, `middleware.*` below), Spring Security,
      Django settings -> `source: preset:<id>`
K8s RBAC kinds and Terraform IAM resources are NOT presets: they wait for an official confirmation (decisions SECPATHS,
deferred, unverified).
"""
import json
import os
import re

from agentive.review import secpaths  # noqa: E402  (apply.py puts the payload lib on sys.path first)

CODEOWNERS_MAX_BYTES = 3 * 1024 * 1024          # GitHub does not load a CODEOWNERS file larger than 3 MB
SOURCE_PREFIXES = ("codeowners:", "preset:")


def _read(root, rel, limit=None):
    try:
        with open(os.path.join(root, rel), encoding="utf-8", errors="replace") as f:
            return f.read() if limit is None else f.read(limit)
    except OSError:
        return None


def _exists(root, rel):
    return os.path.exists(os.path.join(root, rel))


# ---------------------------------------------------------------- L1: CODEOWNERS
def codeowners(root):
    for rel in secpaths.CODEOWNERS_PATHS:
        p = os.path.join(root, rel)
        if not os.path.isfile(p):
            continue
        if os.path.getsize(p) > CODEOWNERS_MAX_BYTES:
            return {"file": rel, "entries": [], "owned": 0, "skipped": [], "note": "larger than 3 MB, GitHub does not load it either"}
        got = secpaths.parse_codeowners(_read(root, rel))
        got["file"] = rel
        return got
    return {"file": None, "entries": [], "owned": 0, "skipped": []}


# ---------------------------------------------------------------- L1: security tools already configured
WORKFLOW_HINTS = {"gitleaks": ("gitleaks",), "semgrep": ("semgrep",), "codeql": ("github/codeql-action",), "trufflehog": ("trufflehog",)}
CONFIG_FILES = {"gitleaks": (".gitleaks.toml",), "semgrep": (".semgrep.yml", ".semgrep.yaml", ".semgrep"),
                "codeql": (".github/codeql", ".github/codeql-config.yml", ".github/codeql-config.yaml"),
                "trufflehog": (".trufflehog.yml", ".trufflehog.yaml")}


def security_tools(root):
    wf_dir = os.path.join(root, ".github", "workflows")
    workflows = []
    if os.path.isdir(wf_dir):
        for name in sorted(os.listdir(wf_dir)):
            if name.endswith((".yml", ".yaml")):
                workflows.append((f".github/workflows/{name}", (_read(root, f".github/workflows/{name}") or "").lower()))
    out = []
    for tool in ("gitleaks", "semgrep", "codeql", "trufflehog"):
        cfg = next((c for c in CONFIG_FILES[tool] if _exists(root, c)), None)
        if cfg is None:
            cfg = next((rel for rel, text in workflows if any(h in text for h in WORKFLOW_HINTS[tool])), None)
        if cfg:
            out.append({"tool": tool, "config": cfg})
    return out


# ---------------------------------------------------------------- L3: stack presets
def _package_json(root):
    text = _read(root, "package.json")
    if not text:
        return {}
    try:
        doc = json.loads(text)
    except ValueError:
        return {}
    deps = {}
    for k in ("dependencies", "devDependencies", "peerDependencies"):
        if isinstance(doc.get(k), dict):
            deps.update(doc[k])
    return deps


def next_major(spec):
    """The major version a package.json range names (^16.0.1, ~15.2, >=14, 16), None when it names none (latest, canary, workspace:*)."""
    m = re.search(r"(\d+)", spec) if isinstance(spec, str) else None
    return int(m.group(1)) if m else None


def preset_nextjs(root):
    deps = _package_json(root)
    if "next" not in deps:
        return None
    major = next_major(deps["next"])
    # next >= 16: proxy.ts; below: middleware.ts (Next.js docs, file convention proxy: middleware renamed to proxy in 16).
    # A range that names no version (latest, canary) gets both names: more review, never less [design judgment]
    names = ("proxy",) if (major is not None and major >= 16) else ("middleware",) if major is not None else ("proxy", "middleware")
    entries = [{"glob": f"{pre}{n}.{ext}", "reason": "auth", "source": "preset:nextjs"}
               for n in names for pre in ("", "src/") for ext in ("ts", "js")]
    return {"id": "nextjs", "entries": entries, "content": [], "evidence": f"next {deps['next']} in package.json -> " + "/".join(names)}


def preset_spring(root):
    text = "\n".join(_read(root, f) or "" for f in ("pom.xml", "build.gradle", "build.gradle.kts"))
    if not re.search(r"spring-security|spring-boot-starter-security", text):
        return None
    marks = ["@EnableWebSecurity", "SecurityFilterChain"]
    content = [{"path": "**/*.java", "contains": list(marks), "reason": "auth", "source": "preset:spring"},
               {"path": "**/*.kt", "contains": list(marks), "reason": "auth", "source": "preset:spring"}]
    return {"id": "spring", "entries": [], "content": content, "evidence": "spring-security in the build file"}


DJANGO_DEP = re.compile(r"(?im)(?:^|[\s\"'\[,(])django(?![\w.-])")


def preset_django(root):
    files = [f for f in ("pyproject.toml", "Pipfile", "setup.py", "setup.cfg") if _exists(root, f)]
    files += sorted(n for n in os.listdir(root) if re.match(r"^requirements.*\.txt$", n)) if os.path.isdir(root) else []
    if not any(DJANGO_DEP.search(_read(root, f) or "") for f in files):
        return None
    marks = ["SECRET_KEY", "DEBUG", "ALLOWED_HOSTS", "_COOKIE_SECURE"]
    content = [{"path": "**/settings*.py", "contains": list(marks), "reason": "security", "source": "preset:django"},
               {"path": "**/settings/*.py", "contains": list(marks), "reason": "security", "source": "preset:django"}]
    return {"id": "django", "entries": [], "content": content, "evidence": "django in the Python package files"}


def presets(root):
    return [p for p in (preset_nextjs(root), preset_spring(root), preset_django(root)) if p]


def scan(root):
    return {"codeowners": codeowners(root), "presets": presets(root), "tools": security_tools(root)}


# ---------------------------------------------------------------- rendering into the block
def _q(s):
    return json.dumps(s, ensure_ascii=False)


def entry_line(e):
    """One `- {..}` flow mapping line of the rules block."""
    if "contains" in e:
        c = e["contains"]
        cs = "[" + ", ".join(_q(x) for x in c) + "]" if isinstance(c, list) else _q(c)
        parts = [f"path: {_q(e['path'])}", f"contains: {cs}"]
    else:
        parts = [f"glob: {_q(e['glob'])}"]
    parts.append(f"reason: {e['reason']}")
    if e.get("source"):
        parts.append(f"source: {_q(e['source'])}")
    return "- {" + ", ".join(parts) + "}"


def managed_entries(sec):
    """(path entries, content entries) the engine owns in the block, in a stable order."""
    paths = list(sec["codeowners"]["entries"])
    content = []
    for p in sec["presets"]:
        paths += p["entries"]
        content += p["content"]
    return secpaths.dedupe(paths), content


def block_text(sec):
    """(render.rules_managed, render.rules_content): the text of the two placeholders of the rules template."""
    paths, content = managed_entries(sec)
    managed = "\n".join(entry_line(e) for e in paths)
    cont = ("content_rules:\n" + "\n".join("  " + entry_line(e) for e in content)) if content else ""
    return managed, cont


# ---------------------------------------------------------------- update: what changed since the last run
def snapshot(code_review_text):
    """The engine-owned entries (source codeowners:<n> or preset:<id>) and the hand-written ones found in an existing
    code_review.md block: (managed [entries], yours [entries not from the floor and not from the engine])."""
    from agentive.review import config as reviewcfg
    from agentive.review import util as rutil
    from agentive import ayaml
    blocks = rutil.fenced_blocks(code_review_text or "", "yaml agentive-review-rules")
    if not blocks:
        return [], []
    try:
        doc = ayaml.loads(blocks[0])
        tb = ((doc or {}).get("agentive-review-rules") or {}).get("trust_boundary") or {}
    except Exception:       # a broken block is reported by the pipeline and the drift report, not here
        return [], []
    floor = reviewcfg.floor_keys()
    managed, yours = [], []
    for e in secpaths.expand(tb.get("never_ai_approve") or []) + secpaths.expand(tb.get("content_rules") or []):
        if str(e.get("source") or "").startswith(SOURCE_PREFIXES):
            managed.append(e)
        elif secpaths.key(e) not in floor:
            yours.append(e)
    return managed, yours


def diff_entries(old, new):
    """(added, removed) as lists of readable labels, matched on the entry key (the line number in `source` may move)."""
    ok = {secpaths.key(e): e for e in old}
    nk = {secpaths.key(e): e for e in new}
    show = lambda e: f"{secpaths.label(e)} ({e.get('source')})"      # noqa: E731
    return [show(nk[k]) for k in nk if k not in ok], [show(ok[k]) for k in ok if k not in nk]


def report_lines(sec, old_managed, yours, first_run, region_customized):
    """AGENTIVE_REPORT lines (without the prefix) for init and update."""
    out = []
    co = sec["codeowners"]
    if co["file"]:
        out.append(f"security paths: {co['file']} has {co['owned']} owned lines, {len(co['entries'])} name a security owner "
                   f"(owner names with security, appsec, secops, infosec, sre, iam or privacy as a word) and are snapshotted into "
                   f"code_review.md as source codeowners:<line>"
                   + (f"; skipped: {', '.join(f'line {n} {p} ({w})' for n, p, w in co['skipped'][:5])}" if co["skipped"] else "")
                   + (f"; {co['note']}" if co.get("note") else ""))
    else:
        out.append("security paths: no CODEOWNERS file (.github/CODEOWNERS, CODEOWNERS, docs/CODEOWNERS); declaring security owners there "
                   "adds those paths to code_review.md on the next update")
    if sec["presets"]:
        out.append("security paths: stack presets " + "; ".join(f"{p['id']} ({p['evidence']})" for p in sec["presets"]))
    if sec["tools"]:
        out.append("security paths: security tools found, their check results are read by the review gate and their rules are not copied: "
                   + ", ".join(f"{t['tool']} ({t['config']})" for t in sec["tools"]))
    if not first_run:
        new = managed_entries(sec)[0] + managed_entries(sec)[1]
        added, removed = diff_entries(old_managed, new)
        if added or removed:
            applied = "written to code_review.md#rules; review that change in the PR" if not region_customized else \
                "NOT written, code_review.md#rules has hand edits: merge them by hand or run update with --accept code_review.md#rules"
            out.append("security paths changed since the last run (" + applied + "): "
                       + ("added " + ", ".join(added) if added else "") + ("; " if added and removed else "")
                       + ("no longer declared " + ", ".join(removed) if removed else ""))
        if yours:
            out.append(f"security paths: {len(yours)} entries in code_review.md#rules are yours ("
                       + ", ".join(secpaths.label(e) for e in yours[:5]) + "); update keeps them only while that region has hand edits; "
                       "move them to .agents/custom/code_review.md under trust_boundary.add so every update keeps them")
    return out
