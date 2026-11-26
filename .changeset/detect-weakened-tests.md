---
"agentive": minor
---

Weakened tests are now found by code. The Stop hook compares the uncommitted and unpushed diff with the merge base and blocks once when it deletes tests, removes test cases or assertions, adds skip, only, xfail or ignore markers, deselects tests in the runner config or lowers a coverage threshold. CI review uses the same detector: such a PR becomes tier 2 with reason test_weakened and the facts listed in the review summary.
