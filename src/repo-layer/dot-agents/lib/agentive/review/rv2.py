# agentive-unit: lib.agentive
# design: spec.review_pipeline.stages[RV2], spec.review_pipeline.stages[RV2].confirm_scope, spec.work_loop.precommit_review.final_confirmation.non_interactive, spec.spec_stage.chain.must_human (owner D)
"""RV2 trust boundary (llm: false).

hits             never_ai_approve globs of the rules block (base commit) + the built-in protected_check_config
                 (reason check_config, cannot be turned off) + the project's extra protected paths
confirm_scope    one glob list per U2 category; a hit without a human confirmation -> u2.action pr_only_tier2
always_human     the design's always_human items that a script can see (returned as (code, detail) pairs; RV4 turns
                 each into tier 2; codes are review-report gate_reason values where one fits)

Not detectable by a script and left to the RV4 raise-only check (content-based backstop, B12): data deletion and mass
external sends without a path signal. CODEOWNERS paths are opt-in and need a ruleset the design leaves to the team;
the pipeline does not read CODEOWNERS [설계 판단].
"""
import posixpath
import re

from . import config
from . import rv1 as rv1mod
from . import util

TRUSTED_ASSOCIATIONS = ("OWNER", "MEMBER", "COLLABORATOR")
MULTI_PURPOSE = {"package.json", "pyproject.toml", "setup.cfg", "tox.ini", "Cargo.toml", "go.mod", "pom.xml",
                 "build.gradle", "build.gradle.kts", "Makefile", "composer.json", "Gemfile"}
REGION = re.compile(r"<!--\s*AGENTIVE (START|END) ([^\s>]+)\s*-->")
CHAIN_STAGE_ARTIFACT = re.compile(r"^docs/plans/(?!archive/)[^/]+/(intent|spec)\.md$")


def _protected(path, builtin, extra):
    for e in builtin:
        if util.glob_match(e["glob"], path) and not util.first_match(e.get("except", []), path):
            return e["glob"]
    return util.first_match(extra, path)


def _region_spans(text):
    """[(start_line, end_line)] 1-based, of AGENTIVE START/END regions."""
    spans, start = [], None
    for i, ln in enumerate((text or "").splitlines(), 1):
        m = REGION.search(ln)
        if not m:
            continue
        if m.group(1) == "START":
            start = i
        elif start is not None:
            spans.append((start, i))
            start = None
    return spans


def _in_spans(lines, spans):
    return any(a <= ln <= b for ln, _ in lines for a, b in spans)


def _section_spans(text, section):
    """Line spans of a TOML/INI section `[section]` (and its sub-tables `[section.x]`)."""
    spans, start = [], None
    lines = (text or "").splitlines()
    for i, ln in enumerate(lines, 1):
        m = re.match(r"^\s*\[\[?\s*([^\]]+?)\s*\]\]?\s*$", ln)
        if not m:
            continue
        name = m.group(1).strip().strip('"')
        if start is not None:
            spans.append((start, i - 1))
            start = None
        if name == section or name.startswith(section + "."):
            start = i
    if start is not None:
        spans.append((start, len(lines)))
    return spans


def evidence_config_files(manifest):
    """[(path, section or None)] of lint, typecheck and format config files named in verbs.*.evidence.files.
    A multi-purpose file (package.json, pyproject.toml, ...) counts only as `path#section` [설계 판단]."""
    out = []
    for name, v in (manifest.get("verbs") or {}).items():
        if name not in ("lint", "typecheck", "format") or not isinstance(v, dict):
            continue
        for f in ((v.get("evidence") or {}).get("files") or []):
            if not isinstance(f, str) or not f:
                continue
            path, _, section = f.partition("#")
            if section:
                out.append((path, section))
            elif posixpath.basename(path) not in MULTI_PURPOSE:
                out.append((path, None))
    return util.uniq(out)


