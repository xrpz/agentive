#!/bin/bash
# Workspace of every case (run by `claude plugin eval --scaffold` in the empty workspace; tools/plugin_eval.py fills @@PLUGIN@@ with
# the throw-away eval plugin directory). Installs the repo layer with the engine of the same plugin: fixture py + a local bare origin,
# then `init`, one commit. Argument: plain | typo | branch.
set -eu
PLUGIN="@@PLUGIN@@"
MODE="${1:-plain}"
cp -R "$PLUGIN/evals/_fixture/py/." .
git init -q -b main
git config user.name eval
git config user.email eval@example.com
git add -A
git commit -q -m "fixture"
ORIGIN="$(mktemp -d "${HOME:-/tmp}/origin.XXXXXX")/origin.git"   # outside the workspace: the run has its own HOME
git init -q --bare -b main "$ORIGIN"
git remote add origin "$ORIGIN"
git push -q -u origin main
git remote set-head origin main
python3 "$PLUGIN/payload/engine/apply.py" init --repo . --no-managed-review-copy > /dev/null
git add -A
git commit -q -m "install agentive"
git push -q origin main
rm -f .agents/state/verify.json        # nothing the Stop hook writes may exist before the agent works
case "$MODE" in
  typo)
    printf '\nWe recieve the data from the upstream service.\n' >> README.md
    git commit -q -am "docs: note the data source"
    ;;
  branch)
    git switch -q -c feat/slugify-docs --no-track origin/main
    printf '\nUse `slugify(text)` to build URL slugs.\n' >> README.md
    git commit -q -am "docs: describe slugify"
    ;;
esac
