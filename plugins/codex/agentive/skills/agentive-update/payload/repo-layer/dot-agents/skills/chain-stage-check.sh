#!/bin/sh
# agentive-unit: rl.skill.chain-stage-check
# design: spec.spec_stage.chain.start_check, spec.spec_stage.chain.flow, spec.repo_layer.files[f-chain-skills], spec.no_human_run
# One source installed as .agents/skills/chain-{intent,spec,plan}/scripts/stage-check.sh; the stage comes from
# the directory it is installed in. Usage: sh scripts/stage-check.sh <slug>
# Checks only the previous artifact on the default branch of origin. Exit 0 prints AGENTIVE_CHAIN=ok:...;
# exit 1 prints one line `AGENTIVE_CHAIN=stop:<stage>:<slug> NEXT=<action>` and the agent stops and tells the human.
set -eu

case "$0" in
  */chain-intent/*) stage=intent ;;
  */chain-spec/*) stage=spec ;;
  */chain-plan/*) stage=plan ;;
  *) stage=${AGENTIVE_CHAIN_STAGE:-unknown} ;;
esac
slug=${1:-}

stop() {
  echo "AGENTIVE_CHAIN=stop:$stage:${slug:-none} NEXT=$1"
  exit 1
}

case "$stage" in intent|spec|plan) ;; *) stop "install this script under .agents/skills/chain-<intent|spec|plan>/scripts/" ;; esac
[ -n "$slug" ] || stop "pass the slug: sh scripts/stage-check.sh <slug>"
if [ "$slug" = archive ] || ! printf '%s\n' "$slug" | grep -Eq '^[a-z0-9]+(-[a-z0-9]+)*$'; then
  stop "use a kebab-case slug other than archive (for example add-rate-limit)"
fi

# The plan stage needs a human approving the plan in plan mode (spec_stage.chain.flow[plan].no_human).
if [ "$stage" = plan ]; then
  if [ "${AGENTIVE_HEADLESS:-}" = 1 ] || [ -n "${CI:-}" ] || [ -n "${GITHUB_ACTIONS:-}" ]; then
    stop "plan approval needs a human: run /chain-plan $slug in an interactive session"
  fi
fi

git rev-parse --git-dir >/dev/null 2>&1 || stop "run inside the git repository"

default=$(git symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null || true)
default=${default#origin/}
if [ -z "$default" ]; then
  default=$(git ls-remote --symref origin HEAD 2>/dev/null | sed -n 's|^ref: refs/heads/\([^[:space:]]*\)[[:space:]]*HEAD$|\1|p' | head -n 1)
fi
[ -n "$default" ] || stop "set origin/HEAD (git remote set-head origin --auto)"

if ! git fetch --quiet origin "$default" 2>/dev/null; then
  echo "NOTE: git fetch origin $default failed; checking the local origin/$default instead" >&2
fi
git rev-parse --verify --quiet "refs/remotes/origin/$default" >/dev/null || stop "fetch the default branch (git fetch origin $default)"

has() {
  git cat-file -e "refs/remotes/origin/$default:docs/plans/$slug/$1" 2>/dev/null
}

case "$stage" in
  intent)
    if has intent.md; then
      stop "docs/plans/$slug/intent.md is already on $default: ask the human whether this is the same change; if not, pick a new slug"
    fi ;;
  spec)
    has intent.md || stop "merge the intent PR (intent/$slug) first, then run /chain-spec $slug" ;;
  plan)
    has spec.md || stop "merge the spec PR (spec/$slug) first, then run /chain-plan $slug" ;;
esac

echo "AGENTIVE_CHAIN=ok:$stage:$slug DEFAULT=$default"
