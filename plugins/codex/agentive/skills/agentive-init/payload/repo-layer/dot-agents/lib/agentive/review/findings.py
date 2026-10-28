# agentive-unit: lib.agentive
# design: spec.review_pipeline.severity_blocking, spec.review_pipeline.severity_blocking.reply_line, spec.review_pipeline.convergence, spec.review_pipeline.stages[RV7], spec.review_pipeline.stages[RV6] (owner D)
"""Findings: evidence filter, stable ids, RV7 verdicts, RV6 coordination, agentive-reply lines, convergence.

Finding ids are stable across rounds [설계 판단]: a reviewer that re-reports an open finding names it with
`same_as: <id>`; otherwise id = F-<sha256(file, bug class or trigger)[:10]>. Every posted finding comment carries
`<!-- agentive-finding: <id> -->` (design) and `<!-- agentive-finding-data: {json} -->` (severity, bug class,
trigger, verdict, round) so the next round can rebuild the open findings from the PR alone [설계 판단].
"""
import json
import re

from . import config as cfgmod
from . import util

SEVERITIES = ("important", "nit", "pre_existing")
REPLY_RE = re.compile(r"^agentive-reply: (\S+) (fixed|wrong|intentional|not_important)(?: (.+))?$")
SENTENCE_REQUIRED = ("wrong", "intentional", "not_important")
MARKER_RE = re.compile(r"<!--\s*agentive-finding:\s*(\S+)\s*-->")
DATA_RE = re.compile(r"<!--\s*agentive-finding-data:\s*(\{.*?\})\s*-->", re.S)
TRUSTED = ("OWNER", "MEMBER", "COLLABORATOR")
DISPUTED = ("wrong", "intentional", "not_important")


def _norm(s):
    return re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).strip()


def clean_reviewer_output(out, reviewer_id, shadow=False, known_ids=()):
    """(findings, open_status, problem). Drops findings without file:line and trigger (severity_blocking.evidence)."""
    if not isinstance(out, dict) or not isinstance(out.get("findings"), list):
        return [], {}, f"{reviewer_id}: output is not an object with findings[]"
    res, dropped = [], 0
    for f in out["findings"]:
        if not isinstance(f, dict):
            dropped += 1
            continue
        file, line, trig = f.get("file"), f.get("line"), f.get("trigger")
        if not (isinstance(file, str) and file and isinstance(line, int) and not isinstance(line, bool) and line >= 1
                and isinstance(trig, str) and trig.strip()):
            dropped += 1
            continue
        sev = f.get("severity") if f.get("severity") in SEVERITIES else "important"
        fid = f.get("same_as") if f.get("same_as") in known_ids else util.stable_id(file, _norm(f.get("bug_class") or trig))
        item = {"id": fid, "reviewers": [reviewer_id], "severity": sev, "blocking": False, "file": file, "line": line,
                "trigger": trig.strip(), "summary": str(f.get("summary") or trig).strip()}
        if f.get("evidence"):
            item["evidence"] = str(f["evidence"])
        if f.get("bug_class"):
            item["bug_class"] = str(f["bug_class"])
        inst = [i for i in (f.get("instances") or []) if isinstance(i, dict) and isinstance(i.get("file"), str)
                and isinstance(i.get("line"), int) and not isinstance(i.get("line"), bool) and i["line"] >= 1]
        if inst:
            item["instances"] = [{"file": i["file"], "line": i["line"]} for i in inst]
        if shadow:
            item["shadow"] = True
        res.append(item)
    status = {}
    for o in out.get("open_findings") or []:
        if isinstance(o, dict) and o.get("id") in known_ids and o.get("status") in ("still_present", "fixed"):
            status[o["id"]] = o["status"]
    return merge_same_ids(res), status, (f"{reviewer_id}: dropped {dropped} finding(s) without file:line and trigger" if dropped else None)


def merge_same_ids(findings):
    by, order = {}, []
    for f in findings:
        if f["id"] in by:
            g = by[f["id"]]
            g["reviewers"] = util.uniq(g["reviewers"] + f["reviewers"])
            g.setdefault("instances", [])
            for i in [{"file": f["file"], "line": f["line"]}] + f.get("instances", []):
                if i not in g["instances"] and (i["file"], i["line"]) != (g["file"], g["line"]):
                    g["instances"].append(i)
            if not g["instances"]:
                del g["instances"]
            if SEVERITIES.index(f["severity"]) < SEVERITIES.index(g["severity"]):
                g["severity"] = f["severity"]
            if not f.get("shadow"):
                g.pop("shadow", None)
        else:
            by[f["id"]] = dict(f)
            order.append(f["id"])
    return [by[i] for i in order]


