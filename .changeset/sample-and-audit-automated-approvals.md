---
"agentive": minor
---

Automated approvals are now audited and sampled: every automated approval decision gets the label agentive:auto-approved and a machine-readable audit block in the review summary comment (no merge without it), and a risk-weighted share of the PRs the gate would merge by itself (tier 0 5 percent, tier 1 20 percent, review.sampling overrides) goes to a human first with the label agentive:sampled-for-review; a sampled PR labeled agentive:sample-found-bad counts for the kill switch.
