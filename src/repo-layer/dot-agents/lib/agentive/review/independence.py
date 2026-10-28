# agentive-unit: lib.agentive
# design: spec.work_loop.ci_independence_check, spec.work_loop.independence, spec.work_loop.pr_skill.ci_checks_headings (owner D)
"""CI job agentive-independence (deterministic):
  1 reviews has >= 1 entry with context: fresh
  2 every reviews[].session_id != implementer.session_id
  3 the PR body has the three all-tier headings (## Verification, ## Risk, ## How to revert) in this order
The reviews are those of the PR body block plus the CI reviews of the review report of the same head (the ai-review
job writes them into the block afterwards) [설계 판단: the job reads both so a first CI run is not judged on a body
the pipeline has not written yet]. The tier-2 ## Explainer is RV8's check, not this one.
"""
from . import pr_body


def check(body, ci_reviews=None):
    """(ok, problems)."""
    probs = []
    blk, bprob = pr_body.block(body)
    reviews = list((blk or {}).get("reviews") or []) + list(ci_reviews or [])
    if not any(r.get("context") == "fresh" for r in reviews):
        probs.append("no review with context: fresh (" + (bprob or "reviews has no fresh entry") + ")")
    impl = ((blk or {}).get("implementer") or {}).get("session_id")
    if impl:
        same = [r.get("reviewer_id") or r.get("role") for r in reviews if r.get("session_id") == impl]
        if same:
            probs.append(f"review session equals the implementer session ({', '.join(map(str, same))})")
    probs += pr_body.all_tier_headings(body)
    return not probs, probs