def apply_verdicts(findings, out, session_id):
    """RV7: set verification on every finding; a missing or invalid verdict is inconclusive."""
    got = {}
    if isinstance(out, dict):
        for v in out.get("verdicts") or []:
            if isinstance(v, dict) and v.get("verdict") in ("confirmed", "refuted", "inconclusive"):
                got[v.get("id")] = v
    for f in findings:
        v = got.get(f["id"])
        ver = {"verdict": v["verdict"] if v else "inconclusive", "verifier_session_id": session_id}
        if v and v.get("method") in ("execution", "adversarial_fresh_context"):
            ver["method"] = v["method"]
        else:
            ver["method"] = "adversarial_fresh_context"
        note = (v or {}).get("note") or ("" if v else "no verdict in the verifier output")
        if note:
            ver["note"] = str(note)
        f["verification"] = ver
    return findings


def coordinate(findings, out):
    """RV6: (findings, problem). The coordinator may merge (merged_from), regroup by bug class and set severity;
    every input id must end up in exactly one output finding, else the input is kept unchanged."""
    if not isinstance(out, dict) or not isinstance(out.get("findings"), list):
        return findings, "coordinator: output is not an object with findings[]"
    by = {f["id"]: f for f in findings}
    used, res = set(), []
    for o in out["findings"]:
        if not isinstance(o, dict) or o.get("id") not in by:
            return findings, f"coordinator: unknown finding {o!r}"
        ids = [o["id"]] + [x for x in (o.get("merged_from") or []) if x != o["id"]]
        if any(x not in by or x in used for x in ids):
            return findings, f"coordinator: finding ids {ids} unknown or used twice"
        used.update(ids)
        base = dict(by[o["id"]])
        if o.get("severity") in SEVERITIES:
            base["severity"] = o["severity"]
        if o.get("bug_class"):
            base["bug_class"] = str(o["bug_class"])
        others = [by[x] for x in ids[1:]]
        if others:
            base["merged_from"] = ids[1:]
            base["reviewers"] = util.uniq(base["reviewers"] + [r for x in others for r in x["reviewers"]])
            inst = list(base.get("instances") or [])
            for x in others:
                for i in [{"file": x["file"], "line": x["line"]}] + list(x.get("instances") or []):
                    if i not in inst and (i["file"], i["line"]) != (base["file"], base["line"]):
                        inst.append(i)
            if inst:
                base["instances"] = inst
            rank = {"confirmed": 0, "inconclusive": 1, "refuted": 2}
            best = min([base] + others, key=lambda x: rank[(x.get("verification") or {}).get("verdict", "inconclusive")])
            if best.get("verification"):
                base["verification"] = dict(best["verification"])
            if any(not x.get("shadow") for x in [base] + others):
                base.pop("shadow", None)
        res.append(base)
    if used != set(by):
        return findings, f"coordinator: findings {sorted(set(by) - used)} missing from the output"
    return res, None


def set_blocking(findings, profile):
    for f in findings:
        if profile == "precommit":
            f["blocking"] = f["severity"] == "important" and not f.get("shadow")
        else:
            f["blocking"] = (f["severity"] == "important" and not f.get("shadow")
                             and (f.get("verification") or {}).get("verdict") == "confirmed"
                             and (f.get("response") or {}).get("source") != "next_round")
    return findings


# ---------------------------------------------------------------- posted findings and reply lines
def parse_reply(body):
    """(finding_id, reason, sentence) of the first valid agentive-reply line in a comment body, or None."""
    for ln in (body or "").splitlines():
        m = REPLY_RE.match(ln.strip())
        if not m:
            continue
        fid, reason, sentence = m.group(1), m.group(2), (m.group(3) or "").strip()
        if reason in SENTENCE_REQUIRED and not sentence:
            continue
        return fid, reason, sentence
    return None