def run(diff, statuses, hunks, rules, manifest, signals, pr=None, checks=None, rules_problems=None):
    """pr: {author_association, is_fork, labels: [..]} (CI) or None (precommit)."""
    tb = rules["trust_boundary"]
    hits, cs_hits, always = [], [], []
    extra_protected = list(tb.get("protected_check_config") or [])
    ev_files = evidence_config_files(manifest)
    changed = [p for _, p, _ in statuses]
    for st, p, old in statuses:
        names = [p] + ([old] if old else [])
        for n in names:
            for e in tb["never_ai_approve"]:
                if util.glob_match(e["glob"], n):
                    hits.append({"path": p, "glob": e["glob"], "reason": e["reason"]})
            g = _protected(n, config.PROTECTED_CHECK_CONFIG, extra_protected)
            if g:
                hits.append({"path": p, "glob": g, "reason": "check_config"})
        base_name = posixpath.basename(p)
        if base_name in ("AGENTS.md", "CLAUDE.md"):
            h = hunks.get(p) or {}
            if _in_spans(h.get("added", []), _region_spans(diff.show_head(p))) or \
                    _in_spans(h.get("removed", []), _region_spans(diff.show_base(old or p))):
                hits.append({"path": p, "glob": f"{base_name} managed region", "reason": "check_config"})
        for path, section in ev_files:
            if p != path and old != path:
                continue
            if section is None:
                hits.append({"path": p, "glob": path, "reason": "check_config"})
            else:
                h = hunks.get(p) or {}
                if _in_spans(h.get("added", []), _section_spans(diff.show_head(p), section)) or \
                        _in_spans(h.get("removed", []), _section_spans(diff.show_base(old or p), section)):
                    hits.append({"path": p, "glob": f"{path}#{section}", "reason": "check_config"})
        for cat, globs in tb["confirm_scope_globs"].items():
            for n in names:
                g = util.first_match(globs, n)
                if g:
                    cs_hits.append({"path": p, "category": cat, "glob": g})
                    break
    hits = _dedupe(hits, ("path", "glob", "reason"))
    cs_hits = _dedupe(cs_hits, ("path", "category"))
    # always_human items a script can see
    for name in signals.get("env_vars_changed") or []:
        always.append(("always_human", f"env var added or removed: {name}"))
    dep = {c["name"]: c["status"] for c in (checks or [])}.get("dependency-review")
    pkg_changed = any(rv1mod.is_package_file(p) or rv1mod.is_lockfile(p) for p in changed)
    if dep == "fail":
        always.append(("always_human", "dependency-review failed (critical CVE in a dependency)"))
    elif pr is not None and pkg_changed and dep != "pass":
        always.append(("critical_cve_unchecked", "package file or lockfile changed and dependency review is unavailable"))
    if any(h["reason"] == "check_config" for h in hits):
        always.append(("always_human", "gate or agent config change (protected_check_config)"))
    if len(changed) > 100:
        always.append(("always_human", f"more than 100 changed files ({len(changed)})"))
    if pr is not None:
        if pr.get("is_fork") or pr.get("author_association") not in TRUSTED_ASSOCIATIONS:
            always.append(("always_human", f"untrusted or fork author ({pr.get('author_association')}, fork={bool(pr.get('is_fork'))})"))
        for lab in pr.get("labels") or []:
            if str(lab).startswith("agentive-janitor:"):
                always.append(("always_human", f"label {lab}"))
    tags = signals.get("agentive_check_tags") or {}
    if tags.get("added") or tags.get("removed"):
        always.append(("always_human", "agentive-check: tag added or removed"))
    for st, p, _ in statuses:
        if st in "AMR" and CHAIN_STAGE_ARTIFACT.match(p):
            always.append(("chain_approval", f"chain artifact {p} added or modified"))
    for x in rules_problems or []:
        always.append(("always_human", f"review rules invalid, design defaults used: {x}"))
    return {"hits": hits, "confirm_scope_hits": cs_hits}, util.uniq(always)


def _dedupe(items, keys):
    seen, out = set(), []
    for it in items:
        k = tuple(it[x] for x in keys)
        if k not in seen:
            seen.add(k)
            out.append(it)
    return out


def u2_record(cs_hits, profile, body_u2=None, body_present=False, run=None, human_confirmation=None):
    """review-report u2 {run, human_confirmation, action, source}.

    precommit: run from the no-human signals (run argument), human_confirmation as recorded by the commit procedure
    (None = not asked yet -> not_obtained while a hit exists). ci: the PR body agentive-review.u2 block; a missing
    block with a hit means not_obtained (RV2.confirm_scope.human_confirmation)."""
    if profile == "precommit":
        source = "local"
        run = run or "interactive"
        hc = human_confirmation
    else:
        if body_u2 is None:
            source, run, hc = "pr_body_missing", "unknown", None
        else:
            source = "pr_body"
            run = body_u2.get("run") if body_u2.get("run") in ("interactive", "non_interactive", "unknown") else "unknown"
            hc = body_u2.get("human_confirmation")
    if not cs_hits:
        hc = "not_required"
    elif hc != "obtained" or run != "interactive":
        hc = "not_obtained"
    action = "pr_only_tier2" if hc == "not_obtained" else "commit"
    return {"run": run, "human_confirmation": hc, "action": action, "source": source}
