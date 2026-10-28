# agentive-unit: lib.agentive
# design: spec.review_pipeline.severity_blocking (nit, answer, reply_line.finding_marker), spec.review_pipeline.convergence (owner D)
"""PR comment text. Important findings are posted inline (they carry file:line) with the finding marker; nits and
pre_existing findings go only to review-report.json and a collapsed section of the summary comment (never inline,
never sent to the author agent as fix requests, F1e). Refuted findings are recorded, not posted.
"""
import json

from . import findings as fmod

SUMMARY_MARKER = "<!-- agentive-review-summary -->"


def finding_comment(f, round_no):
    v = f.get("verification") or {}
    lines = [f"**[{f['severity']}] {f['summary']}**" + (f" ({f['bug_class']})" if f.get("bug_class") else ""), "",
             f"- Trigger: {f['trigger']}"]
    if f.get("evidence"):
        lines.append(f"- Evidence: {f['evidence']}")
    if f.get("instances"):
        lines.append("- Same bug class at: " + ", ".join(f"`{i['file']}:{i['line']}`" for i in f["instances"]))
    lines.append(f"- Reviewers: {', '.join(f['reviewers'])}; verifier: {v.get('verdict', 'not run')}"
                 + (f" ({v['method']})" if v.get("method") else ""))
    if v.get("verdict") == "inconclusive":
        lines.append("- Inconclusive: not blocking by itself; a human decides (RV8 human_required).")
    lines += ["", "Answer in this thread: push a fix, or reply with one line "
              f"`agentive-reply: {f['id']} <fixed|wrong|intentional|not_important> <one sentence>` "
              "(a sentence is required for wrong, intentional and not_important).",
              "", f"<!-- agentive-finding: {f['id']} -->",
              f"<!-- agentive-finding-data: {json.dumps(fmod.finding_data(f, round_no), ensure_ascii=False, sort_keys=True)} -->"]
    return "\n".join(lines)


def summary_comment(report, posted_now, extras):
    rv3, rv4, rv7b = report.get("rv3") or {}, report.get("rv4") or {}, report.get("rv7b") or {}
    rnd = report.get("review_round") or {}
    fnd = report.get("findings") or []
    imp = [f for f in fnd if f["severity"] == "important" and (f.get("verification") or {}).get("verdict") != "refuted"]
    minor = [f for f in fnd if f["severity"] in ("nit", "pre_existing") and (f.get("verification") or {}).get("verdict") != "refuted"]
    refuted = [f for f in fnd if (f.get("verification") or {}).get("verdict") == "refuted"]
    out = [SUMMARY_MARKER, f"### agentive review (round {rnd.get('round', 1)}, {rnd.get('mode', 'full')})", "",
           f"- head: `{report['head_sha'][:12]}` · classes: {', '.join(rv3.get('classes') or [])} ({rv3.get('method', '-')})"
           f" · tier: {rv4.get('tier', '-')} ({', '.join(rv4.get('reasons') or [])})",
           f"- risk score: {rv7b.get('score') if rv7b.get('score') is not None else '-'} ({rv7b.get('reason', '-')})",
           f"- important findings: {len(imp)} (blocking {sum(1 for f in imp if f['blocking'])}, posted inline this round "
           f"{posted_now}); refuted by the verifier: {len(refuted)}",
           "- the merge decision is the required check `agentive-review-gate`"]
    if imp:
        out += ["", "| id | file:line | verdict | answer |", "|---|---|---|---|"]
        for f in imp:
            r = f.get("response") or {}
            out.append(f"| {f['id']} | `{f['file']}:{f['line']}` | {(f.get('verification') or {}).get('verdict', '-')} | "
                       f"{r.get('reason', 'open')}{' (' + r['source'] + ')' if r.get('source') else ''} |")
    if minor:
        out += ["", f"<details><summary>{len(minor)} nit / pre-existing note(s) (not blocking, no action needed)</summary>", ""]
        for f in minor:
            out.append(f"- [{f['severity']}] `{f['file']}:{f['line']}` {f['summary']}")
        out += ["", "</details>"]
    for p in (extras or {}).get("llm_problems") or []:
        out.append(f"- note: {p}")
    return "\n".join(out) + "\n"