def posted_findings(comments):
    """{finding_id: {comment_id, path, line, data, replies: [(comment_id, reason, sentence, author)]}} from the
    REST pulls/<n>/comments list (id, in_reply_to_id, body, path, line, author_association, user.login)."""
    roots, out = {}, {}
    for c in comments or []:
        m = MARKER_RE.search(c.get("body") or "")
        if m and not c.get("in_reply_to_id"):
            data = {}
            dm = DATA_RE.search(c.get("body") or "")
            if dm:
                try:
                    data = json.loads(dm.group(1))
                except ValueError:
                    data = {}
            roots[c["id"]] = m.group(1)
            out[m.group(1)] = {"comment_id": c["id"], "path": c.get("path"), "line": c.get("line") or c.get("original_line"),
                               "data": data, "replies": []}
    for c in sorted(comments or [], key=lambda x: (x.get("created_at") or "", x.get("id") or 0)):
        root = c.get("in_reply_to_id")
        if not root or root not in roots:
            continue
        if c.get("author_association") not in TRUSTED:
            continue
        r = parse_reply(c.get("body"))
        if r is None or r[0] != roots[root]:
            continue                      # a reply line whose id does not match its thread's marker is ignored
        out[roots[root]]["replies"].append((c["id"], r[1], r[2], (c.get("user") or {}).get("login")))
    return out


def attach_responses(findings, posted):
    """finding.response from the latest valid reply line in its thread (source reply_line)."""
    for f in findings:
        p = posted.get(f["id"])
        if not p or not p["replies"]:
            continue
        cid, reason, _, _ = p["replies"][-1]
        kind = "open" if reason == "fixed" else "replied_resolved"
        f["response"] = {"kind": kind, "reason": reason, "source": "reply_line", "comment_id": int(cid)}
    return findings


def carried_findings(posted, current_ids, open_status, mode, round_no, reviewers_ran=True):
    """Findings posted in an earlier round and not reported again in this round.
    full mode: not reproduced -> fixed (source next_round). incremental mode: a reviewer said fixed -> fixed; otherwise
    the finding stays open with its earlier severity and verdict (the new diff may not show it). Without any reviewer
    output in this round (reviewers_ran false) nothing is marked fixed."""
    out = []
    for fid, p in posted.items():
        if fid in current_ids:
            continue
        d = p.get("data") or {}
        if not p.get("path") or not p.get("line"):
            continue
        f = {"id": fid, "reviewers": [r for r in (d.get("reviewers") or []) if r in cfgmod.REVIEWERS] or ["correctness-reviewer"],
             "severity": d.get("severity") if d.get("severity") in SEVERITIES else "important", "blocking": False,
             "file": p["path"], "line": int(p["line"]), "trigger": d.get("trigger") or "posted in an earlier round",
             "summary": d.get("summary") or d.get("trigger") or "posted in an earlier round",
             "verification": {"verdict": d.get("verdict") if d.get("verdict") in ("confirmed", "refuted", "inconclusive") else "inconclusive",
                              "verifier_session_id": d.get("verifier_session_id") or "earlier-round"}}
        if d.get("bug_class"):
            f["bug_class"] = d["bug_class"]
        if isinstance(d.get("round"), int) and d["round"] >= 1:
            f["first_posted_round"] = d["round"]
        fixed = reviewers_ran and ((mode == "full" and open_status.get(fid) != "still_present") or open_status.get(fid) == "fixed")
        if fixed:
            f["response"] = {"kind": "fixed", "reason": "fixed", "source": "next_round"}
        out.append(f)
    return out


def finding_data(f, round_no):
    d = {"severity": f["severity"], "trigger": f["trigger"], "summary": f["summary"][:300],
         "reviewers": f["reviewers"], "round": f.get("first_posted_round", round_no)}
    if f.get("bug_class"):
        d["bug_class"] = f["bug_class"]
    v = f.get("verification") or {}
    if v:
        d["verdict"] = v.get("verdict")
        d["verifier_session_id"] = v.get("verifier_session_id")
    return d


def to_post(findings, posted_ids):
    """Inline comments to create: important, not refuted, not shadow-hidden, not posted before (convergence)."""
    res = []
    for f in findings:
        if f["id"] in posted_ids or f["severity"] != "important":
            continue
        if (f.get("verification") or {}).get("verdict") == "refuted":
            continue
        if (f.get("response") or {}).get("source") == "next_round":
            continue
        res.append(f)
    return res
