# agentive-unit: lib.agentive
# design: decisions.accepted[SAMPAUDIT], spec.review_pipeline.stages[RV8], spec.review_pipeline.auto_merge_policy (owner D)
"""SAMPAUDIT audit record: every automated approval decision of the gate leaves one machine-readable block in the sticky
agentive-review-summary comment, which the gate (github-actions[bot]) writes and a PR author cannot edit. The label
agentive:auto-approved makes the PRs findable; the block says why. No file in the repo, no event log.

record(report, ...) -> dict or None. None when the gate made no automated decision (a PR for a human anyway). An outcome is
  auto_merged  the gate enabled auto-merge (rv8.auto_merge)
  sampled      the gate would have merged it and the draw sent it to a human first (rv8.sampled.selected)
  shadow       review.auto_merge does not merge this tier: the PR qualified and got the label agentive:shadow-auto-merge
block(rec) / parse(text): the HTML comment `<!-- agentive-audit {json} -->` (one line, no `-->` inside: json.dumps escapes `>` as >).
"""
import json
import re

from . import config as cfgmod

VERSION = 1
MARKER = "agentive-audit"
BLOCK_RE = re.compile(r"<!--\s*" + MARKER + r" (\{.*?\})\s*-->", re.S)


def outcome(rv8):
    if rv8.get("decision") == "pass" and rv8.get("auto_merge"):
        return "auto_merged"
    if (rv8.get("sampled") or {}).get("selected"):
        return "sampled"
    if rv8.get("would_auto_merge"):
        return "shadow"
    return None


def record(report, reviews=None, gate_version=None, run_url=None, now=None):
    rv8 = report.get("rv8") or {}
    out = outcome(rv8)
    if not out:
        return None
    rv2, rv3, rv4, rv7b = (report.get(k) or {} for k in ("rv2", "rv3", "rv4", "rv7b"))
    hits = sorted({h["path"] for h in rv2.get("hits") or []})
    findings = report.get("findings") or []
    rec = {
        "v": VERSION, "outcome": out, "head_sha": rv8.get("head_sha"), "gate_version": gate_version or "unknown",
        "auto_merge_setting": rv8.get("auto_merge_setting"),
        "tier": rv4.get("tier"), "tier_reasons": list(rv4.get("reasons") or []),
        "classes": list(rv3.get("classes") or []), "classification": rv3.get("method"),
        "trust_boundary": {"result": "hit" if hits else "clear", "paths": hits[:20]},
        "rubric": ({k: rv7b[k] for k in ("score", "preset", "human_if_score_gt", "config_sha256") if k in rv7b}
                   | {"items": [{"id": i["id"], "hit": bool(i["hit"]), "weight": i["weight"]} for i in rv7b.get("items") or []]})
                  if rv7b.get("score") is not None else None,
        "checks": {c["name"]: c["status"] for c in (report.get("rv1") or {}).get("checks") or []},
        "reviewers": [{k: r[k] for k in ("role", "reviewer_id", "verdict") if r.get(k)} for r in reviews or []],
        "findings": {"total": len(findings),
                     "important": sum(1 for f in findings if f.get("severity") == "important"),
                     "confirmed": sum(1 for f in findings if (f.get("verification") or {}).get("verdict") == "confirmed")},
        "reasons": list(rv8.get("reason_codes") or []),
    }
    if rv8.get("sampled"):
        rec["sampled"] = dict(rv8["sampled"])
    if rv8.get("kill_switch"):
        rec["kill_switch"] = {k: rv8["kill_switch"][k] for k in ("tier", "source", "count") if k in rv8["kill_switch"]}
    if run_url:
        rec["run_url"] = run_url
    if now:
        rec["decided_at"] = now
    return rec


def block(rec):
    return "<!-- " + MARKER + " " + json.dumps(rec, sort_keys=True, separators=(",", ":")).replace(">", "\\u003e") + " -->"


def parse(text):
    """The audit record of a comment body, or None."""
    ms = BLOCK_RE.findall(text or "")      # the gate writes its block last; PR text that mimics one earlier in the comment is ignored
    if not ms:
        return None
    try:
        got = json.loads(ms[-1])
    except ValueError:
        return None
    return got if isinstance(got, dict) and got.get("v") == VERSION else None


def labels_for(rv8):
    """(labels to have, labels to drop) for the audit and sampling labels of this decision."""
    want = []
    if rv8.get("decision") == "pass" and rv8.get("auto_merge"):
        want.append(cfgmod.APPROVED_LABEL)
    if (rv8.get("sampled") or {}).get("selected"):
        want.append(cfgmod.SAMPLED_LABEL)
    return want, [lb for lb in (cfgmod.APPROVED_LABEL, cfgmod.SAMPLED_LABEL) if lb not in want]
