# agentive

## 0.1.1

### Patch Changes

- 82d9ec2: The CI review gate now treats every claude-code-action run that ends successfully without starting Claude as an exempt skip (kind `action_skipped`), even when `.github/workflows/agentive-review.yml` is unchanged: the gate ends human_required instead of failing agentive-independence.
