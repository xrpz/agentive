# agentive-unit: lib.agentive
# design: decisions.accepted[SAMPAUDIT], spec.review_pipeline.auto_merge_policy (owner D)
"""SAMPAUDIT: which automated approvals a human reviews first. A pure function of the PR identity and the rates.

draw   sha256 of `<repository id>:<PR number>`, first 8 bytes as a number in [0, 1). One number per PR [design judgment: not per
       head]: a push, a re-run, a label event or the merge queue never changes it, so a PR that is sampled stays sampled and an author
       cannot re-roll it by pushing another commit or rewriting history.
select draw < rate of the PR's tier: rate 0 never selects, rate 1 always selects, tier 1 has the higher default rate (risk weighted;
       the PRs drawn at tier 0 are also drawn at tier 1 because the draw is the same number).
The rates come from the base commit (config.load_sampling), so a PR cannot change the rate it is drawn against. Limit [design
judgment]: the repository id and the PR number are public and the next PR number can be predicted, so someone who burns numbers
(issues share the counter) could open a PR with a number that is not drawn; that person could do more with write access, so this
is a control against honest mistakes of an AI approval, not against an attacker (untrusted authors, trust-boundary paths and
tier 2 never reach it)."""
import hashlib


def seed(repo_id, pr_number):
    return f"{repo_id}:{pr_number}"


def draw(seed_text):
    return int.from_bytes(hashlib.sha256(str(seed_text).encode("utf-8")).digest()[:8], "big") / 2 ** 64


def select(tier, rates, seed_text):
    """{selected, tier, rate, draw} for a tier 0 or 1 PR that would be merged automatically."""
    rate = float((rates or {}).get(f"tier{tier}", 0))
    d = draw(seed_text)
    return {"selected": d < rate, "tier": tier, "rate": rate, "draw": round(d, 6)}
