## agentive review rules (generated)

The block below is machine data for the review pipeline: RV2 trust boundary and U2 confirm scope, RV3 classification, RV4 tiers. Scripts read it from the base commit, never from the PR branch. To change it, put a fenced `yaml agentive-review-rules` block with only your changes in `.agents/custom/code_review.md`; it is merged into this block.

```yaml agentive-review-rules
agentive-review-rules:
  trust_boundary:
    never_ai_approve:
      - {glob: "**/auth/**", reason: auth}
      - {glob: "**/security/**", reason: security}
      - {glob: "**/permissions*", reason: permissions}
      - {glob: "**/*secret*", reason: secrets}
      - {glob: "**/.env*", reason: secrets}
      - {glob: "**/migrations/**", reason: db_migration}
      - {glob: ".github/workflows/**", reason: ci_config}
      - {glob: ".claude/settings*.json", reason: agent_hooks_settings}
      - {glob: "**/hooks.json", reason: agent_hooks_settings}
      - {glob: ".codex/**", reason: agent_hooks_settings}
      - {glob: "**/billing/**", reason: billing}
      - {glob: "**/payment*/**", reason: billing}
    confirm_scope_globs:
      hooks: ["**/hooks.json", "plugins/**/hooks/**", ".agents/hooks/**"]
      permissions: ["**/permissions*", ".agents/guard.yaml", ".agents/custom/.agents/guard.yaml"]
      ci: [".github/workflows/**"]
      claude_settings: [".claude/settings*.json"]
      codex_dir: [".codex/**"]
      review_config: [".agents/framework.yaml", ".agents/decisions.yaml", ".agents/custom/.agents/rubric.yaml"]
  classify:
    whitespace_only_is_format: true
  tiers:
    tier0_classes: [docs, format]
    tier0_max_changed_lines: 200
    large_change_lines: 1000
    confirm_scope_without_human_tier: 2
    tier2_classes: [security]
```

Always check:
- A descriptive doc (paths, commands, counts, behavior) that this change makes wrong and does not update in the same change.
- Code that violates an accepted ADR, rule or spec: report it for a human to decide; neither side is changed in this PR.
- Do not relitigate decisions tagged [user]; review decisions tagged [inferred].
