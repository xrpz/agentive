# agentive-unit: lib.agentive
# design: spec.review_pipeline.stages[RV3], spec.spec_stage.chain.must_human.rv3 (owner D)
"""RV3 change classification: rules first, then an LLM layer for what the rules leave undecided.

Per changed file, the first rule that decides wins (all [설계 판단] except where the design names the rule):
  1 instruction and criteria files (CLAUDE.md, AGENTS*.md, code_review.md, REVIEW.md) and chain artifacts
    docs/plans/<slug>/intent.md|spec.md never count as docs (design): class config / other
  2 the project's classify.path_rules (a tier-0-eligible class from a path rule is kept only when the mechanical
    check of that class also holds; otherwise the file stays undecided)
  3 generated (agentive manifest paths.generated), lockfile (known lockfile names), whitespace-only (git diff -w
    --ignore-blank-lines is empty, when whitespace_only_is_format), docs (documentation file types)
  4 raises the rules can see: security (RV2 hit with reason auth, security, secrets or permissions), CI files and
    root config files (config), tests (test), package files (deps)
  5 anything else is undecided -> the LLM layer (task rv3-classifier) may pick only non-tier-0 classes; an invalid
    output, or llm_layer: false, gives class other (RV4 then decides on other, which is never tier 0)
"""
import posixpath
import re

from . import config as cfgmod
from . import rv1 as rv1mod
from . import util

DOC_EXT = {".md", ".mdx", ".rst", ".txt", ".adoc", ".asciidoc"}
DOC_ASSET_EXT = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}
NOT_DOCS = re.compile(r"(^|/)(CLAUDE\.md|AGENTS[^/]*\.md|code_review\.md|REVIEW\.md)$")
CHAIN_ART = re.compile(r"^docs/plans/(?!archive/)[^/]+/(intent|spec)\.md$")
CI_GLOBS = [".github/workflows/**", ".gitlab-ci.yml", "Jenkinsfile", ".circleci/**", "azure-pipelines.yml",
            ".buildkite/**", "bitbucket-pipelines.yml"]
CONFIG_EXT = {".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".properties", ".xml"}
SECURITY_REASONS = {"auth", "security", "secrets", "permissions"}


def _is_doc(path):
    ext = posixpath.splitext(path)[1].lower()
    return ext in DOC_EXT or (path.startswith("docs/") and ext in DOC_ASSET_EXT)


def _is_root_config(path):
    base = posixpath.basename(path)
    if path.startswith(".config/") or path.startswith("config/"):
        return True
    if "/" in path:
        return False
    return base.startswith(".") or posixpath.splitext(base)[1].lower() in CONFIG_EXT


def classify_rules(diff, statuses, manifest, rules, rv2_hits):
    """({path: class}, [undecided paths])."""
    gen = [cfgmod.path_glob(p) for p in cfgmod.manifest_paths(manifest, "generated")]
    path_rules = (rules.get("classify") or {}).get("path_rules") or []
    ws_format = (rules.get("classify") or {}).get("whitespace_only_is_format", True)
    tests = rv1mod.test_globs_for(manifest)
    sec_paths = {h["path"] for h in rv2_hits if h["reason"] in SECURITY_REASONS}
    decided, undecided = {}, []

    def whitespace_only(st, p):
        return st == "M" and ws_format and diff.patch([p], context=0, ignore_ws=True).strip() == ""

    def mechanical(cls, st, p):
        return {"generated": util.first_match(gen, p) is not None, "lockfile": rv1mod.is_lockfile(p),
                "format": whitespace_only(st, p), "docs": _is_doc(p) and not NOT_DOCS.search(p) and not CHAIN_ART.match(p)}.get(cls, True)

    for st, p, _ in statuses:
        if NOT_DOCS.search(p):
            decided[p] = "config"
            continue
        if CHAIN_ART.match(p):
            decided[p] = "other"
            continue
        pr = next((e["class"] for e in path_rules if util.glob_match(e["glob"], p)), None)
        if pr is not None:
            if pr not in cfgmod.TIER0_ELIGIBLE or mechanical(pr, st, p):
                decided[p] = pr
            else:
                undecided.append(p)
            continue
        if p in sec_paths:
            decided[p] = "security"
        elif util.first_match(gen, p):
            decided[p] = "generated"
        elif rv1mod.is_lockfile(p):
            decided[p] = "lockfile"
        elif whitespace_only(st, p):
            decided[p] = "format"
        elif _is_doc(p):
            decided[p] = "docs"
        elif util.first_match(CI_GLOBS, p):
            decided[p] = "config"
        elif rv1mod.is_test(p, tests):
            decided[p] = "test"
        elif rv1mod.is_package_file(p):
            decided[p] = "deps"
        elif _is_root_config(p):
            decided[p] = "config"
        else:
            undecided.append(p)
    return decided, undecided


def validate_llm(out, undecided):
    """LLM layer output {"session_id", "classes": [..], "files": {path: class}?}: only non-tier-0 classes."""
    if not isinstance(out, dict):
        return None
    cls = out.get("classes")
    if not isinstance(cls, list) or not cls:
        return None
    for c in cls:
        if c not in cfgmod.CLASSES or c in cfgmod.TIER0_ELIGIBLE:
            return None
    return util.uniq(cls)


def finish(decided, undecided, llm_layer, llm_out=None):
    """review-report rv3 {classes, method, llm_valid?}."""
    classes = util.uniq(sorted(set(decided.values()), key=cfgmod.CLASSES.index))
    if not undecided:
        return {"classes": classes or ["other"], "method": "deterministic"}
    if not llm_layer:
        return {"classes": util.uniq(classes + ["other"]), "method": "deterministic"}
    got = validate_llm(llm_out, undecided) if llm_out is not None else None
    if got is None:
        return {"classes": util.uniq(classes + ["other"]), "method": "llm", "llm_valid": False}
    return {"classes": util.uniq(classes + got), "method": "llm", "llm_valid": True}
