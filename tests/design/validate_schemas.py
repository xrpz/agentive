# agentive-unit: selftest.schema_validation
# design: spec.framework_repo.self_tests (schema_validation)
# Self-test id schema_validation. Ported unchanged from the design workspace validate.py (design state C-067, 2026-10-27).
# Usage: python3 -I tests/design/validate_schemas.py design/schemas   (tools/selftest.py runs it)
# Every schema must be meta-valid; every sample instance must be accepted or rejected as labelled; a few
# consistency checks that JSON Schema cannot express are run as plain Python (marked PASS/FAIL). The last
# section re-checks design/framework.yaml for regressions of applied decisions: when a design change is
# applied on purpose, update the matching cond(...) line in the same change.
# context/rules-10.md is not in this repo, so the two rules-10 checks pass vacuously (RULES10 == '').
import json, glob, os, sys, copy, posixpath
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

D = sys.argv[1]
reg = Registry(); S = {}
for p in sorted(glob.glob(os.path.join(D, '*.json'))):
    s = json.load(open(p)); Draft202012Validator.check_schema(s)
    S[os.path.basename(p)] = s
    reg = reg.with_resource(s['$id'], Resource.from_contents(s))
print('meta-valid schemas:', len(S))
RESULTS = []
def v(name, inst):
    return [e.message for e in Draft202012Validator(S[name], registry=reg).iter_errors(inst)]
def chk(label, name, inst, expect_valid):
    e = v(name, inst); good = (not e) if expect_valid else bool(e)
    print(('VALID ' if not e else 'REJECT') + (' ok ' if good else ' !! '), label, '' if good else e[:3]); RESULTS.append(good)
def cond(label, c):
    print(('PASS  ok ' if c else 'FAIL  !! ') + label); RESULTS.append(bool(c))
def section(t): print('---', t, '---')
def vref(ref, inst): return [e.message for e in Draft202012Validator({"$ref": ref}, registry=reg).iter_errors(inst)]
def chkref(label, ref, inst, expect_valid):
    e = vref(ref, inst); good = (not e) if expect_valid else bool(e)
    print(('VALID ' if not e else 'REJECT') + (' ok ' if good else ' !! '), label, '' if good else e[:3]); RESULTS.append(good)
SHA = 'a' * 64; H = 'abc1234'

# ================= files that must exist / must be gone =================
section('schema set')
for gone in ['metrics-event.schema.json', 'framework-config.schema.json', 'migration-spec.schema.json', 'intent.schema.json', 'pr-body-fw-review.schema.json', 'golden-task.schema.json']:
    cond(f'retired schema absent: {gone}', gone not in S)
for need in ['guard-rules', 'rubric', 'handoff', 'pending-confirmation', 'project-config', 'framework-repo-config', 'review-config', 'review-report', 'review-rules',
             'precommit-review', 'pr-body-agentive-review', 'decisions', 'manifest', 'verify-result', 'stop-state', 'harvest-queue', 'reviewer-eval-case', 'drift-report',
             'probe-result', 'version-compat', 'skills-lock', 'dirty-state']:
    cond(f'schema present: {need}', need + '.schema.json' in S)

# ================= review-rules =================
section('review-rules')
CATS = ["hooks", "permissions", "ci", "claude_settings", "codex_dir", "review_config"]
rules = {"agentive-review-rules": {"trust_boundary": {
    "confirm_scope_globs": {"hooks": ["**/hooks.json", "plugins/**/hooks/**"], "permissions": ["**/permissions*"], "ci": [".github/workflows/**"],
                            "claude_settings": [".claude/settings*.json"], "codex_dir": [".codex/**"], "review_config": [".agents/framework.yaml", ".agents/decisions.yaml", ".agents/custom/.agents/rubric.yaml"]},
    "never_ai_approve": [{"glob": "**/auth/**", "reason": "auth"}, {"glob": "**/migrations/**", "reason": "db_migration"}, {"glob": ".github/workflows/**", "reason": "ci_config"}]},
  "classify": {"path_rules": [{"glob": "**/*.md", "class": "docs"}, {"glob": "**/package-lock.json", "class": "lockfile"}]},
  "tiers": {"tier0_classes": ["docs", "format"], "tier0_max_changed_lines": 200, "large_change_lines": 1000, "tier2_classes": ["security"], "confirm_scope_without_human_tier": 2}}}
chk("review-rules: defaults incl. review_config category", "review-rules.schema.json", rules, True)
x = copy.deepcopy(rules); x['agentive-review-rules']['trust_boundary']['confirm_scope_globs'].pop('review_config')
chk("review-rules: review_config category missing", "review-rules.schema.json", x, False)
x = copy.deepcopy(rules); x['agentive-review-rules']['trust_boundary']['confirm_scope_globs']['ci'] = []
chk("review-rules: a category emptied", "review-rules.schema.json", x, False)
x = copy.deepcopy(rules); x['agentive-review-rules']['tiers']['tier0_classes'] = ["docs", "format", "lockfile", "generated"]
chk("review-rules: project adds lockfile and generated to tier 0", "review-rules.schema.json", x, True)
x = copy.deepcopy(rules); x['agentive-review-rules']['tiers']['tier0_classes'] = ["docs", "feature"]
chk("review-rules: feature as a tier-0 class rejected", "review-rules.schema.json", x, False)
x = copy.deepcopy(rules); x['agentive-review-rules']['tiers']['confirm_scope_without_human_tier'] = 1
chk("review-rules: forced tier lowered", "review-rules.schema.json", x, False)
x = copy.deepcopy(rules); x['agentive-review-rules']['trust_boundary']['never_ai_approve'] = []
chk("review-rules: empty boundary", "review-rules.schema.json", x, False)
x = copy.deepcopy(rules); x['agentive-review-rules']['trust_boundary']['never_ai_approve'] += [{"glob": "**/billing/**", "reason": "billing"}, {"glob": "**/payment*/**", "reason": "billing"}]
chk("review-rules: billing and payment default globs (reason billing, B13)", "review-rules.schema.json", x, True)
x = copy.deepcopy(rules); x['agentive-review-rules']['trust_boundary']['never_ai_approve'].append({"glob": "tools/lint/**", "reason": "check_config"})
chk("review-rules: check_config reason", "review-rules.schema.json", x, True)
chk("review-rules: old block name fw-review-rules", "review-rules.schema.json", {"fw-review-rules": rules["agentive-review-rules"]}, False)

# ================= review-config / project-config =================
section('review-config / project-config')
cfg = {"stages": {"RV1": {"require_checks": ["lint", "test"]}, "RV3": {"llm_layer": True}, "RV5": {"reviewers": ["correctness-reviewer", "security-reviewer", "testing-reviewer"], "shadow_reviewers": []}, "RV8": {"check_name": "agentive-review-gate"}},
       "profiles": {"precommit": {"reviewers": ["correctness-reviewer"]}}, "rules_file": "code_review.md", "auto_merge": {"tier1": "shadow"}}
chk("review-config: defaults", "review-config.schema.json", cfg, True)
chk("review-config: empty object", "review-config.schema.json", {}, True)
for k, val in [("RV1", {"enabled": False}), ("RV2", {"enabled": False}), ("RV6", {"enabled": False}), ("RV7", {"enabled": False}), ("RV7b", {"enabled": True}), ("RV8", {"enabled": False})]:
    x = copy.deepcopy(cfg); x['stages'][k] = val
    chk(f"review-config: stage toggle {k} removed (D3)", "review-config.schema.json", x, False)
x = copy.deepcopy(cfg); x['stages']['RV5']['reviewers'] = ["correctness-reviewer", "testing-reviewer"]
chk("review-config: security-reviewer removed", "review-config.schema.json", x, False)
x = copy.deepcopy(cfg); x['stages']['RV5']['reviewers'] = ["correctness-reviewer", "security-reviewer", "performance"]
chk("review-config: performance perspective (removed)", "review-config.schema.json", x, False)
x = copy.deepcopy(cfg); x['stages']['RV5']['shadow_reviewers'] = ["testing-reviewer"]
chk("review-config: testing-reviewer in shadow", "review-config.schema.json", x, True)
x = copy.deepcopy(cfg); x['stages']['RV5']['context'] = 'same'
chk("review-config: RV5 same context", "review-config.schema.json", x, False)
for m in ["on", "off"]:
    x = copy.deepcopy(cfg); x['auto_merge']['tier1'] = m
    chk(f"review-config: tier1 {m}", "review-config.schema.json", x, True)
x = copy.deepcopy(cfg); x['auto_merge']['tier1'] = 'rubric'
chk("review-config: tier1 rubric (old key value)", "review-config.schema.json", x, False)
x = copy.deepcopy(cfg); x['auto_merge']['sampling'] = {"tier1_rate": 0.1}
chk("review-config: sampling (U1.b removed)", "review-config.schema.json", x, False)
x = copy.deepcopy(cfg); x['auto_merge']['kill_switch'] = {"count": 3}
chk("review-config: kill switch count changed (constant 2)", "review-config.schema.json", x, False)
x = copy.deepcopy(cfg); x['auto_merge']['kill_switch'] = {"count": 2, "window_days": 7, "scope": "tier_and_source"}
chk("review-config: kill switch constants restated", "review-config.schema.json", x, True)
x = copy.deepcopy(cfg); x['risk_score'] = {"rubric_version": "A-1", "items": []}
chk("review-config: risk_score key (moved to rubric)", "review-config.schema.json", x, False)
x = copy.deepcopy(cfg); x['profiles']['precommit']['stages'] = ["RV1", "RV2", "RV3", "RV4", "RV5"]
chk("review-config: precommit with RV3/RV4 (D3 N1-f)", "review-config.schema.json", x, False)
x = copy.deepcopy(cfg); x['profiles']['ci'] = {"stages": ["RV1", "RV2", "RV3", "RV4", "RV5", "RV6", "RV7", "RV7b", "RV8"]}
chk("review-config: old ci order", "review-config.schema.json", x, False)
x = copy.deepcopy(cfg); x['stages']['RV9'] = {}
chk("review-config: unknown stage", "review-config.schema.json", x, False)
crit = {"scope": "ancestor_directory", "one_kind_per_file": True, "no_applicable_file": "normal", "fallback_files": ["CLAUDE.md", "AGENTS.md"]}
chk("review-config: criteria fixed rules", "review-config.schema.json", {**cfg, "criteria": crit}, True)
for bad in [{"fallback_files": []}, {"fallback_files": ["REVIEW.md"]}, {"scope": "repo_wide"}, {"one_kind_per_file": False}, {"no_applicable_file": "error"}]:
    chk(f"review-config: criteria {list(bad)[0]} changed", "review-config.schema.json", {**cfg, "criteria": bad}, False)
pc = {"review": {"managed_code_review_copy": False, "pipeline": cfg}}
chk("project-config: review copy + pipeline", "project-config.schema.json", pc, True)
for k, val in [("source_format", "single_source_generator"), ("manifest", {"format": "yaml"}), ("tie_break", "official_default"), ("conflict", {"strategy": "backup_overwrite"}),
               ("proof", {"mode": "pass_only"}), ("ci_review", {"tool": "both"}), ("evals", {"per_project": {"min": 5}}), ("harvest", {"thresholds": {"stop_blocks": 3}}), ("intent", {}), ("docs_watch", {})]:
    chk(f"project-config: knob {k} is a constant now", "project-config.schema.json", {k: val}, False)
chk("project-config: review.tiers is a constant now", "project-config.schema.json", {"review": {"tiers": 3}}, False)
chk("project-config: managed copy as string", "project-config.schema.json", {"review": {"managed_code_review_copy": "yes"}}, False)

# ================= framework-repo-config =================
section('framework-repo-config')
U3B = {"value": "B", "decided_by": "user", "decided_at": "2026-10-14T00:00:00+09:00", "confirm_scope": ["hooks", "permissions", "sandbox", "security", "release"], "self_tests": ["generate_check", "schema_validation", "sample_repo_tests"]}
U4C = {"value": "C", "decided_by": "user", "decided_at": "2026-10-14T00:00:00+09:00", "criteria_file": "code_review.md", "referenced_from": "AGENTS.md", "managed_code_review_copy_default": False}
frc = {"plugin": {"name": "agentive"}, "decisions": {"U3": U3B, "U4": U4C}}
chk("framework-repo-config: plugin + U3=B + U4=C", "framework-repo-config.schema.json", frc, True)
x = copy.deepcopy(U3B); x.pop('confirm_scope'); chk("framework-repo-config: U3=B without confirm_scope", "framework-repo-config.schema.json", {"decisions": {"U3": x}}, False)
x = copy.deepcopy(U3B); x['self_tests'] = ["generate_check"]; chk("framework-repo-config: U3 partial self-tests", "framework-repo-config.schema.json", {"decisions": {"U3": x}}, False)
x = copy.deepcopy(U4C); x['managed_code_review_copy_default'] = True; chk("framework-repo-config: U4 copy on by default", "framework-repo-config.schema.json", {"decisions": {"U4": x}}, False)
x = copy.deepcopy(U4C); x['criteria_file'] = "REVIEW.md"; chk("framework-repo-config: U4 criteria REVIEW.md", "framework-repo-config.schema.json", {"decisions": {"U4": x}}, False)
chk("framework-repo-config: U5 rejected (obsolete)", "framework-repo-config.schema.json", {"decisions": {"U5": {"value": "A", "decided_by": "user"}}}, False)
chk("framework-repo-config: versioning knob is a constant", "framework-repo-config.schema.json", {"versioning": {"mode": "changesets"}}, False)
chk("framework-repo-config: selftest knob is a constant", "framework-repo-config.schema.json", {"selftest": {"sample_repos": ["ts"]}}, False)
chk("framework-repo-config: codex.distribution is a constant", "framework-repo-config.schema.json", {"codex": {"distribution": "repo_layer_only"}}, False)

# ================= decisions =================
section('decisions')
dec = {"U1": {"value": "C", "decided_by": "user", "decided_at": "2026-10-14T00:00:00+09:00", "conditions": ["U1.a", "U1.c", "U1.d"]},
       "U2": {"value": "B", "decided_by": "user", "decided_at": "2026-10-14T00:00:00+09:00", "confirm_scope": CATS, "non_interactive": "pr_only_tier2"},
       "U6": {"value": "agentive:rubric@1", "decided_by": "user", "decided_at": "2026-10-15T00:00:00+09:00"}}
chk("decisions: init output U1=C(a,c,d), U2=B(6 categories), U6=rubric@1", "decisions.schema.json", dec, True)
x = copy.deepcopy(dec); x['U1']['conditions'] = ["U1.a", "U1.b", "U1.c"]; chk("decisions: U1 with removed condition b", "decisions.schema.json", x, False)
x = copy.deepcopy(dec); x['U1'].pop('conditions'); chk("decisions: U1=C without conditions", "decisions.schema.json", x, False)
x = copy.deepcopy(dec); x['U2']['confirm_scope'] = CATS[:5]; chk("decisions: U2=B without review_config", "decisions.schema.json", x, False)
x = copy.deepcopy(dec); x['U2'].pop('non_interactive'); chk("decisions: U2=B without non_interactive", "decisions.schema.json", x, False)
x = copy.deepcopy(dec); x['U2'] = {"value": "A", "decided_by": "user"}; chk("decisions: U2=A override", "decisions.schema.json", x, True)
x = copy.deepcopy(dec); x['U6'] = {"value": None, "decided_by": None}; chk("decisions: U6 null (no longer allowed)", "decisions.schema.json", x, False)
x = copy.deepcopy(dec); x['U6'] = {"value": "A", "decided_by": "user"}; chk("decisions: U6 preset A (options removed)", "decisions.schema.json", x, False)
x = copy.deepcopy(dec); x['U6']['non_interactive'] = "pr_only_tier2"; chk("decisions: non_interactive on U6", "decisions.schema.json", x, False)
x = copy.deepcopy(dec); x['U1']['non_interactive'] = "pr_only_tier2"; chk("decisions: non_interactive on U1", "decisions.schema.json", x, False)
x = copy.deepcopy(dec); x.pop('U6'); chk("decisions: missing U6", "decisions.schema.json", x, False)
chk("decisions: U3 is not a project decision", "decisions.schema.json", {**dec, "U3": U3B}, False)

# ================= rubric =================
section('rubric')
preset = {"preset": "agentive:rubric@1", "scoring": "binary", "human_if_score_gt": 28, "shared_globs": ["**/shared/**", "**/common/**"], "items": [
    {"id": "spread", "hit": ">=2 modules or a shared path", "input": "modules_touched_or_shared_path", "weight": 24},
    {"id": "size", "hit": ">400 changed lines", "input": "changed_lines", "weight": 24},
    {"id": "incident_history", "hit": "revert or hotfix on touched files within 90 days", "input": "incident_history_90d", "weight": 24},
    {"id": "change_type", "hit": "feature | fix | dependency", "input": "change_class", "weight": 14},
    {"id": "recent_churn", "hit": "touched files changed within 30 days", "input": "recent_churn_30d", "weight": 14}]}
chk("rubric: preset agentive:rubric@1", "rubric.schema.json", preset, True)
def score(p, hits):
    return sum(i['weight'] for i in p['items'] if i.get('enabled', True) and hits.get(i['id']))
cond("rubric: any single item auto-merges (<=28)", all(score(preset, {i['id']: True}) <= 28 for i in preset['items']))
cond("rubric: 14+14 auto-merges", score(preset, {"change_type": True, "recent_churn": True}) <= 28)
cond("rubric: a 24 item with any other item goes to a human", all(score(preset, {a: True, b: True}) > 28 for a in ["spread", "size", "incident_history"] for b in [i['id'] for i in preset['items']] if a != b))
ov = {"extends": "agentive:rubric@1", "items": [{"id": "recent_churn", "enabled": False}]}
chk("rubric: override switches an item off", "rubric.schema.json", ov, True)
chk("rubric: override changes the threshold", "rubric.schema.json", {"extends": "agentive:rubric@1", "human_if_score_gt": 20}, True)
chk("rubric: override without extends", "rubric.schema.json", {"items": [{"id": "size", "weight": 30}]}, False)
chk("rubric: override with nothing to change", "rubric.schema.json", {"extends": "agentive:rubric@1"}, False)
x = copy.deepcopy(preset); x['scoring'] = 'buckets'; chk("rubric: non-binary scoring", "rubric.schema.json", x, False)
x = copy.deepcopy(preset); x['items'][0]['input'] = 'author_experience'; chk("rubric: unknown input", "rubric.schema.json", x, False)
x = copy.deepcopy(preset); x['preset'] = 'A'; chk("rubric: preset A (U6 option, removed)", "rubric.schema.json", x, False)
def merge(p, o):
    e = copy.deepcopy(p); byid = {i['id']: i for i in e['items']}
    for it in o.get('items', []):
        if it['id'] in byid: byid[it['id']].update(it)
        else: e['items'].append(it)
    for k in ('human_if_score_gt', 'shared_globs'):
        if k in o: e[k] = o[k]
    return e
eff = merge(preset, ov)
cond("rubric: merge by id keeps 5 items, recent_churn disabled", len(eff['items']) == 5 and [i for i in eff['items'] if i['id'] == 'recent_churn'][0]['enabled'] is False)
chk("rubric: effective config still a valid preset", "rubric.schema.json", eff, True)

# ================= guard-rules =================
section('guard-rules')
guard = {"version": 1, "rules": [
    {"id": "push-default-branch", "decision": "deny", "match": {"kind": "special", "value": "default_branch_push"}, "reason": "the default branch changes only through PRs", "next": "push a work branch and run /agentive:pr"},
    {"id": "push-force", "decision": "deny", "match": {"kind": "regex", "value": "git push .*(--force|-f\\b|\\+)"}, "reason": "force push rewrites shared history", "next": "push a new commit instead"},
    {"id": "push-tag", "decision": "deny", "match": {"kind": "regex", "value": "git push .*(--tags|--follow-tags)"}, "reason": "releases are done by humans", "next": "ask a human to tag the release"},
    {"id": "publish-npm", "decision": "deny", "match": {"kind": "argv_prefix", "value": ["npm", "publish"], "unless": ["--dry-run"]}, "reason": "publishing is done by humans from CI", "next": "run npm publish --dry-run or ask a human"},
    {"id": "infra-change", "decision": "deny", "match": {"kind": "argv_prefix", "value": ["terraform", "apply"]}, "reason": "infra changes are the infra team's job", "next": "write the change in a PR for the infra team"},
    {"id": "reset-hard", "decision": "ask", "match": {"kind": "argv_prefix", "value": ["git", "reset", "--hard"]}, "reason": "discards uncommitted work", "next": "commit or stash first"},
    {"id": "sql-delete-no-where", "decision": "deny", "expressible": False, "match": {"kind": "special", "value": "sql_without_where"}, "reason": "DELETE without WHERE removes every row", "next": "add a WHERE clause"}]}
chk("guard-rules: framework deny/ask sample", "guard-rules.schema.json", guard, True)
x = copy.deepcopy(guard); x['rules'][5]['decision'] = 'allow'; chk("guard-rules: allow decision", "guard-rules.schema.json", x, False)
x = copy.deepcopy(guard); x['rules'][6]['decision'] = 'ask'; chk("guard-rules: non-expressible ask rejected (must be deny)", "guard-rules.schema.json", x, False)
x = copy.deepcopy(guard); x['rules'][0].pop('next'); chk("guard-rules: rule without next", "guard-rules.schema.json", x, False)
x = copy.deepcopy(guard); x['rules'][1]['pack'] = 'core.git'; chk("guard-rules: pack field (always on, removed)", "guard-rules.schema.json", x, False)
x = copy.deepcopy(guard); x['rules'][5]['decision'] = 'defer'; chk("guard-rules: defer decision (dropped)", "guard-rules.schema.json", x, False)
def override_ok(base, over):
    b = {r['id']: r for r in base['rules']}
    for r in over['rules']:
        if r['id'] in b and b[r['id']]['decision'] == 'deny' and r['decision'] == 'ask':
            return False
    return {r['id'] for r in base['rules']} <= {r['id'] for r in base['rules']} | {r['id'] for r in over['rules']}
cond("guard-rules: repo override may turn ask into deny", override_ok(guard, {"rules": [{"id": "reset-hard", "decision": "deny"}]}))
cond("guard-rules: repo override may not turn deny into ask", not override_ok(guard, {"rules": [{"id": "push-force", "decision": "ask"}]}))
def msg(r): return f"BLOCKED {r['id']}: {r['reason']}. NEXT: {r['next']}"
cond("guard-rules: block message template", msg(guard['rules'][0]).startswith("BLOCKED push-default-branch: ") and "NEXT: " in msg(guard['rules'][0]))

# ================= review-report =================
section('review-report')
FIND = {"id": "f1", "reviewers": ["correctness-reviewer"], "severity": "important", "blocking": True, "file": "a.ts", "line": 3, "trigger": "when items is empty", "summary": "x",
        "verification": {"verdict": "confirmed", "verifier_session_id": "s9", "method": "execution"}}
rep = {"profile": "ci", "head_sha": H, "stages_run": ["RV1", "RV2", "RV3", "RV4", "RV7b", "RV5", "RV7", "RV6", "RV8"], "review_round": {"round": 1, "mode": "full"},
  "rv1": {"checks": [{"name": "lint", "status": "pass", "source": "ci"}], "excluded": ["dist/app.js"], "diff_stats": {"files": 3, "added": 40, "deleted": 2},
          "signals": {"public_api_change": "skipped", "changed_files": 3, "modules_touched": ["api"], "incident_history_90d": False, "recent_churn_30d": True}},
  "rv2": {"hits": [], "confirm_scope_hits": []}, "u2": {"run": "interactive", "human_confirmation": "not_required", "action": "commit", "source": "pr_body"},
  "rv3": {"classes": ["fix"], "method": "deterministic"}, "rv4": {"tier": 1, "reasons": ["class fix"]},
  "rv5": {"reviewers": [{"reviewer_id": "correctness-reviewer", "tool": "claude_code", "session_id": "s2", "context": "fresh"}, {"reviewer_id": "security-reviewer", "tool": "codex", "session_id": "s3", "context": "fresh"}]},
  "findings": [FIND],
  "rv7b": {"score": 14, "reason": "computed", "preset": "agentive:rubric@1", "human_if_score_gt": 28, "config_sha256": SHA, "items": [{"id": "recent_churn", "hit": True, "weight": 14}]},
  "rv8": {"decision": "block", "reasons": ["confirmed important f1 without a response"], "u1_value": "C", "auto_merge": False, "check_conclusion": "failure", "head_sha": H, "tier1_mode": "on"}, "at": "2026-10-27T10:00:00Z"}
chk("review-report: ci block on a confirmed important finding", "review-report.schema.json", rep, True)
am = copy.deepcopy(rep); am['findings'] = []; am['rv8'] = {"decision": "pass", "reasons": ["tier 1", "score 14 <= 28"], "u1_value": "C", "auto_merge": True, "check_conclusion": "success", "head_sha": H, "tier1_mode": "on", "demoted": False, "kill_switch": {"tier": 1, "source": "octocat", "count": 0}}
chk("review-report: tier 1 auto-merge (score <= 28, tier1 on)", "review-report.schema.json", am, True)
x = copy.deepcopy(am); x['rv8']['tier1_mode'] = 'shadow'; chk("review-report: tier 1 auto-merge while shadow", "review-report.schema.json", x, False)
x = copy.deepcopy(am); x['rv8']['demoted'] = True; chk("review-report: tier 1 auto-merge while demoted", "review-report.schema.json", x, False)
x = copy.deepcopy(am); x['rv8']['kill_switch']['count'] = 2; chk("review-report: auto-merge with kill-switch count 2", "review-report.schema.json", x, False)
x = copy.deepcopy(am); x['rv8'].pop('kill_switch'); chk("review-report: auto-merge without kill-switch scope", "review-report.schema.json", x, False)
x = copy.deepcopy(am); x['rv7b'] = {"score": None, "reason": "tier1_off"}; chk("review-report: tier 1 auto-merge without a score", "review-report.schema.json", x, False)
x = copy.deepcopy(am); x['rv8']['gate_only'] = ["public_api_breaking"]; chk("review-report: auto-merge despite a gate-only condition", "review-report.schema.json", x, False)
x = copy.deepcopy(am); x['rv4']['tier'] = 2; chk("review-report: tier 2 auto-merge", "review-report.schema.json", x, False)
x = copy.deepcopy(am); x['rv8']['u1_value'] = 'B'; chk("review-report: tier 1 auto-merge under U1=B", "review-report.schema.json", x, False)
x = copy.deepcopy(am); x['rv4'] = {"tier": 0, "reasons": ["docs only"]}; x['rv3'] = {"classes": ["docs"], "method": "deterministic"}; x['rv7b'] = {"score": None, "reason": "not_tier1"}; x['rv8']['u1_value'] = 'B'; x['rv8']['kill_switch']['tier'] = 0
chk("review-report: tier 0 auto-merge under U1=B", "review-report.schema.json", x, True)
sh = copy.deepcopy(am); sh['rv8'] = {"decision": "human_required", "reasons": ["shadow"], "u1_value": "C", "auto_merge": False, "check_conclusion": "success", "head_sha": H, "tier1_mode": "shadow", "shadow_would_auto_merge": True}
chk("review-report: shadow -> human merges, check success", "review-report.schema.json", sh, True)
x = copy.deepcopy(sh); x['rv8']['check_conclusion'] = 'failure'; chk("review-report: human_required concluding failure (P3: success)", "review-report.schema.json", x, False)
x = copy.deepcopy(sh); x['rv8']['check_conclusion'] = 'neutral'; chk("review-report: neutral conclusion", "review-report.schema.json", x, False)
x = copy.deepcopy(rep); x['rv8']['check_conclusion'] = 'success'; chk("review-report: block concluding success", "review-report.schema.json", x, False)
x = copy.deepcopy(rep); x['rv8'].pop('head_sha'); chk("review-report: gate without head_sha (P4)", "review-report.schema.json", x, False)
x = copy.deepcopy(rep); x['rv8']['sampled'] = True; chk("review-report: sampled (U1.b removed)", "review-report.schema.json", x, False)
x = copy.deepcopy(rep); x['rv7b'] = {"score": None, "reason": "rubric_undecided"}; chk("review-report: rubric_undecided (U6 decided)", "review-report.schema.json", x, False)
x = copy.deepcopy(rep); x['findings'][0].pop('verification'); chk("review-report: ci finding without verification", "review-report.schema.json", x, False)
x = copy.deepcopy(rep); x['findings'][0]['verification']['verdict'] = 'refuted'; chk("review-report: refuted but blocking", "review-report.schema.json", x, False)
x = copy.deepcopy(rep); x['findings'][0]['severity'] = 'nit'; chk("review-report: blocking nit", "review-report.schema.json", x, False)
x = copy.deepcopy(rep); x['findings'][0]['severity'] = 'pre_existing'; chk("review-report: blocking pre_existing (P2)", "review-report.schema.json", x, False)
x = copy.deepcopy(rep); x['findings'][0]['shadow'] = True; chk("review-report: blocking shadow-reviewer finding", "review-report.schema.json", x, False)
x = copy.deepcopy(rep); x['findings'][0].pop('trigger'); chk("review-report: finding without trigger condition", "review-report.schema.json", x, False)
x = copy.deepcopy(rep); x['findings'][0].pop('line'); chk("review-report: finding without line", "review-report.schema.json", x, False)
x = copy.deepcopy(rep); x['findings'][0]['reviewers'] = ["maintainability"]; chk("review-report: removed perspective as reviewer", "review-report.schema.json", x, False)
nit = {"id": "f2", "reviewers": ["correctness-reviewer"], "severity": "nit", "blocking": False, "file": "a.ts", "line": 9, "trigger": "always", "summary": "name",
       "verification": {"verdict": "confirmed", "verifier_session_id": "s9", "method": "different_model"}}
x = copy.deepcopy(rep); x['findings'].append(nit); chk("review-report: verified nit (all findings verified)", "review-report.schema.json", x, True)
x = copy.deepcopy(rep); n2 = copy.deepcopy(nit); n2.pop('verification'); x['findings'].append(n2); chk("review-report: unverified nit in ci", "review-report.schema.json", x, False)
x = copy.deepcopy(rep); x['review_round'] = {"round": 2, "mode": "incremental", "since_sha": "def5678"}; chk("review-report: round 2 incremental", "review-report.schema.json", x, True)
x = copy.deepcopy(rep); x['review_round'] = {"round": 2, "mode": "incremental"}; chk("review-report: incremental round without since_sha", "review-report.schema.json", x, False)
x = copy.deepcopy(rep); x['review_round'] = {"round": 1, "mode": "incremental", "since_sha": "def5678"}; chk("review-report: round 1 incremental", "review-report.schema.json", x, False)
x = copy.deepcopy(rep); x.pop('review_round'); chk("review-report: ci without review_round", "review-report.schema.json", x, False)
x = copy.deepcopy(rep); x.pop('rv7b'); chk("review-report: ci without rv7b", "review-report.schema.json", x, False)
x = copy.deepcopy(rep); x.pop('rv4'); chk("review-report: ci without rv4", "review-report.schema.json", x, False)
pre_rep = {"profile": "precommit", "head_sha": H, "stages_run": ["RV1", "RV2", "RV5"], "rv1": rep['rv1'], "rv2": rep['rv2'], "u2": {**rep['u2'], "source": "local"},
           "rv5": {"reviewers": [rep['rv5']['reviewers'][0]]}, "at": "2026-10-27T10:00:00Z"}
chk("review-report: precommit profile RV1, RV2, RV5", "review-report.schema.json", pre_rep, True)
x = copy.deepcopy(pre_rep); x['rv4'] = {"tier": 0, "reasons": []}; chk("review-report: precommit with local tier (removed)", "review-report.schema.json", x, False)
x = copy.deepcopy(pre_rep); x['stages_run'] = ["RV1", "RV2", "RV3", "RV4", "RV5"]; chk("review-report: precommit running RV3/RV4", "review-report.schema.json", x, False)
# U2.non_interactive
HIT = [{"path": ".github/workflows/ci.yml", "category": "ci", "glob": ".github/workflows/**"}]
ALLP = {k: True for k in ["section", "first", "before", "after", "before_mermaid", "after_mermaid"]}
def forced(base):
    x = copy.deepcopy(base); x['rv2']['confirm_scope_hits'] = HIT
    x['rv2']['hits'] = [{"path": ".github/workflows/ci.yml", "glob": ".github/workflows/**", "reason": "ci_config"}]
    x['u2'] = {"run": "non_interactive", "human_confirmation": "not_obtained", "action": "pr_only_tier2", "source": "pr_body"}
    x['rv4'] = {"tier": 2, "reasons": ["RV2 hit", "U2.non_interactive"]}; x['findings'] = []
    x['rv8'] = {"decision": "human_required", "reasons": ["tier 2", "U2.non_interactive"], "u1_value": "C", "auto_merge": False, "check_conclusion": "success", "head_sha": H,
                "explainer": {"required": True, "result": "ok", "problems": [], "present": dict(ALLP)}}
    return x
f0 = forced(rep)
chk("review-report: U2 hit, no human -> PR only, tier 2, human merges", "review-report.schema.json", f0, True)
x = copy.deepcopy(f0); x['rv4'] = {"tier": 1, "reasons": ["RV2 hit"]}; chk("review-report: hit without human but tier 1", "review-report.schema.json", x, False)
x = copy.deepcopy(f0); x['u2']['action'] = 'commit'; chk("review-report: hit without human but action commit", "review-report.schema.json", x, False)
x = copy.deepcopy(f0); x['rv8'] = {"decision": "pass", "reasons": [], "u1_value": "C", "auto_merge": True, "check_conclusion": "success", "head_sha": H, "demoted": False, "kill_switch": {"tier": 1, "source": "x", "count": 0}}
chk("review-report: hit without human but auto-merge", "review-report.schema.json", x, False)
x = copy.deepcopy(f0); x['u2']['human_confirmation'] = 'obtained'; x['u2']['action'] = 'commit'; chk("review-report: non_interactive claims obtained", "review-report.schema.json", x, False)
x = copy.deepcopy(f0); x['rv2']['confirm_scope_hits'] = [{"path": ".agents/custom/.agents/rubric.yaml", "category": "review_config", "glob": ".agents/custom/.agents/rubric.yaml"}]
chk("review-report: rubric override change is a review_config hit", "review-report.schema.json", x, True)
x = copy.deepcopy(f0); x['u2'] = {"run": "unknown", "human_confirmation": "not_obtained", "action": "pr_only_tier2", "source": "pr_body_missing"}
chk("review-report: PR body without u2 -> unknown, not obtained", "review-report.schema.json", x, True)
x = copy.deepcopy(rep); x['u2']['human_confirmation'] = 'obtained'; chk("review-report: no hit but obtained", "review-report.schema.json", x, False)
x = copy.deepcopy(rep); x['rv2']['confirm_scope_hits'] = [{"path": "docs/x.md", "category": "docs", "glob": "**"}]; chk("review-report: unknown confirm_scope category", "review-report.schema.json", x, False)
cmap = [{"kind": "dedicated", "sources": ["code_review.md"], "files": ["src/a.ts"]}, {"kind": "fallback", "sources": ["services/api/CLAUDE.md"], "files": ["services/api/b.ts"]}, {"kind": "none", "files": ["tools/x.sh"]}]
x = copy.deepcopy(rep); x['rv1']['criteria'] = cmap; chk("review-report: criteria map dedicated/fallback/none", "review-report.schema.json", x, True)
x = copy.deepcopy(rep); x['rv1']['criteria'] = [{"kind": "fallback", "sources": ["REVIEW.md"], "files": ["a"]}]; chk("review-report: fallback source REVIEW.md", "review-report.schema.json", x, False)
def criteria_consistent(entries):
    seen = set()
    for en in entries:
        for f in en['files']:
            if f in seen: return False
            seen.add(f)
            for s in en.get('sources', []):
                sd = posixpath.dirname(s)
                if sd and not (posixpath.dirname(f) == sd or posixpath.dirname(f).startswith(sd + '/')): return False
    return True
cond("criteria: one entry per file, sources in an ancestor directory", criteria_consistent(cmap))
cond("criteria: nested fallback outside its directory caught", not criteria_consistent([{"kind": "fallback", "sources": ["services/api/CLAUDE.md"], "files": ["src/c.ts"]}]))
# kill switch counting (RV8 logic, plain python)
def kill_count(prs, tier, source, now_day, closed_day=None):
    return sum(1 for p in prs if p['auto'] and p['tier'] == tier and p['source'] == source and p['bad_day'] is not None
               and now_day - p['bad_day'] <= 7 and (closed_day is None or p['bad_day'] > closed_day))
prs = [{"auto": True, "tier": 1, "source": "bot", "bad_day": 10}, {"auto": True, "tier": 1, "source": "bot", "bad_day": 12}, {"auto": True, "tier": 1, "source": "alice", "bad_day": 12}, {"auto": True, "tier": 0, "source": "bot", "bad_day": 12}]
cond("kill switch: 2 bad tier-1 PRs from one source within 7 days -> that scope demoted", kill_count(prs, 1, "bot", 14) >= 2)
cond("kill switch: other source of the same tier not demoted", kill_count(prs, 1, "alice", 14) < 2)
cond("kill switch: tier 0 of the same source counted separately", kill_count(prs, 0, "bot", 14) < 2)
cond("kill switch: events before the latest close are not counted", kill_count(prs, 1, "bot", 14, closed_day=11) < 2)
cond("kill switch: events older than 7 days are not counted", kill_count(prs, 1, "bot", 19) < 2)

# ================= precommit-review =================
section('precommit-review')
pre = {"head_sha": H, "blocking": [], "nits": [{"file": "a.ts", "line": 2, "trigger": "always", "summary": "name"}], "at": "2026-10-27T10:00:00Z", "trust_boundary_hits": [], "confirm_scope_hits": [],
       "u2": {"run": "interactive", "human_confirmation": "not_required", "action": "commit"}, "checks_ok": True, "reviewer": {"reviewer_id": "correctness-reviewer", "session_id": "s4", "context": "fresh"}}
chk("precommit-review: clean run", "precommit-review.schema.json", pre, True)
for k, val in [("diff_sha256", SHA), ("tier", 0), ("classification", ["docs"])]:
    chk(f"precommit-review: removed field {k}", "precommit-review.schema.json", {**pre, k: val}, False)
p_ = copy.deepcopy(pre); p_['confirm_scope_hits'] = HIT; p_['u2'] = {"run": "non_interactive", "human_confirmation": "not_obtained", "action": "pr_only_tier2"}
chk("precommit-review: hit, no human -> pr_only_tier2", "precommit-review.schema.json", p_, True)
x = copy.deepcopy(p_); x['u2']['action'] = 'commit'; chk("precommit-review: hit, no human, commit", "precommit-review.schema.json", x, False)
x = copy.deepcopy(p_); x['u2'] = {"run": "non_interactive", "human_confirmation": "obtained", "action": "commit"}; chk("precommit-review: non_interactive claims obtained", "precommit-review.schema.json", x, False)
x = copy.deepcopy(pre); x['reviewer']['reviewer_id'] = 'correctness'; chk("precommit-review: old perspective name", "precommit-review.schema.json", x, False)
x = copy.deepcopy(pre); x['blocking'] = [{"file": "a.ts", "summary": "x"}]; chk("precommit-review: finding without line/trigger", "precommit-review.schema.json", x, False)
x = copy.deepcopy(pre); x.pop('u2'); chk("precommit-review: u2 missing", "precommit-review.schema.json", x, False)

# ================= pr-body =================
section('pr-body-agentive-review')
pr = {"agentive-review": {"implementer": {"tool": "claude_code", "model": "m", "session_id": "s1", "started_at": "2026-10-27T09:00:00Z"},
  "reviews": [{"role": "reviewer", "reviewer_id": "security-reviewer", "tool": "codex", "model": "m", "session_id": "s3", "context": "fresh", "source": "ci", "verdict": "pass"},
              {"role": "verifier", "tool": "claude_code", "model": "m", "session_id": "s9", "context": "fresh", "source": "ci", "verdict": "pass"}],
  "review_round": 1,
  "pipeline": {"classification": {"classes": ["fix"], "method": "deterministic"}, "tier": {"value": 1, "reasons": ["class fix"]}, "verified_findings": [],
     "risk_score": {"score": 14, "reason": "computed", "preset": "agentive:rubric@1", "human_if_score_gt": 28},
     "gate": {"decision": "pass", "check": "agentive-review-gate", "auto_merge": True, "shadow": False, "demoted": False, "head_sha": H}}}}
chk("pr-body: tier 1 auto-merge with rubric score", "pr-body-agentive-review.schema.json", pr, True)
XH = S['pr-body-agentive-review.schema.json'].get('x-agentive-body-headings', {})
cond("pr-body: all-tier headings declared in order", XH.get('all_tiers') == ["## Verification", "## Risk", "## How to revert"])
cond("pr-body: tier-2 heading order starts with ## Explainer (EXPLAIN)", XH.get('tier2') == ["## Explainer"] + XH.get('all_tiers', []))
def headings_ok(body):
    hs = XH['all_tiers']; pos = [body.find(h) for h in hs]
    return all(p >= 0 for p in pos) and pos == sorted(pos)
cond("pr-body: body with Verification, Risk, How to revert passes the heading check", headings_ok("## Verification\nlint -> 0\n## Risk\nlow\n## How to revert\ngit revert <sha>\n```yaml\n```"))
cond("pr-body: body without How to revert fails the heading check", not headings_ok("## Verification\n## Risk\n"))
x = copy.deepcopy(pr); x['agentive-review']['pipeline']['gate']['shadow'] = True; chk("pr-body: auto-merge while shadow", "pr-body-agentive-review.schema.json", x, False)
x = copy.deepcopy(pr); x['agentive-review']['pipeline']['tier']['value'] = 2; chk("pr-body: tier 2 auto-merge", "pr-body-agentive-review.schema.json", x, False)
x = copy.deepcopy(pr); x['agentive-review']['pipeline']['risk_score'] = {"score": None, "reason": "tier1_off"}; chk("pr-body: tier 1 auto-merge without score", "pr-body-agentive-review.schema.json", x, False)
x = copy.deepcopy(pr); x['agentive-review']['pipeline']['gate']['sampled'] = True; chk("pr-body: sampled (removed)", "pr-body-agentive-review.schema.json", x, False)
x = copy.deepcopy(pr); x['agentive-review']['reviews'][0]['perspective'] = 'security'; chk("pr-body: old perspective field", "pr-body-agentive-review.schema.json", x, False)
x = copy.deepcopy(pr); x['agentive-review']['hotfix_of'] = 41; chk("pr-body: hotfix_of (kill switch input)", "pr-body-agentive-review.schema.json", x, True)
x = copy.deepcopy(pr); x['agentive-review']['pipeline']['verified_findings'] = [{"id": "f1", "severity": "nit", "blocking": True, "verdict": "confirmed", "file": "a", "line": 1, "summary": "s"}]
chk("pr-body: blocking nit", "pr-body-agentive-review.schema.json", x, False)
pr3 = copy.deepcopy(pr); pr3['agentive-review']['u2'] = {"run": "non_interactive", "human_confirmation": "not_obtained", "confirm_scope_hits": [".github/workflows/ci.yml"]}
pr3['agentive-review']['pipeline']['tier'] = {"value": 2, "reasons": ["RV2 hit", "U2.non_interactive"]}; pr3['agentive-review']['pipeline']['gate'] = {"decision": "human_required", "check": "agentive-review-gate", "auto_merge": False}
chk("pr-body: u2 not_obtained -> tier 2, no auto-merge", "pr-body-agentive-review.schema.json", pr3, True)
x = copy.deepcopy(pr3); x['agentive-review']['pipeline']['tier']['value'] = 1; chk("pr-body: u2 not_obtained but tier 1", "pr-body-agentive-review.schema.json", x, False)
chk("pr-body: old block name fw-review", "pr-body-agentive-review.schema.json", {"fw-review": pr['agentive-review']}, False)
x = copy.deepcopy(pr); x['agentive-review']['pipeline']['gate']['check'] = 'fw-review-gate'; chk("pr-body: old gate name", "pr-body-agentive-review.schema.json", x, False)

# ================= tier-2 ## Explainer (decisions.accepted EXPLAIN, 2026-10-27) =================
section('tier-2 explainer (RV8 check)')
import re
XE = XH['tier2_explainer']
RE_PL = re.compile(XE['code_ref_format']['path_line']); RE_PS = re.compile(XE['code_ref_format']['path_symbol'])
def explainer_check(body, tier):
    """RV8 deterministic presence check (presence only since 2026-10-27). Returns (result, problems, present)."""
    if tier != 2:
        return 'not_required', [], None
    pres = {k: False for k in ALLP}
    lines = body.split('\n'); h2 = [i for i, l in enumerate(lines) if l.startswith('## ')]
    ex = [i for i in h2 if lines[i].strip() == XE['heading']]
    if not ex:
        return 'explainer_missing', ['no_section'], pres
    pres['section'] = True; probs = []
    if h2[0] != ex[0]: probs.append('not_first')
    else: pres['first'] = True
    end_ = next((i for i in h2 if i > ex[0]), len(lines)); sec = lines[ex[0] + 1:end_]
    bi = next((i for i, l in enumerate(sec) if l.strip() == '### Before'), None); ai = next((i for i, l in enumerate(sec) if l.strip() == '### After'), None)
    if bi is None: probs.append('no_before')
    else: pres['before'] = True
    if ai is None: probs.append('no_after')
    else: pres['after'] = True
    if bi is not None and ai is not None and bi > ai: probs.append('before_order')
    def has_mermaid(part): return any(l.strip() == '```mermaid' for l in part) and sum(1 for l in part if l.strip().startswith('```')) >= 2
    if bi is not None and ai is not None and bi < ai:
        if has_mermaid(sec[bi + 1:ai]): pres['before_mermaid'] = True
        else: probs.append('before_no_mermaid')
        if has_mermaid(sec[ai + 1:]): pres['after_mermaid'] = True
        else: probs.append('after_no_mermaid')
    return ('explainer_missing' if probs else 'ok'), probs, pres
def refs_resolve(body, head, base):
    """not a CI check: confirms that the page example's code refs point inside the example tree (writing-rule quality of the example)."""
    bad = []
    for m in RE_PL.finditer(body):
        path, last = m.group(1), int(m.group(3) or m.group(2))
        if not ((path in head and last <= head[path]) or (path in base and last <= base[path])): bad.append(m.group(0))
    return bad
import yaml as _y
_Y = _y.safe_load(open(os.path.join(os.path.dirname(os.path.abspath(D)), 'framework.yaml'), encoding='utf-8'))
E = _Y['view']['explainer']; TREE = E['example_tree']
def ex_body(before=True, after=True, mermaid=True, refs=True, first=True):
    text = ['- ' + x for x in E['text']] if refs else ['- 로그인에 rate limit을 넣습니다.']
    mb = E['mermaid_before'].rstrip('\n'); ma = E['mermaid_after'].rstrip('\n')
    if not refs:
        mb = re.sub(RE_PL, 'x', mb); ma = re.sub(RE_PL, 'x', ma)
    parts = ['## Explainer', ''] + text
    if before: parts += ['', '### Before'] + (['```mermaid', mb, '```'] if mermaid else ['(그림 없음)'])
    if after: parts += ['', '### After', '```mermaid', ma, '```']
    parts += ['', E['pr_legend_line']]
    rest = ['## Verification', 'test -> 0', '## Risk', 'auth 경로', '## How to revert', 'git revert <sha>']
    return '\n'.join(parts + [''] + rest) if first else '\n'.join(rest[:2] + [''] + parts + [''] + rest[2:])
H_T, B_T = TREE['head'], TREE['base']
r = explainer_check(ex_body(), 2)
cond("explainer: the page example body passes the RV8 presence check (tier 2)", r[0] == 'ok' and r[1] == [] and r[2] == ALLP)
cond("explainer: the page example's code refs point inside the example tree (writing rule; not a CI check)", refs_resolve(ex_body(), H_T, B_T) == [] and len(RE_PL.findall(ex_body())) >= 3)
cond("explainer: page example still passes the all-tier heading check", headings_ok(ex_body()))
cond("explainer: tier 2 without ## Explainer -> explainer_missing (no_section)", explainer_check("## Verification\n## Risk\n## How to revert\n", 2)[:2] == ('explainer_missing', ['no_section']))
cond("explainer: ## Explainer not first -> not_first", 'not_first' in explainer_check(ex_body(first=False), 2)[1])
cond("explainer: missing ### Before -> no_before", 'no_before' in explainer_check(ex_body(before=False), 2)[1])
cond("explainer: missing ### After -> no_after", 'no_after' in explainer_check(ex_body(after=False), 2)[1])
cond("explainer: ### Before without a mermaid block -> before_no_mermaid", 'before_no_mermaid' in explainer_check(ex_body(mermaid=False), 2)[1])
cond("explainer: presence only: a body without any code ref passes CI (refs are a writing rule, RV5 checks)", explainer_check(ex_body(refs=False), 2)[0] == 'ok')
cond("explainer: presence only: refs that resolve in no tree do not fail CI", explainer_check(ex_body().replace('src/auth/routes.ts:42', 'src/gone.ts:999'), 2)[0] == 'ok')
cond("explainer: tier 0 and tier 1 bodies need no ## Explainer", explainer_check("## Verification\n## Risk\n## How to revert\n", 0)[0] == 'not_required' and explainer_check("## Verification\n## Risk\n## How to revert\n", 1)[0] == 'not_required')
cond("explainer: ref format: a range path:start-end is one ref", RE_PL.search('`src/auth/rate_limit.ts:9-20`').group(0) == 'src/auth/rate_limit.ts:9-20')
cond("explainer: ref format: a URL fragment is not a path#symbol ref", RE_PS.search('see https://github.com/o/r/blob/main/a.ts#L10') is None and RE_PS.search('`src/a.ts#handler`') is not None)
cond("explainer: ref format: a time like 10:30 is not a path:line ref", RE_PL.search('at 10:30 today') is None)
cond("explainer: retired resolve rule and problem code are gone from the schema (presence only)", 'code_ref_rule' not in XE and 'code_ref_patterns' not in XE and 'no_head_code_ref' not in XE['problems'] and 'presence only' in XE['check'])
# review-report rv8.explainer
EXOK = {"required": True, "result": "ok", "problems": [], "present": dict(ALLP)}
t2 = copy.deepcopy(f0)
chk("review-report: tier 2 human_required with a valid explainer", "review-report.schema.json", t2, True)
x = copy.deepcopy(t2); x['rv8'].pop('explainer'); chk("review-report: tier 2 without the explainer check", "review-report.schema.json", x, False)
x = copy.deepcopy(t2); x['rv8']['explainer'] = {"required": True, "result": "explainer_missing", "problems": ["no_section"], "next_action": "run /agentive:pr --explain", "present": {**{k: False for k in ALLP}}}
chk("review-report: explainer_missing but decision human_required", "review-report.schema.json", x, False)
x['rv8'].update({"decision": "block", "check_conclusion": "failure", "reasons": ["tier 2", "explainer_missing"]})
chk("review-report: explainer_missing -> block, failure, reason", "review-report.schema.json", x, True)
y_ = copy.deepcopy(x); y_['rv8']['reasons'] = ["tier 2"]; chk("review-report: explainer_missing block without the reason", "review-report.schema.json", y_, False)
y_ = copy.deepcopy(x); y_['rv8']['explainer'].pop('next_action'); chk("review-report: explainer_missing without a next action", "review-report.schema.json", y_, False)
y_ = copy.deepcopy(x); y_['rv8']['explainer']['problems'] = []; chk("review-report: explainer_missing without problems", "review-report.schema.json", y_, False)
y_ = copy.deepcopy(x); y_['rv8']['explainer']['problems'] = ["mermaid_syntax"]; chk("review-report: mermaid syntax is not a CI problem code", "review-report.schema.json", y_, False)
y_ = copy.deepcopy(x); y_['rv8']['explainer']['problems'] = ["no_head_code_ref"]; chk("review-report: retired problem code no_head_code_ref", "review-report.schema.json", y_, False)
y_ = copy.deepcopy(x); y_['rv8']['explainer'].pop('present'); chk("review-report: explainer_missing without the presence record", "review-report.schema.json", y_, False)
x = copy.deepcopy(t2); x['rv8']['explainer'] = {"required": True, "result": "ok", "problems": ["no_after"], "present": dict(ALLP)}; chk("review-report: ok with a problem", "review-report.schema.json", x, False)
x = copy.deepcopy(t2); x['rv8']['explainer'] = {"required": True, "result": "ok", "problems": [], "present": {**ALLP, "after_mermaid": False}}; chk("review-report: ok while a presence check is false", "review-report.schema.json", x, False)
x = copy.deepcopy(t2); x['rv8']['explainer'] = {"required": True, "result": "ok", "problems": []}; chk("review-report: ok without the presence record", "review-report.schema.json", x, False)
x = copy.deepcopy(t2); x['rv8']['explainer']['head_refs'] = ["src/app.ts:3"]; chk("review-report: retired field head_refs", "review-report.schema.json", x, False)
x = copy.deepcopy(t2); x['rv8']['explainer']['trigger'] = 'pull_request_review_comment'; chk("review-report: gate re-run by a reply line (pull_request_review_comment)", "review-report.schema.json", x, True)
x = copy.deepcopy(t2); x['rv8']['explainer'] = {"required": False, "result": "not_required"}; chk("review-report: tier 2 marked not_required", "review-report.schema.json", x, False)
x = copy.deepcopy(t2); x['rv8']['explainer']['trigger'] = 'edited'; chk("review-report: re-run on pull_request edited", "review-report.schema.json", x, True)
x = copy.deepcopy(am); x['rv8']['explainer'] = {"required": False, "result": "not_required"}; chk("review-report: tier 1 auto-merge, explainer not required", "review-report.schema.json", x, True)
x = copy.deepcopy(am); x['rv8']['explainer'] = {"required": True, "result": "explainer_missing", "problems": ["no_section"], "next_action": "n", "present": {k: False for k in ALLP}}; chk("review-report: tier 1 required to have an explainer", "review-report.schema.json", x, False)
# ================= agentive-reply line, finding marker, finding_disputed (decisions.accepted REPLYLINE, 2026-10-27) =================
section('reply line (REPLYLINE)')
RLY = _Y['spec']['review_pipeline']['severity_blocking']['reply_line']; RE_RL = re.compile(RLY['regex'])
def reply_ok(line, assoc='MEMBER'):
    """parse one line of a thread reply; None = ignored"""
    if assoc not in ('OWNER', 'MEMBER', 'COLLABORATOR'): return None
    m = RE_RL.match(line)
    if not m or (m.group(2) in RLY['sentence_required_for'] and not m.group(3)): return None
    return m.group(1), m.group(2), m.group(3)
cond("reply line: fixed without a sentence is accepted", reply_ok('agentive-reply: f1 fixed') == ('f1', 'fixed', None))
cond("reply line: wrong with a sentence is accepted", reply_ok('agentive-reply: f2 wrong the guard on line 40 already rejects empty input') == ('f2', 'wrong', 'the guard on line 40 already rejects empty input'))
cond("reply line: wrong, intentional or not_important without a sentence is ignored", all(reply_ok(f'agentive-reply: f3 {r}') is None for r in ['wrong', 'intentional', 'not_important']))
cond("reply line: an unknown reason is ignored", reply_ok('agentive-reply: f4 wontfix later') is None)
cond("reply line: a line from an author without write access is ignored", reply_ok('agentive-reply: f1 fixed', assoc='NONE') is None and reply_ok('agentive-reply: f1 fixed', assoc='CONTRIBUTOR') is None)
cond("reply line: the line must start the reply line (quoted text is not a reply line)", reply_ok('> agentive-reply: f1 fixed') is None)
cond("reply line: posted findings carry the hidden marker <!-- agentive-finding: <id> -->", '<!-- agentive-finding: <id> -->' in RLY['finding_marker'])
FD = {**FIND, "blocking": False, "response": {"kind": "replied_resolved", "reason": "wrong", "source": "reply_line", "comment_id": 9}}
dis = copy.deepcopy(am); dis['findings'] = [FD]
dis['rv8'] = {"decision": "human_required", "reasons": ["finding_disputed"], "u1_value": "C", "auto_merge": False, "check_conclusion": "success", "head_sha": H, "tier1_mode": "on"}
chk("review-report: confirmed important answered 'wrong' -> human_required, reason finding_disputed", "review-report.schema.json", dis, True)
x = copy.deepcopy(dis); x['rv8']['reasons'] = ["tier 1"]; chk("review-report: disputed finding without reason finding_disputed", "review-report.schema.json", x, False)
x = copy.deepcopy(dis); x['rv8'] = copy.deepcopy(am['rv8']); chk("review-report: disputed finding but auto-merge", "review-report.schema.json", x, False)
x = copy.deepcopy(dis); x['findings'][0]['response'] = {"kind": "fixed", "reason": "fixed", "source": "next_round"}; x['rv8'] = copy.deepcopy(am['rv8'])
chk("review-report: fixed confirmed by the next round, auto-merge allowed", "review-report.schema.json", x, True)
x = copy.deepcopy(dis); x['findings'][0]['response'] = {"kind": "replied_resolved", "reason": "wrong", "source": "next_round"}; chk("review-report: next_round with a reason other than fixed", "review-report.schema.json", x, False)
x = copy.deepcopy(dis); x['findings'][0]['response'].pop('comment_id'); chk("review-report: reply_line source without comment_id", "review-report.schema.json", x, False)
x = copy.deepcopy(dis); x['findings'][0]['response']['source'] = 'reaction'; chk("review-report: reactions are not a response source", "review-report.schema.json", x, False)
x = copy.deepcopy(dis); x['findings'][0]['response'] = {"kind": "replied_resolved", "reason": "intentional", "source": "inferred"}; chk("review-report: [inferred] reason from a plain human reply still disputes", "review-report.schema.json", x, True)
chk("review-report: tier 1 without the explainer record (optional)", "review-report.schema.json", am, True)

# ================= artifact chain intent.md -> spec.md -> plan.md (decisions.accepted CHAIN, 2026-10-27) =================
section('artifact chain (CHAIN: RV1 detect, RV2 always human, RV8 block)')
XC = S['pr-body-agentive-review.schema.json']['x-agentive-chain']
RE_SLUG = re.compile(XC['slug_pattern'])
def chain_check(changed, base, head, marker=None):
    """RV1 chain check. changed = {path: git status A|M|D|R}; base/head = sets of paths; marker = agentive-review.chain.
    Returns (slug, stage, result, problems)."""
    arts = {}
    for p_, st_ in changed.items():
        m = re.fullmatch(r'docs/plans/([^/]+)/(intent|spec|plan)\.md', p_)
        if m and st_ in ('A', 'M') and m.group(1) not in XC['reserved_slugs']:
            arts.setdefault(m.group(1), set()).add(m.group(2))
    slugs = set(arts) | ({marker['slug']} if marker else set())
    if not slugs:
        return None, None, 'not_chain', []
    probs = []
    if len(slugs) > 1:
        probs.append('multiple_slugs')
    slug = sorted(slugs)[0]; kinds = arts.get(slug, set())
    stage = 'feature' if ('plan' in kinds or (marker and marker['stage'] == 'feature')) else ('spec' if 'spec' in kinds else ('intent' if 'intent' in kinds else marker['stage']))
    d = f'docs/plans/{slug}/'
    if 'spec' in kinds or stage == 'spec':
        if d + 'intent.md' not in base: probs.append('intent_not_on_base')
    if stage == 'feature':
        if d + 'spec.md' not in base: probs.append('spec_not_on_base')
        if d + 'plan.md' not in head: probs.append('plan_not_in_head')
    for k in ('intent', 'spec'):
        if k in kinds and changed[d + k + '.md'] == 'A' and len(changed) > 1:
            probs.append('mixed_artifacts'); break
    return slug, stage, ('chain_order' if probs else 'ok'), probs
B0 = {'src/app.ts'}
I_ = 'docs/plans/login-limit/intent.md'; S_ = 'docs/plans/login-limit/spec.md'; P_ = 'docs/plans/login-limit/plan.md'
r = chain_check({I_: 'A'}, B0, B0 | {I_})
cond("chain: intent PR with only intent.md -> ok, stage intent", r == ('login-limit', 'intent', 'ok', []))
r = chain_check({S_: 'A'}, B0, B0 | {S_})
cond("chain: spec PR without intent.md on base -> chain_order intent_not_on_base", r[1:] == ('spec', 'chain_order', ['intent_not_on_base']))
r = chain_check({S_: 'A'}, B0 | {I_}, B0 | {I_, S_})
cond("chain: spec PR after the intent PR merged -> ok", r[1:] == ('spec', 'ok', []))
r = chain_check({I_: 'A', S_: 'A'}, B0, B0 | {I_, S_})
cond("chain: intent.md and spec.md in one PR -> intent_not_on_base + mixed_artifacts", r[2] == 'chain_order' and {'intent_not_on_base', 'mixed_artifacts'} <= set(r[3]))
r = chain_check({P_: 'A', 'src/limit.ts': 'A'}, B0 | {I_}, B0 | {I_, P_, 'src/limit.ts'})
cond("chain: plan + code PR without spec.md on base -> spec_not_on_base", r[1:] == ('feature', 'chain_order', ['spec_not_on_base']))
r = chain_check({P_: 'A', 'src/limit.ts': 'A'}, B0 | {I_, S_}, B0 | {I_, S_, P_, 'src/limit.ts'})
cond("chain: feature PR with plan.md first and spec.md on base -> ok", r[1:] == ('feature', 'ok', []))
r = chain_check({'src/limit.ts': 'M'}, B0 | {I_, S_}, B0 | {I_, S_}, {'slug': 'login-limit', 'stage': 'feature'})
cond("chain: code PR linked by agentive-review.chain without plan.md in head -> plan_not_in_head", r[1:] == ('feature', 'chain_order', ['plan_not_in_head']))
r = chain_check({'src/limit.ts': 'M'}, B0 | {I_}, B0 | {I_, P_}, {'slug': 'login-limit', 'stage': 'feature'})
cond("chain: code PR linked by marker without spec.md on base -> spec_not_on_base", 'spec_not_on_base' in r[3])
r = chain_check({I_: 'A', 'src/app.ts': 'M'}, B0, B0 | {I_})
cond("chain: intent PR that also changes code -> mixed_artifacts", r[2:] == ('chain_order', ['mixed_artifacts']))
r = chain_check({I_: 'A', 'docs/plans/other/intent.md': 'A'}, B0, B0)
cond("chain: two slugs in one PR -> multiple_slugs", 'multiple_slugs' in r[3])
r = chain_check({'README.md': 'M'}, B0, B0)
cond("chain: non-chain small PR -> not_chain, no problem (no spec safety net)", r == (None, None, 'not_chain', []))
r = chain_check({'src/app.ts': 'M', 'src/b.ts': 'A'}, B0, B0 | {'src/b.ts'})
cond("chain: non-chain code PR without any spec is not checked (spec_ci_safety_net stays removed)", r[2] == 'not_chain')
r = chain_check({I_: 'D', 'docs/plans/archive/login-limit/intent.md': 'A'}, B0 | {I_}, B0)
cond("chain: move into docs/plans/archive/ is not a chain-stage change", r[2] == 'not_chain')
cond("chain: slug pattern rejects archive and upper case, accepts kebab-case", RE_SLUG.match('login-limit') and not RE_SLUG.match('archive') and not RE_SLUG.match('Login'))
# --- next-stage start check (skill step 1): the previous artifact must be on the default branch
def stage_check(stage, slug, default_tree):
    need = {'spec': 'intent.md', 'plan': 'spec.md'}.get(stage)
    if stage == 'intent':
        return ('ask_same_change', 1) if f'docs/plans/{slug}/intent.md' in default_tree else ('ok', 0)
    return ('ok', 0) if f'docs/plans/{slug}/{need}' in default_tree else (f'AGENTIVE_CHAIN=stop:{stage}:{slug}', 1)
cond("start check: /chain-spec before the intent PR merged stops (exit 1, AGENTIVE_CHAIN=stop)", stage_check('spec', 'login-limit', B0) == ('AGENTIVE_CHAIN=stop:spec:login-limit', 1))
cond("start check: /chain-plan after the spec PR merged continues", stage_check('plan', 'login-limit', B0 | {I_, S_}) == ('ok', 0))
cond("start check: /chain-plan with only intent.md merged stops", stage_check('plan', 'login-limit', B0 | {I_})[1] == 1)
cond("start check: /chain-intent on a slug that already holds an intent asks first", stage_check('intent', 'login-limit', B0 | {I_})[0] == 'ask_same_change')
def default_branch(symref, ls_remote):
    """stage-check.sh: git symbolic-ref --short refs/remotes/origin/HEAD, else git ls-remote --symref origin HEAD (A9_fallback)"""
    if symref: return symref[len('origin/'):] if symref.startswith('origin/') else symref
    m = re.match(r'ref: refs/heads/(\S+)\tHEAD', ls_remote or '')
    return m.group(1) if m else None
cond("start check: default branch from origin/HEAD", default_branch('origin/main', None) == 'main')
cond("start check: origin/HEAD missing -> git ls-remote --symref origin HEAD fallback", default_branch(None, 'ref: refs/heads/trunk\tHEAD\n0123abc\tHEAD') == 'trunk')
cond("start check: both missing -> no default branch (stop with NEXT)", default_branch(None, '') is None)
# --- schema: review-report rv1.chain + tier 2 + block
def with_chain(base, ch, tier=None, rv8=None):
    x = copy.deepcopy(base); x['rv1']['chain'] = ch
    if tier is not None: x['rv4'] = tier
    if rv8 is not None: x['rv8'] = rv8
    return x
T2C = {"tier": 2, "reasons": ["chain_approval"]}
EXI = {"required": True, "result": "ok", "problems": [], "present": dict(ALLP)}
HR = {"decision": "human_required", "reasons": ["tier 2", "chain_approval"], "u1_value": "C", "auto_merge": False, "check_conclusion": "success", "head_sha": H, "explainer": EXI}
ip = copy.deepcopy(rep); ip['findings'] = []; ip['rv3'] = {"classes": ["docs"], "method": "deterministic"}; ip['rv7b'] = {"score": None, "reason": "not_tier1"}
CI_OK = {"slug": "login-limit", "stage": "intent", "changed_artifacts": [I_], "result": "ok", "problems": []}
chk("review-report: intent PR forced to tier 2 (chain_approval), human merges", "review-report.schema.json", with_chain(ip, CI_OK, T2C, HR), True)
chk("review-report: intent PR as docs tier 0 (must be tier 2)", "review-report.schema.json", with_chain(ip, CI_OK, {"tier": 0, "reasons": ["docs only"]}, {**HR, "explainer": {"required": False, "result": "not_required"}}), False)
chk("review-report: intent PR tier 2 without reason chain_approval", "review-report.schema.json", with_chain(ip, CI_OK, {"tier": 2, "reasons": ["docs"]}, HR), False)
SP_OK = {**CI_OK, "stage": "spec", "changed_artifacts": [S_], "base_has_intent": True}
chk("review-report: spec PR with intent on base, tier 2, human merges", "review-report.schema.json", with_chain(ip, SP_OK, T2C, HR), True)
x = with_chain(ip, SP_OK, T2C, {**HR, "decision": "pass", "auto_merge": True, "demoted": False, "kill_switch": {"tier": 0, "source": "x", "count": 0}})
chk("review-report: spec PR auto-merged", "review-report.schema.json", x, False)
SP_BAD = {**SP_OK, "base_has_intent": False, "result": "ok", "problems": []}
chk("review-report: spec PR without intent on base recorded as ok", "review-report.schema.json", with_chain(ip, SP_BAD, T2C, HR), False)
SP_ORD = {**SP_OK, "base_has_intent": False, "result": "chain_order", "problems": ["intent_not_on_base"], "next_action": XC['next_actions']['intent_not_on_base']}
BLK = {"decision": "block", "reasons": ["chain_order"], "u1_value": "C", "auto_merge": False, "check_conclusion": "failure", "head_sha": H, "explainer": EXI}
chk("review-report: spec PR without intent on base -> block, reason chain_order", "review-report.schema.json", with_chain(ip, SP_ORD, T2C, BLK), True)
chk("review-report: chain_order but decision human_required", "review-report.schema.json", with_chain(ip, SP_ORD, T2C, HR), False)
chk("review-report: chain_order block without the reason", "review-report.schema.json", with_chain(ip, SP_ORD, T2C, {**BLK, "reasons": ["tier 2"]}), False)
x = copy.deepcopy(SP_ORD); x.pop('next_action'); chk("review-report: chain_order without a next action", "review-report.schema.json", with_chain(ip, x, T2C, BLK), False)
FE_ORD = {"slug": "login-limit", "stage": "feature", "changed_artifacts": [P_], "base_has_spec": False, "head_has_plan": True, "result": "chain_order", "problems": ["spec_not_on_base"], "next_action": XC['next_actions']['spec_not_on_base']}
chk("review-report: plan/code PR without spec on base -> block chain_order", "review-report.schema.json", with_chain(rep, FE_ORD, None, {**rep['rv8'], "reasons": ["chain_order"]}), True)
x = copy.deepcopy(FE_ORD); x['problems'] = ["plan_not_in_head"]; chk("review-report: missing spec on base not listed as a problem", "review-report.schema.json", with_chain(rep, x, None, {**rep['rv8'], "reasons": ["chain_order"]}), False)
FE_NOPLAN = {**FE_ORD, "base_has_spec": True, "head_has_plan": False, "problems": ["plan_not_in_head"], "next_action": XC['next_actions']['plan_not_in_head']}
chk("review-report: feature PR without plan.md in head -> block", "review-report.schema.json", with_chain(rep, FE_NOPLAN, None, {**rep['rv8'], "reasons": ["chain_order"]}), True)
FE_OK = {"slug": "login-limit", "stage": "feature", "changed_artifacts": [P_], "base_has_spec": True, "head_has_plan": True, "result": "ok", "problems": []}
chk("review-report: feature PR of a chain keeps the normal tier rules (tier 1 auto-merge)", "review-report.schema.json", with_chain(am, FE_OK), True)
chk("review-report: non-chain small PR unaffected (not_chain, tier 1 auto-merge)", "review-report.schema.json", with_chain(am, {"result": "not_chain"}), True)
chk("review-report: not_chain with a slug", "review-report.schema.json", with_chain(am, {"result": "not_chain", "slug": "x"}), False)
chk("review-report: tier 1 auto-merge without any chain record", "review-report.schema.json", am, True)
x = copy.deepcopy(pre_rep); x['rv1'] = copy.deepcopy(x['rv1']); x['rv1']['chain'] = SP_ORD
chk("review-report: precommit reports chain_order (no rv8, commit not blocked)", "review-report.schema.json", x, True)
x = with_chain(ip, {**CI_OK, "problems": ["mixed_artifacts"], "result": "chain_order", "next_action": "split"}, T2C, BLK)
chk("review-report: intent PR with other files -> mixed_artifacts block", "review-report.schema.json", x, True)
x = with_chain(ip, {**CI_OK, "problems": ["missing_spec"], "result": "chain_order", "next_action": "x"}, T2C, BLK)
chk("review-report: unknown chain problem code", "review-report.schema.json", x, False)
# --- schema: pr-body chain field
pc = copy.deepcopy(pr); pc['agentive-review']['chain'] = {"slug": "login-limit", "stage": "feature"}
chk("pr-body: feature PR with chain field, tier 1 auto-merge (normal rules)", "pr-body-agentive-review.schema.json", pc, True)
pi = copy.deepcopy(pr); pi['agentive-review']['chain'] = {"slug": "login-limit", "stage": "intent"}
chk("pr-body: intent PR at tier 1 auto-merge", "pr-body-agentive-review.schema.json", pi, False)
pi['agentive-review']['pipeline']['tier'] = {"value": 2, "reasons": ["chain_approval"]}; pi['agentive-review']['pipeline']['gate'] = {"decision": "human_required", "check": "agentive-review-gate", "auto_merge": False}
chk("pr-body: intent PR tier 2 chain_approval, human merges", "pr-body-agentive-review.schema.json", pi, True)
x = copy.deepcopy(pi); x['agentive-review']['chain']['stage'] = 'spec'; x['agentive-review']['pipeline']['tier']['reasons'] = ["docs"]
chk("pr-body: spec PR tier 2 without chain_approval", "pr-body-agentive-review.schema.json", x, False)
x = copy.deepcopy(pi); x['agentive-review']['chain'] = {"slug": "archive", "stage": "intent"}; chk("pr-body: reserved slug archive", "pr-body-agentive-review.schema.json", x, False)
x = copy.deepcopy(pi); x['agentive-review']['chain'] = {"slug": "login-limit", "stage": "plan"}; chk("pr-body: stage plan (plan.md ships in the feature PR)", "pr-body-agentive-review.schema.json", x, False)
x = copy.deepcopy(pi); x['agentive-review'].pop('pipeline'); chk("pr-body: intent PR body written by /agentive:pr before CI", "pr-body-agentive-review.schema.json", x, True)
cond("chain: problem codes in x-agentive-chain match review-report rv1.chain enum", XC['problems'] == S['review-report.schema.json']['properties']['rv1']['properties']['chain']['properties']['problems']['items']['enum'])
cond("chain: every problem code has a next action", set(XC['problems']) == set(XC['next_actions']))
# --- tier-2 explainer on intent/spec PRs: a ref to the artifact itself satisfies the head-ref rule; code PRs unchanged
ib = '\n'.join(['## Explainer', '', '- **무엇**: 로그인 시도 제한을 제안합니다 (`docs/plans/login-limit/intent.md:3`).', '', '### Before', '```mermaid', 'flowchart TD', '  a["현재: 로그인 제한 없음"]', '```', '', '### After', '```mermaid', 'flowchart TD', '  a["제안: 계정 · IP별 분당 한도"]:::added', '```', '', E['pr_legend_line'], '', '## Verification', 'not run', '## Risk', 'none', '## How to revert', 'close the PR'])
r = explainer_check(ib, 2)
cond("explainer: greenfield intent PR passes the presence check (ref to the artifact itself in the writing)", r[0] == 'ok' and refs_resolve(ib, {I_: 30}, {}) == [])
cond("explainer: intent PR may cite existing affected code instead", explainer_check(ib.replace('docs/plans/login-limit/intent.md:3', 'src/auth/routes.ts:42'), 2)[0] == 'ok')
cond("explainer: a PR whose refs resolve nowhere passes CI (presence only; RV5 checks the content)", explainer_check(ib.replace('docs/plans/login-limit/intent.md:3', 'src/gone.ts:3'), 2)[0] == 'ok')

# ================= manifest =================
section('manifest (agentive manifest)')
mf = {"schema_version": 1, "framework": {"plugin": "agentive", "version": "1.0.0"},
      "verbs": {"lint": {"cmd": "npm run lint", "tier": "inner", "evidence": {"rank": 1, "source": "package.json", "files": ["package.json"]}, "proof": {"pass_run_id": "r1", "seeded_fail_run_id": "r2", "at": "2026-10-27T10:00:00Z"}},
                "test": {"cmd": "npm test", "tier": "inner", "evidence": {"rank": 1, "source": "package.json", "files": ["package.json"]}, "proof": {"pass_run_id": "r3", "seeded_fail_run_id": "r4", "at": "2026-10-27T10:00:00Z"}, "fail_count": "^\\s+●", "coverage_report": "coverage/coverage-summary.json"},
                "verify": {"cmd": ".agents/bin/verb verify", "tier": "inner", "evidence": {"rank": 4, "source": "framework", "files": [".agents/manifest.yaml"]}, "proof": {"pass_run_id": "r5", "at": "2026-10-27T10:00:00Z"}}},
      "paths": {"src": [{"path": "src", "proof": "exists"}], "tests": [{"path": "tests", "proof": "exists"}], "generated": [{"path": "dist", "proof": "gitignored"}], "vendor": [{"path": "node_modules", "proof": "gitignored"}]},
      "capabilities": {"T1": {"result": "pass", "fallback_applied": False, "tool": {"name": "claude_code", "version": "2.1.296"}, "at": "2026-10-27T10:00:00Z"}},
      "tools": {"claude_code": {"version": "2.1.296", "plugin": {"name": "agentive", "version": "1.0.0"}}},
      "stop_conditions": {"max_blocks": 8, "no_progress_rounds": 2}, "context": {"read_limit_bytes": 65536}, "janitor": {"kinds": []},
      "generated": {"AGENTS.md#path-rules": {"kind": "region", "sha256": SHA, "framework_version": "1.0.0", "verified_at": "2026-10-27", "verified_commit": "0123abc"}, ".agents/rubric.yaml": {"kind": "file", "sha256": SHA, "framework_version": "1.0.0"}}}
chk("manifest: full sample", "manifest.schema.json", mf, True)
x = copy.deepcopy(mf); x['capabilities']['T2'] = mf['capabilities']['T1']; chk("manifest: probe T2 (now a documented fact)", "manifest.schema.json", x, False)
x = copy.deepcopy(mf); x['capabilities']['T4'] = mf['capabilities']['T1']; chk("manifest: probe T4 (now a documented fact)", "manifest.schema.json", x, False)
x = copy.deepcopy(mf); x['tools']['gh'] = {"version": "2.0.0"}; chk("manifest: CLI list in tools (reversed decision)", "manifest.schema.json", x, False)
x = copy.deepcopy(mf); x['janitor']['kinds'] = [{"id": "lint-fix", "sensor": "npm run lint -- -f json", "batch_max": 5}]; chk("manifest: one janitor kind", "manifest.schema.json", x, True)
x = copy.deepcopy(mf); x['janitor']['kinds'] = [{"id": "lint-fix", "sensor": "npm run lint"}]; chk("manifest: janitor kind without batch_max", "manifest.schema.json", x, False)
x = copy.deepcopy(mf); x['context']['read_limit_bytes'] = 100; chk("manifest: read limit below 1 KiB", "manifest.schema.json", x, False)
cond("manifest: context gate default is 64 KiB", S['manifest.schema.json']['properties']['context']['properties']['read_limit_bytes']['default'] == 65536)
pend = {"status": "pending", "reason": "no_evidence"}
gf = copy.deepcopy(mf); gf['verbs'] = {"lint": dict(pend), "test": dict(pend), "verify": dict(pend)}; gf['paths'] = {"src": [], "tests": [], "generated": [], "vendor": []}
chk("manifest: empty directory, every verb pending", "manifest.schema.json", gf, True)
x = copy.deepcopy(gf); x['verbs']['build'] = {"status": "pending", "reason": "no_evidence", "cmd": "cargo build"}; chk("manifest: pending verb with a guessed command", "manifest.schema.json", x, False)
x = copy.deepcopy(mf); x['generated']['AGENTS.md#path-rules'].pop('verified_commit'); chk("manifest: region without verified_commit", "manifest.schema.json", x, False)
x = copy.deepcopy(mf); x['generated']['.agents/custom/AGENTS.md'] = {"kind": "file", "sha256": SHA, "framework_version": "1.0.0"}; chk("manifest: override file as generated entry", "manifest.schema.json", x, False)
x = copy.deepcopy(mf); x['framework']['plugin'] = 'fw'; chk("manifest: old plugin name", "manifest.schema.json", x, False)

# ================= verify-result / stop-state / handoff / pending-confirmation / harvest / reviewer-eval-case =================
section('state files')
vr = {"run_id": "r1", "ts": "2026-10-27T10:00:00Z", "total_failures": 1, "head_sha": H, "worktree_dirty": False,
      "verbs": {"test": {"ok": False, "failures": 1, "duration_ms": 900, "cmd": "npm test", "log": ".agents/state/logs/test-20261027T100000.log"}}, "pending": ["typecheck"]}
chk("verify-result: head_sha + worktree_dirty + cmd/log", "verify-result.schema.json", vr, True)
x = copy.deepcopy(vr); x.pop('head_sha'); chk("verify-result: without head_sha (E1c)", "verify-result.schema.json", x, False)
x = copy.deepcopy(vr); x.pop('worktree_dirty'); chk("verify-result: without worktree_dirty", "verify-result.schema.json", x, False)
x = copy.deepcopy(vr); x['pending'] = ["deploy"]; chk("verify-result: unknown pending verb", "verify-result.schema.json", x, False)
def reusable(rec, pr_head): return rec['head_sha'] == pr_head and not rec['worktree_dirty']
cond("verify record reused only with the same head and a clean tree", reusable(vr, H) and not reusable({**vr, "worktree_dirty": True}, H) and not reusable(vr, "fff0000"))
st = {"session_id": "s1", "consecutive_blocks": 0, "last_fail_count": 3, "handoff_requested": True, "failure_window": [], "last_reset": "after_no_progress", "last_reason": "NO-PROGRESS"}
chk("stop-state: NO-PROGRESS with handoff_requested", "stop-state.schema.json", st, True)
x = copy.deepcopy(st); x['last_reset'] = 'user_prompt_submit'; chk("stop-state: reset on UserPromptSubmit (removed)", "stop-state.schema.json", x, False)
x = copy.deepcopy(st); x['last_reason'] = 'ESCALATE'; chk("stop-state: old ESCALATE reason", "stop-state.schema.json", x, False)
ho = {"branch": "feat/login-flow", "branch_key": "feat-login-flow-1a2b3c4d", "head": H, "worktree_diff_sha256": SHA, "created_at": "2026-10-27T10:00:00Z", "reason": "no_progress",
      "plan": "docs/plans/login/plan.md", "task": "slice 2: session cookie", "failure_reason": "test auth.spec fails on token refresh", "untried": "mock clock", "next_step": "read logs/test-...log line 40",
      "previous": [{"at": "2026-10-26T10:00:00Z", "head": "1112223", "summary": "lint loop on generated types"}]}
chk("handoff: NO-PROGRESS frontmatter", "handoff.schema.json", ho, True)
x = copy.deepcopy(ho); x['branch'] = None; x['branch_key'] = 'detached-abc1234'; chk("handoff: detached HEAD key", "handoff.schema.json", x, True)
x = copy.deepcopy(ho); x['branch_key'] = 'feat/login-flow'; chk("handoff: raw branch name as key", "handoff.schema.json", x, False)
x = copy.deepcopy(ho); x['reason'] = 'ask_class_denied'; chk("handoff: ask_class_denied without the command", "handoff.schema.json", x, False)
x['blocked_command'] = "git reset --hard (reset-hard)"; chk("handoff: ask_class_denied with the command", "handoff.schema.json", x, True)
x = copy.deepcopy(ho); x['previous'] = x['previous'] * 6; chk("handoff: more than 5 previous summaries", "handoff.schema.json", x, False)
x = copy.deepcopy(ho); x.pop('next_step'); chk("handoff: without next_step", "handoff.schema.json", x, False)
import hashlib, re
def branch_key(b):
    if b is None: return None
    return re.sub(r'[^a-z0-9.-]', '-', b.lower()) + '-' + hashlib.sha256(b.encode()).hexdigest()[:8]
k = branch_key("Feat/Login Flow"); cond("handoff: branch key from a branch name matches the schema pattern", re.fullmatch(r'[a-z0-9.-]+-[a-f0-9]{8}', k) is not None and '/' not in k)
pcf = {"created_at": "2026-10-27T10:00:00Z", "command": "/agentive:update", "framework_version": "1.1.0", "files": [{"path": ".claude/settings.json", "category": "claude_settings", "rendered_sha256": SHA}]}
chk("pending-confirmation: one skipped file", "pending-confirmation.schema.json", pcf, True)
x = copy.deepcopy(pcf); x['files'] = []; chk("pending-confirmation: empty list", "pending-confirmation.schema.json", x, False)
x = copy.deepcopy(pcf); x['files'][0]['category'] = 'docs'; chk("pending-confirmation: non-confirm_scope category", "pending-confirmation.schema.json", x, False)
hq = {"session_id": "s1", "transcript_path": "/t", "reason": "repeat", "at": "2026-10-27T10:00:00Z", "signals": {"repeated_command": [{"normalized": "gh run view <id> --log-failed", "count": 3, "sessions": 1}]}}
chk("harvest-queue: repeated_command signal", "harvest-queue.schema.json", hq, True)
x = copy.deepcopy(hq); x['signals'] = {"takeover": True}; chk("harvest-queue: takeover signal (removed)", "harvest-queue.schema.json", x, False)
x = copy.deepcopy(hq); x['signals'] = {}; chk("harvest-queue: no signal", "harvest-queue.schema.json", x, False)
# reviewer-eval-case (replaces golden-task, 2026-10-27; origin, forbidden_findings and $defs.summary added 2026-10-27 RVEVALRUN)
RS = 'reviewer-eval-case.schema.json'; RSU = 'https://fw.local/schemas/reviewer-eval-case.schema.json#/$defs/summary'
rpos = {"id": "inc-41", "pr": "#41", "label": "positive", "origin": {"kind": "fix_pr", "ref": "#44"}, "expected_findings": [{"file": "src/auth/session.ts", "line_from": 30, "line_to": 34}]}
chk("reviewer-eval-case: positive (incident PR + origin fix_pr + human-confirmed file:line range)", RS, rpos, True)
for k_ in ["revert", "reproduction_diff", "injected_bug"]:
    x = copy.deepcopy(rpos); x['origin'] = {"kind": k_, "ref": "abc1234"}; chk(f"reviewer-eval-case: positive with origin {k_}", RS, x, True)
x = copy.deepcopy(rpos); x['origin'] = {"kind": "llm_guess", "ref": "x"}; chk("reviewer-eval-case: unknown origin kind", RS, x, False)
x = copy.deepcopy(rpos); x.pop('origin'); chk("reviewer-eval-case: positive without origin", RS, x, False)
x = copy.deepcopy(rpos); x['fix_pr'] = "#44"; chk("reviewer-eval-case: retired field fix_pr (use origin)", RS, x, False)
x = copy.deepcopy(rpos); x.pop('expected_findings'); chk("reviewer-eval-case: positive without expected findings", RS, x, False)
x = copy.deepcopy(rpos); x['expected_findings'] = []; chk("reviewer-eval-case: positive with an empty finding list", RS, x, False)
x = copy.deepcopy(rpos); x['expected_findings'][0].pop('line_from'); chk("reviewer-eval-case: finding without a line", RS, x, False)
x = copy.deepcopy(rpos); x['forbidden_findings'] = [{"file": "a.ts", "line_from": 1, "line_to": 2}]; chk("reviewer-eval-case: positive with forbidden findings", RS, x, False)
x = copy.deepcopy(rpos); x['budget_usd'] = 1.5; chk("reviewer-eval-case: optional budget", RS, x, True)
rneg = {"id": "clean-50", "pr": "#50", "label": "negative"}
chk("reviewer-eval-case: negative (known-clean PR)", RS, rneg, True)
rnz = {"id": "noise-61", "pr": "#61", "label": "negative", "forbidden_findings": [{"file": "src/api/list.ts", "line_from": 12, "line_to": 12}]}
chk("reviewer-eval-case: negative with forbidden_findings (answered wrong or intentional, B11)", RS, rnz, True)
x = copy.deepcopy(rnz); x['forbidden_findings'] = []; chk("reviewer-eval-case: empty forbidden_findings", RS, x, False)
x = copy.deepcopy(rneg); x['expected_findings'] = rpos['expected_findings']; chk("reviewer-eval-case: negative with expected findings", RS, x, False)
x = copy.deepcopy(rneg); x['origin'] = {"kind": "fix_pr", "ref": "#51"}; chk("reviewer-eval-case: negative with an origin", RS, x, False)
x = copy.deepcopy(rneg); x.pop('label'); chk("reviewer-eval-case: without label", RS, x, False)
# retired implementer golden-task fields are rejected
for k, val in [("kind", "agent"), ("prompt", "fix login"), ("setup", {"fixture": "ts"}), ("expected", {"verbs_green": ["test"], "no_human_edit": True}), ("max_turns", 30), ("reviewer_case", {"pr": "#41", "label": "positive"})]:
    chk(f"reviewer-eval-case: retired golden-task field {k}", RS, {**rneg, k: val}, False)
# scoring (review_pipeline.reviewer_eval.scoring): counted = posted important findings; location match on file + line range (instances too)
def _caught(rng, counted):
    return any(f['file'] == rng['file'] and any(rng['line_from'] <= l <= rng['line_to'] for l in [f['line']] + [i['line'] for i in f.get('instances', []) if i['file'] == f['file']]) for f in counted)
def rv_case_pass(case, findings):
    counted = [f for f in findings if f['severity'] == 'important' and f.get('posted', True)]
    if case['label'] == 'positive':
        return all(_caught(e, counted) for e in case['expected_findings'])
    return not any(f.get('blocking') for f in findings) and not any(_caught(r_, counted) for r_ in case.get('forbidden_findings', []))
F_ = lambda file, line, sev='important', blocking=True, posted=True, inst=(): {"file": file, "line": line, "severity": sev, "blocking": blocking, "posted": posted, "instances": [{"file": file, "line": i} for i in inst]}
cond("reviewer-eval: positive passes only with a posted important finding inside the expected range", rv_case_pass(rpos, [F_("src/auth/session.ts", 31)]) and not rv_case_pass(rpos, [F_("src/auth/session.ts", 80)]))
cond("reviewer-eval: a summary-only mention does not count (S135)", not rv_case_pass(rpos, [F_("src/auth/session.ts", 31, posted=False)]))
cond("reviewer-eval: a nit at the right place does not count", not rv_case_pass(rpos, [F_("src/auth/session.ts", 31, sev='nit', blocking=False)]))
cond("reviewer-eval: a bug-class instance inside the range counts", rv_case_pass(rpos, [F_("src/auth/session.ts", 90, inst=(33,))]))
cond("reviewer-eval: negative passes with nits only and fails with a blocking finding", rv_case_pass(rneg, [F_("a.ts", 1, sev='nit', blocking=False)]) and not rv_case_pass(rneg, [F_("a.ts", 1)]))
cond("reviewer-eval: negative with forbidden_findings fails when a counted finding is posted there again", not rv_case_pass(rnz, [F_("src/api/list.ts", 12, blocking=False)]) and rv_case_pass(rnz, [F_("src/api/other.ts", 12, blocking=False)]))
def summarize(cases, base_runs, cand_runs):
    """review_pipeline.reviewer_eval.ci: runs = list of N {case_id: pass}; case result = majority; means per side; noise = baseline spread"""
    n = len(base_runs); maj = lambda runs, cid: sum(r_[cid] for r_ in runs) * 2 > n
    pos = [c for c in cases if c['label'] == 'positive']; neg = [c for c in cases if c['label'] == 'negative']
    pb = [sum(r_[c['id']] for c in pos) for r_ in base_runs]; pc = [sum(r_[c['id']] for c in pos) for r_ in cand_runs]
    nb = [sum(not r_[c['id']] for c in neg) for r_ in base_runs]; nc = [sum(not r_[c['id']] for c in neg) for r_ in cand_runs]
    mean = lambda v: sum(v) / len(v)
    out = {"baseline_ref": "base0001", "candidate_ref": "head0002", "n_runs": n,
           "cases": [{"id": c['id'], "label": c['label'], "baseline_pass_runs": sum(r_[c['id']] for r_ in base_runs), "candidate_pass_runs": sum(r_[c['id']] for r_ in cand_runs)} for c in cases],
           "positives": {"total": len(pos), "baseline_mean": mean(pb), "candidate_mean": mean(pc), "baseline_spread": max(pb) - min(pb)},
           "negatives": {"total": len(neg), "baseline_mean": mean(nb), "candidate_mean": mean(nc), "baseline_spread": max(nb) - min(nb)},
           "newly_caught": [c['id'] for c in pos if not maj(base_runs, c['id']) and maj(cand_runs, c['id'])],
           "newly_missed": [c['id'] for c in pos if maj(base_runs, c['id']) and not maj(cand_runs, c['id'])],
           "newly_noisy": [c['id'] for c in neg if maj(base_runs, c['id']) and not maj(cand_runs, c['id'])]}
    if not pos or not neg: reason = 'insufficient_cases'
    elif out['newly_missed']: reason = 'newly_missed'
    else:
        dp = mean(pc) - mean(pb); dn = mean(nb) - mean(nc)
        improved = (dp > out['positives']['baseline_spread']) if dp != 0 else (dn > out['negatives']['baseline_spread'] if dn != 0 else False)
        reason = 'improved' if improved else ('no_regression' if not out['newly_noisy'] else 'no_gain')
    out['reason'] = reason; out['adopt_suggested'] = reason in ('improved', 'no_regression')
    return out
CS = [{"id": "p1", "label": "positive"}, {"id": "p2", "label": "positive"}, {"id": "n1", "label": "negative"}, {"id": "n2", "label": "negative"}]
run = lambda **kw: {"p1": True, "p2": False, "n1": True, "n2": True, **kw}
s1 = summarize(CS, [run()] * 3, [run(p2=True)] * 3)
cond("reviewer-eval CI: catching one more positive beyond the run-to-run spread -> improved, adopt suggested", s1['reason'] == 'improved' and s1['adopt_suggested'] and s1['newly_caught'] == ['p2'])
chkref("reviewer-eval summary: improved block is schema-valid", RSU, s1, True)
s2 = summarize(CS, [run()] * 3, [run(p1=False, p2=True)] * 3)
cond("reviewer-eval CI: a positive caught before and missed after -> newly_missed, not adopted (even with a new catch)", s2['reason'] == 'newly_missed' and not s2['adopt_suggested'] and s2['newly_missed'] == ['p1'])
chkref("reviewer-eval summary: newly_missed block is schema-valid", RSU, s2, True)
x = copy.deepcopy(s2); x['adopt_suggested'] = True; chkref("reviewer-eval summary: adopt suggested despite newly_missed", RSU, x, False)
s3 = summarize(CS, [run(n2=False)] * 3, [run()] * 3)
cond("reviewer-eval CI: tie on positives, fewer unwanted findings on negatives -> improved", s3['reason'] == 'improved' and s3['adopt_suggested'])
s4 = summarize(CS, [run()] * 3, [run()] * 3)
cond("reviewer-eval CI: same results (for example a model alias move) -> no_regression, adopt suggested", s4['reason'] == 'no_regression' and s4['adopt_suggested'])
s5 = summarize(CS, [run(), run(p2=True), run()], [run(p2=True), run(n1=False), run(p2=True, n1=False)])
cond("reviewer-eval CI: a gain within the noise with a newly noisy negative -> no_gain, not adopted", s5['reason'] == 'no_gain' and not s5['adopt_suggested'] and s5['newly_noisy'] == ['n1'])
x = copy.deepcopy(s5); x['reason'] = 'no_regression'; x['adopt_suggested'] = True; chkref("reviewer-eval summary: no_regression with a newly noisy case", RSU, x, False)
s6 = summarize(CS[:2], [run()] * 3, [run(p2=True)] * 3)
cond("reviewer-eval CI: no negative case -> insufficient_cases, not adopted", s6['reason'] == 'insufficient_cases' and not s6['adopt_suggested'])
chkref("reviewer-eval summary: insufficient_cases block is schema-valid", RSU, s6, True)
x = copy.deepcopy(s1); x.pop('n_runs'); chkref("reviewer-eval summary: without n_runs", RSU, x, False)

# ================= drift-report (unchanged contract, regression) =================
section('drift-report regression')
dr = {"schema_version": 1, "generated_at": "2026-10-27T12:00:00Z", "framework": {"from_version": "1.0.0", "to_version": "1.1.0"},
      "overwritten": [{"id": ".claude/skills/verify/SKILL.md", "path": ".claude/skills/verify/SKILL.md"}],
      "items": [{"id": "AGENTS.md#path-rules", "path": "AGENTS.md", "section": "path-rules", "reason": "customized", "differences": [{"kind": "changed", "summary": "rule pointer edited"}], "template_sha256": "b" * 64, "current_sha256": "c" * 64}]}
chk("drift-report: kept item + overwritten", "drift-report.schema.json", dr, True)
x = copy.deepcopy(dr); x['items'][0]['differences'] = []; chk("drift-report: item without differences", "drift-report.schema.json", x, False)
x = copy.deepcopy(dr); x['force'] = True; x['forced'] = [{"id": "CLAUDE.md#imports", "path": "CLAUDE.md", "section": "imports", "reason": "customized", "previous_sha256": "d" * 64}]
chk("drift-report: --force run", "drift-report.schema.json", x, True)
x = copy.deepcopy(dr); x['stale_check'] = {"status": "skipped"}; chk("drift-report: skipped stale check without reason", "drift-report.schema.json", x, False)

# ================= framework.yaml consistency (design source) =================
section('framework.yaml consistency')
import yaml
Y = yaml.safe_load(open(os.path.join(os.path.dirname(os.path.abspath(D)), 'framework.yaml'), encoding='utf-8'))
txt = open(os.path.join(os.path.dirname(os.path.abspath(D)), 'framework.yaml'), encoding='utf-8').read()
_R10 = os.path.join(os.path.dirname(os.path.abspath(D)), '..', 'context', 'rules-10.md')
RULES10 = open(_R10, encoding='utf-8').read() if os.path.exists(_R10) else ''
cond("framework.yaml: no banned word 1 (Korean, written as escapes so the word never appears in the repo)", "\ubb34\uc778" not in txt)
cond("framework.yaml: no banned word 2 (written as escapes)", "\u0075nattended" not in txt)
src_ids = {s['id'] for s in Y['sources']}
cited = set(re.findall(r'\b(S\d+)\b', txt)) & {f'S{i}' for i in range(1, 200)}
RETIRED_SRC = {'S65'}  # retired in C-059, mentioned only in retirement records
cond("framework.yaml: every cited source id is defined (retired ids excepted)", cited - RETIRED_SRC <= src_ids and not (RETIRED_SRC & src_ids))
cond("framework.yaml: source ids unique", len(src_ids) == len(Y['sources']))
ctr_schema = {c['schema'].split('/')[-1] for c in Y['contracts']}
cond("framework.yaml: every contract schema exists", all(s in S for s in ctr_schema))
cond("framework.yaml: every schema file has a contract (except probe-result via manifest, review-config via project-config)", set(S) - ctr_schema <= set())
cond("framework.yaml: no user decision left open", Y['decisions']['user'] == [])
cond("framework.yaml: U1 conditions a, c, d", [d for d in Y['decisions']['decided'] if d['id'] == 'U1'][0]['conditions'] == ["U1.a", "U1.c", "U1.d"])
cond("framework.yaml: U6 decided rubric@1", [d for d in Y['decisions']['decided'] if d['id'] == 'U6'][0]['value'] == "agentive:rubric@1")
cond("framework.yaml: stage order generate -> verify -> dedupe", Y['spec']['review_pipeline']['stage_order'] == ["RV1", "RV2", "RV3", "RV4", "RV7b", "RV5", "RV7", "RV6", "RV8"])
cond("framework.yaml: RV5 reviewer ids match the schema enum", [r['id'] for r in [s for s in Y['spec']['review_pipeline']['stages'] if s['id'] == 'RV5'][0]['reviewers']] == S['review-config.schema.json']['$defs']['reviewer_id']['enum'])
cond("framework.yaml: probes T1 and T3 only", [p['id'] for p in Y['decisions']['probes']] == ['T1', 'T3'])
removed_ids = {r['id'] for r in Y['decisions']['removed']}
live_ids = {d['id'] for k in ('accepted', 'decided', 'resolved', 'defaults', 'probes') for d in Y['decisions'][k]}
cond("framework.yaml: removed ids are not reused by live decisions", not (removed_ids & live_ids))
pv = Y['view']['pipeline']; lp = Y['layout']['pipeline']
cond("view: every pipeline node has a layout box", all(n['id'] in lp['items'] for n in pv['nodes']) and set(lp['items']) == {n['id'] for n in pv['nodes']})
cond("view: every pipeline arrow has a layout path", {a['id'] for a in pv['arrows']} == set(lp['arrows']))
sp = Y['spec']; pe = sp['work_loop']['pr_explainer']
acc = {d['id']: d for d in Y['decisions']['accepted']}
cond("framework.yaml: EXPLAIN accepted, decided_by user 2026-10-27, id not retired", acc.get('EXPLAIN', {}).get('decided_by') == 'user' and acc['EXPLAIN']['decided_at'] == '2026-10-27' and 'EXPLAIN' not in removed_ids and pe['id'] == 'EXPLAIN')
cond("framework.yaml: F3 keeps tier 0/1 and names tier 2 as the exception", 'tier 0 and tier 1' in sp['verification']['pr_guidance']['F3'] and 'tier 2' in sp['verification']['pr_guidance']['F3_tier2_exception'])
rv8 = [s for s in sp['review_pipeline']['stages'] if s['id'] == 'RV8'][0]
cond("framework.yaml: RV8 re-runs on pull_request edited and blocks with explainer_missing", 'edited' in rv8['rerun'] and any('explainer_missing' in r_ for r_ in rv8['rules']))
cond("framework.yaml: RV5 correctness-reviewer checks the explainer", 'Explainer' in [s for s in sp['review_pipeline']['stages'] if s['id'] == 'RV5'][0]['reviewers'][0]['scope'])
cond("framework.yaml: S121-S123 defined, S108-S120 left free (reserved)", {'S121', 'S122', 'S123'} <= src_ids and not ({f'S{i}' for i in range(108, 121)} & src_ids))
cond("framework.yaml: problem codes in the schema match rv8.explainer enum", XE['problems'] == S['review-report.schema.json']['properties']['rv8']['properties']['explainer']['properties']['problems']['items']['enum'])
ve = Y['view']['explainer']; le = Y['layout']['explainer']
ex_ids = [n['id'] for k in ('before', 'after') for n in ve[k]['nodes']]; ex_arr = [a['id'] for k in ('before', 'after') for a in ve[k]['arrows']]
cond("view: every explainer node and arrow has layout", set(ex_ids) == set(le['items']) and set(ex_arr) == set(le['arrows']))
cond("view: explainer has both Before and After with added, changed and removed nodes", {n['state'] for n in ve['after']['nodes']} >= {'added', 'changed', 'removed', 'same'})
mv = Y['view']['map']; lm = Y['layout']['map']
cond("view: every map item and arrow has layout", {i['id'] for i in mv['items']} == set(lm['items']) and {a['id'] for a in mv['arrows']} == set(lm['arrows']))

# --- framework.yaml / view consistency for CHAIN (2026-10-27)
ch = sp['spec_stage']['chain']
cond("framework.yaml: CHAIN accepted, decided_by user 2026-10-27, id not retired", acc.get('CHAIN', {}).get('decided_by') == 'user' and acc['CHAIN']['decided_at'] == '2026-10-27' and 'CHAIN' not in removed_ids and ch['id'] == 'CHAIN')
cond("framework.yaml: CHAIN records that it overrides SPEC and SMALLPR", any('SPEC' in o for o in acc['CHAIN']['overrides']) and any('SMALLPR' in o for o in acc['CHAIN']['overrides']) and 'CHAIN' in acc['SPEC'].get('superseded_in_part', '') and 'CHAIN' in acc['SMALLPR'].get('superseded_in_part', ''))
cond("framework.yaml: S124-S161 defined, next free id S162 not used (S155-S161 added by C-067)", {f'S{i}' for i in range(124, 162)} <= src_ids and 'S162' not in src_ids)
cond("framework.yaml: chain flow is intent -> spec -> plan -> feature", [f['stage'] for f in ch['flow']] == ['intent', 'spec', 'plan', 'feature'])
cond("framework.yaml: spec.md has no ## Intent section and links intent.md", 'no ## Intent section' in sp['spec_stage']['files']['spec'] and '`Intent: docs/plans/<slug>/intent.md`' in sp['spec_stage']['files']['spec'])
cond("framework.yaml: intent.md uses the five Academy section names", all(h in sp['spec_stage']['files']['intent'] for h in ['## Problem', '## Proposed outcome', '## Affected users and systems', '## Constraints', '## Open questions']))
cond("framework.yaml: plan.md keeps ## Decisions and ## Open questions, no 'Files that change' section", '## Decisions' in sp['spec_stage']['files']['plan'] and '## Open questions' in sp['spec_stage']['files']['plan'] and 'Files that change' not in sp['spec_stage']['files']['plan'] and 'Files that change' in sp['spec_stage']['files']['not_in_plan'])
rv2 = [s for s in sp['review_pipeline']['stages'] if s['id'] == 'RV2'][0]
cond("framework.yaml: RV2 always_human has the chain intent/spec item (chain_approval)", any('chain_approval' in a_ for a_ in rv2['always_human']))
cond("framework.yaml: RV8 blocks on chain_order", any('chain_order' in r_ for r_ in rv8['rules']))
cond("framework.yaml: RV5 correctness-reviewer checks the diff against plan.md, R<n> and intent constraints", all(k in [s for s in sp['review_pipeline']['stages'] if s['id'] == 'RV5'][0]['reviewers'][0]['scope'] for k in ['plan.md', 'R<n>', '## Constraints']))
cond("framework.yaml: AGENTS.md must contain the chain pointer line", any('Chain tasks: before working on docs/plans/<slug>/' in m_ for m_ in [f for f in sp['repo_layer']['files'] if f['id'] == 'f-agents-md'][0]['must_contain']))
PLUGIN_CMDS = ['claude_init', 'claude_reprove', 'claude_update', 'codex_init', 'codex_update']  # C-067 PRSKILLS: pr and distill moved to repo skills
cond("framework.yaml: plugin command list is init, update, reprove (no /agentive:intent|spec|plan; pr and distill are repo skills since C-067)", sorted(sp['plugin']['commands']) == sorted(PLUGIN_CMDS) and '/agentive:spec' not in txt)
cond("framework.yaml: no CI trigger opens spec PRs (G2 kept)", 'agentive ships no scheduled or event triggers that start agent work' in sp['outer_loop']['triggers'] and 'no_level3' in ch['start_check'])
cond("framework.yaml: no hook starts the next stage", 'no hook' in ch['start_check']['no_hook'])
cond("framework.yaml: spec_ci_safety_net stays removed", 'spec_ci_safety_net' in removed_ids and 'spec_ci_safety_net' in ch['ci_check']['not_a_safety_net'])
uv_ids = [u['id'] for u in Y['unverified']]
cond("framework.yaml: UV-32 (Codex /plan read-only) resolved and retired: not in unverified, not cited in spec or view", 'UV-32' not in uv_ids and 'UV-32' not in yaml.safe_dump({k: Y[k] for k in ('spec', 'view')}, allow_unicode=True))
cond("framework.yaml: fresh UV ids UV-36..UV-38 added; proposal placeholders UV-33..UV-35 unused; ids unique", {'UV-36', 'UV-37', 'UV-38'} <= set(uv_ids) and not ({'UV-33', 'UV-34', 'UV-35'} & set(uv_ids)) and len(uv_ids) == len(set(uv_ids)))
vc = Y['view']['chain']; lc = Y['layout']['chain']
cond("view: every chain node and arrow has layout", {n['id'] for n in vc['nodes']} == set(lc['items']) and {a['id'] for a in vc['arrows']} == set(lc['arrows']))
cond("view: chain diagram groups nodes into the agent, check and human regions", {z['id'] for z in vc['zones']} == set(lc['zones']) and all(any(n['zone'] == z['id'] for n in vc['nodes']) for z in vc['zones']))
cond("view: every chain node sits inside its region box", all(lc['zones'][n['zone']][0] <= lc['items'][n['id']][0] and lc['items'][n['id']][0] + lc['items'][n['id']][2] <= lc['zones'][n['zone']][0] + lc['zones'][n['zone']][2] for n in vc['nodes']))
cond("view: chain gate table has the four stages", [g['stage'] for g in vc['gates']] == ['intent', 'spec', 'plan', 'feature'])

# --- implementer evals removed, reviewer eval set kept (C-063, 2026-10-27)
rem = {r['id']: r for r in Y['decisions']['removed']}
cond("framework.yaml: implementer_evals removed by the user 2026-10-27 (id new, not live)", rem.get('implementer_evals', {}).get('decided_by') == 'user' and rem['implementer_evals']['decided_at'] == '2026-10-27' and 'implementer_evals' not in live_ids)
cond("framework.yaml: RVEVAL accepted, decided_by user 2026-10-27, id not retired", acc.get('RVEVAL', {}).get('decided_by') == 'user' and acc['RVEVAL']['decided_at'] == '2026-10-27' and 'RVEVAL' not in removed_ids)
cond("framework.yaml: spec.maintenance has no evals key (only the reviewer_eval pointer)", 'evals' not in sp['maintenance'] and 'reviewer_eval' in sp['maintenance'])
cond("framework.yaml: no implementer eval path, golden task or fixed suite outside retirement records", all(s_ not in yaml.safe_dump({k: v for k, v in Y.items() if k != 'decisions'}, allow_unicode=True) for s_ in ['evals/tasks', 'golden-task', 'golden_task', 'golden task', 'fixed suite', '고정 suite', 'evals.when']))
cond("framework.yaml: defaults card D7 retired (not live)", 'D7' not in live_ids)
cond("framework.yaml: distill classes are mechanical, judgment, knowledge, review_criteria (no eval class or home)", 'eval' not in sp['maintenance']['distill']['home'] and '`class: mechanical | judgment | knowledge | review_criteria`' in sp['maintenance']['distill']['classify'])
rve = sp['review_pipeline']['reviewer_eval']
cond("framework.yaml: reviewer eval set lives in evals/reviewer/ with schema reviewer-eval-case", rve['set'].startswith('evals/reviewer/<id>.yaml (schema reviewer-eval-case'))
cond("framework.yaml: reviewer eval rescored only on reviewer prompt/model/tool/rule change, adopted when the score improves or with an eval-override line (EVALOVERRIDE, C-067), no schedule", all(k in rve['when'] for k in ['prompt, model, tool or review rule', 'adopt the change when the score improves', '`eval-override: <reason>`', 'No daily, nightly or other scheduled run']) and 'only when the score improves' not in rve['when'])
cond("framework.yaml: reviewer eval pass rule covers positives (file:line) and negatives (no blocking finding)", 'file:line' in rve['pass'] and 'no blocking finding' in rve['pass'])
cond("framework.yaml: decided video proposals V3-V5 applied (reviewer_eval intake, scoring, ci; schema forbidden_findings)", all(k in rve for k in ['intake', 'scoring', 'ci']) and 'forbidden_findings' in S['reviewer-eval-case.schema.json']['properties'])
cond("framework.yaml: G2 outer_loop.triggers: no scheduled or event triggers that start agent work; the PR-event CI review jobs are the stated exception (F-13)", sp['outer_loop']['triggers'].startswith("agentive ships no scheduled or event triggers that start agent work") and 'ai-review and reviewer-eval' in sp['outer_loop']['triggers'])
cond("framework.yaml: contract reviewer-eval-case replaces golden-task", any(c['id'] == 'reviewer-eval-case' and c['schema'] == 'schemas/reviewer-eval-case.schema.json' for c in Y['contracts']) and not any(c['id'] == 'golden-task' for c in Y['contracts']))
rl = {f['id']: f for f in sp['repo_layer']['files']}
cond("framework.yaml: repo-layer f-reviewer-eval replaces f-evals", 'f-evals' not in rl and rl['f-reviewer-eval']['schema'] == 'reviewer-eval-case')
cond("framework.yaml: prompt-audit stays a human step without an eval rerun", 'never run automatically' in sp['instructions']['prompt_audit'] and 'evals confirm' not in sp['instructions']['prompt_audit'])
cond("framework.yaml: headless --bare ban names reviewer_eval, selftest and pr_feedback", sp['context_management']['headless']['forbid_bare_for'] == ['reviewer_eval', 'selftest', 'pr_feedback'])
cond("framework.yaml: framework self-test sample_repo_tests kept and scoped to the framework repo", [i for i in sp['framework_repo']['self_tests']['items'] if i['id'] == 'sample_repo_tests'][0]['scope'].startswith('framework repo CI'))
cond("framework.yaml: protected check-config lists evals/reviewer/** (not evals/**)", any('evals/reviewer/**' in p_ for p_ in rv2['protected_check_config']['paths']) and not any('(evals/**)' in p_ for p_ in rv2['protected_check_config']['paths']))
cond("view: map item Z6.reviewer_eval replaces Z6.evals and states the rescore rule", 'Z6.evals' not in {i['id'] for i in mv['items']} and any(i['id'] == 'Z6.reviewer_eval' and all(p_ in ' '.join(l if isinstance(l, str) else l[0] for l in i['lines']) for p_ in ['리뷰어 프롬프트·모델·도구·규칙', 'CI가 전후 3회 재채점', '오르면 채택 · 아니면 이유 줄']) for i in mv['items']))
cond("view: PM.eval states the rescore rule (CI before/after either way, adopt when the score improves or with eval-override)", all(p_ in [m for m in Y['view']['pipeline']['mapping'] if m['id'] == 'PM.eval'][0]['v'] for p_ in ['리뷰어 프롬프트·모델·도구·규칙 변경 PR에서 CI가 전후를 3회씩 재채점', '점수가 오르면', '`eval-override: <이유>`', 'forbidden_findings', 'newly_missed']))

# --- final apply C-064 (2026-10-27): every decided final_apply entry is in the design
section('final apply (C-064)')
NEW_ACC = ['PRFEEDBACK', 'INVOKE', 'REPLYLINE', 'DISTILLREVIEW', 'RVEVALRUN', 'SELFTEST', 'DENYEDIT', 'SECRETSCAN', 'HUMANJUDGE']
all_dec_ids = [d_['id'] for k in ('accepted', 'decided', 'resolved', 'defaults', 'probes', 'removed') for d_ in Y['decisions'][k]]
cond("decisions: new accepted ids exist, decided by the user 2026-10-27, unique, not retired", all(acc.get(i_, {}).get('decided_by') == 'user' and acc[i_]['decided_at'] == '2026-10-27' for i_ in NEW_ACC) and len(all_dec_ids) == len(set(all_dec_ids)) and not (set(NEW_ACC) & removed_ids))
cond("decisions: EXPLAIN and CHAIN carry the 2026-10-27 revision", acc['EXPLAIN'].get('revised_at') == '2026-10-27' and acc['CHAIN'].get('revised_at') == '2026-10-27' and 'UV-32' not in ' '.join(acc['CHAIN']['design_judgments']))
pe_en = pe['enforcement']
cond("explainer: RV8 check is presence only (3 checks, no ref resolution); refs stay in the writing rule", len(pe_en['checks']) == 3 and not any('resolve' in c_ for c_ in pe_en['checks']) and 'presence only' in pe_en['presence_only'] and 'Writing rule only' in pe['contents'][0] and any('presence only' in r_ for r_ in rv8['rules']) and not any('resolving in the head tree' in r_ for r_ in rv8['rules']))
inv = sp['plugin']['invocation']
cond("INVOKE: invocation written for every command (init/update user-only; pr/distill/reprove model-invocable)", all('disable-model-invocation: true' in inv[c_]['claude'] for c_ in ['init', 'update']) and all('disable-model-invocation: false' in inv[c_]['claude'] for c_ in ['pr', 'distill', 'reprove']) and all('allow_implicit_invocation: false' in inv[c_]['codex'] for c_ in ['init', 'update']))
cond("framework.yaml: plugin commands are init, update, reprove (no pr-feedback, no chain plugin command; pr and distill repo skills since C-067)", sorted(sp['plugin']['commands']) == sorted(PLUGIN_CMDS) and not any('feedback' in v_ or 'chain' in v_ for v_ in sp['plugin']['commands'].values()))
cond("A9: repo-layer f-chain-skills replaces f-spec-skill with three stage skills", 'f-spec-skill' not in rl and all(s_ in rl['f-chain-skills']['path'] for s_ in ['chain-intent/', 'chain-spec/', 'chain-plan/']) and 'f-spec-skill' in rl['f-chain-skills']['replaces'])
cond("A9: chain flow starts the stages with /chain-intent, /chain-spec, /chain-plan; no '/spec <stage>' left in spec_stage or view", [('/chain-' + f_['stage']) in f_['start'] for f_ in ch['flow'][:3]] == [True] * 3 and not any(s_ in yaml.safe_dump({'a': sp['spec_stage'], 'b': Y['view']}, allow_unicode=True) for s_ in ['/spec intent', '/spec spec', '/spec plan', '$spec ', 'skills/spec/']))
cond("A9: pr-body x-agentive-chain next_actions name /chain-spec and /chain-plan, no retired '/spec <stage>' (integration fix 2026-10-28)", not any(s_ in json.dumps(S['pr-body-agentive-review.schema.json'], ensure_ascii=False) for s_ in ['/spec intent', '/spec spec', '/spec plan']) and '/chain-spec <slug>' in json.dumps(S['pr-body-agentive-review.schema.json']))
cond("A9: commands text corrected (server-managed plugins, Claude project cloud threads, Codex plugin skills)", all(k_ in ch['commands'] for k_ in ['server-managed', 'cloud threads of a Claude project', 'no commands [S15]', 'UV-23']))
cond("A9_fallback: stage-check reads origin/HEAD, falls back to git ls-remote --symref origin HEAD", 'git ls-remote --symref origin HEAD' in ch['start_check']['default_branch'] and 'git ls-remote --symref origin HEAD' in ch['start_check']['does'])
cond("A8: spec PR merge = acceptance, closing = rejection, marked as an extension of the intent rule (design judgment)", 'closing = rejection' in ch['flow'][1]['approval'] and '[S124]' in ch['flow'][1]['approval'] and '[설계 판단]' in ch['flow'][1]['approval'])
cond("A10: Codex plan mode read-only sourced by S130 (template) and S131 (Prompting); instruction-level vs tool restriction", all(k_ in ch['flow'][2]['writer'] for k_ in ['[S130]', '[S131]', 'by instruction', 'tool restriction']))
cond("A7: R<n> kept and no lint (no agentive-lint spec subcommand)", 'spec' not in sp['repo_checks']['agentive_lint']['subcommands'] and 'R1, R2' in sp['spec_stage']['files']['spec'] and 'no deterministic R<n> lint' in sp['spec_stage']['files']['spec'])
cond("B1: plan.md has ## Risks and the PR ## Risk is filled from it", '## Risks' in sp['spec_stage']['files']['plan'] and 'plan.md ## Risks' in sp['work_loop']['pr_skill']['writes'] and 'Risks and Proof sections are not added' not in sp['spec_stage']['files']['not_in_plan'])
cond("B2_final: normative-conflict rule only (no Source line, no lint)", 'never resolved silently' in sp['spec_stage']['normative_conflicts']['rule'] and 'design judgment' in sp['spec_stage']['normative_conflicts']['scope'] and 'No `Source:` line' in sp['spec_stage']['files']['spec'])
pf = sp['work_loop']['pr_feedback']
cond("B3: /pr-feedback is a repo skill, one pass, never merges, untrusted text, human threads stay open", 'f-pr-feedback' in rl and pf['id'] == 'PRFEEDBACK' and 'one call = one pass' in pf['one_pass'] and any('merge' in n_ for n_ in pf['never']) and 'untrusted' in pf['untrusted'] and any('needs-human' in s_ for s_ in pf['steps']) and 'existing_pr' in sp['work_loop']['pr_skill'])
cond("B4: /pr-feedback is model-invocable and recorded as a skill-lint exception", pf['invocation'].startswith('model-invocable') and 'pr-feedback' in sp['repo_checks']['agentive_lint']['subcommands']['skills'] and 'model-invocable' in rl['f-pr-feedback']['invocation'])
de = sp['enforcement']['permissions']['deny_edit']
cond("B5: Edit deny on generated/vendor paths (Write covered by Edit rules; no Write(path) rule)", de['rules'] == ['Edit(<generated|vendor dir>/**)'] and 'Write' in de['write_covered'] and not any(r_.startswith('Write(') for r_ in de['rules']))
cond("B6: secret scan: no write hook, push-protection line, existing scanner wired into verify", sp['enforcement']['secret_scan']['write_hook'].startswith('none') and 'push protection' in sp['enforcement']['secret_scan']['init_report'] and 'verify verb' in sp['enforcement']['secret_scan']['wire_existing'])
cond("B7: reply line regex in the design equals the parser; RV8 human_required on finding_disputed; gate re-runs on pull_request_review_comment", RLY['regex'] == RE_RL.pattern and any('finding_disputed' in r_ for r_ in rv8['rules']) and 'pull_request_review_comment' in rv8['rerun'] and 'pull_request_review_comment' in rl['f-ci']['triggers'])
dh = sp['maintenance']['distill']
cond("B8: distill triggers review_noise and review_escape; escape follows the Claude Code team (no ## Always check line); 3 guardrails", any(t_.startswith('review_noise') for t_ in dh['triggers']) and any(t_.startswith('review_escape') for t_ in dh['triggers']) and 'never a code_review.md `## Always check` line' in dh['home']['review_escape'] and len(dh['home']['review_noise']['guardrails']) == 3)
cond("B9-B11: reviewer eval intake, scoring (location match, newly_missed), negatives with forbidden_findings", 'review_escape' in rve['intake']['positives'] and 'forbidden_findings' in rve['intake']['negatives'] and 'newly_missed' in rve['scoring'] and 'summary' in S['reviewer-eval-case.schema.json']['$defs'] and 'fix_pr' not in S['reviewer-eval-case.schema.json']['properties'])
cond("A12: positive origin options and a required human-confirmed location", set(S['reviewer-eval-case.schema.json']['properties']['origin']['properties']['kind']['enum']) == {'fix_pr', 'revert', 'reproduction_diff', 'injected_bug'} and 'human merge' in rve['confirmation'])
cond("A13: CI (not a human) posts before/after with N = 3 runs and the improvement rule; human merges; no schedule", 'CI, not a human' in rve['ci']['does'] and 'N = 3' in rve['ci']['n_runs'] and 'run-to-run noise' in rve['ci']['improves'] and 'reviewer-eval' in rl['f-ci']['jobs'] and 'no schedule' in rve['ci']['no_schedule'])
sr = [i_ for i_ in sp['framework_repo']['self_tests']['items'] if i_['id'] == 'sample_repo_tests'][0]
cond("A11: framework self-test narrowed (deterministic on every PR for 4 stacks; agent fixture tasks on init/apply path changes, 1-2 stacks)", 'every framework-repo PR' in sr['deterministic'] and 'all 4 sample stacks' in sr['deterministic'] and '1-2 stacks' in sr['agent_run'])
rv4 = [s_ for s_ in sp['review_pipeline']['stages'] if s_['id'] == 'RV4'][0]
cond("B12: RV4 raise-only question carries the S89 human-judgment list", all(k_ in rv4['raise_only_check']['asks'] for k_ in ['whether or how far to build', 'billing, payment, deploy', 'others will copy']))
cond("B13: RV2 default globs add **/billing/** and **/payment*/** (reason billing)", {'**/billing/**', '**/payment*/**'} <= {g_['glob'] for g_ in rv2['default_globs'] if g_['reason'] == 'billing'})
cond("B14 skipped: no 'Not covered:' line in ## Verification", 'Not covered' not in sp['verification']['E1a'])
cond("view: chain and pipeline show the new names and rules", any(n_['id'] == 'CH.intent' and '/chain-intent <slug>' in str(n_['lines']) for n_ in vc['nodes']) and any(l_['id'] in ('LC.feedback', 'LC.reply') for l_ in Y['view']['loop_contract']) and any(m_['id'] == 'PM.distill' for m_ in Y['view']['pipeline']['mapping']))

# --- INTENTENTRY (2026-10-27, C-065): chain-entry triage name, /chain-intent interview, two-way user override
tri = sp['spec_stage']['triage']; itv = sp['spec_stage']['chain']['interview']; uo = tri['user_override']
cond("INTENTENTRY: accepted, decided_by user 2026-10-27, id not retired; CHAIN amended", acc.get('INTENTENTRY', {}).get('decided_by') == 'user' and acc['INTENTENTRY']['decided_at'] == '2026-10-27' and 'INTENTENTRY' not in removed_ids and 'INTENTENTRY' in acc['CHAIN'].get('amended', ''))
cond("INTENTENTRY: triage named 'intent.md 필요? (chain 진입 판단)', old keys renamed, criteria (a)-(d) unchanged and marked design judgment", tri['name'] == 'intent.md 필요? (chain 진입 판단)' and 'needs_spec_when' not in tri and 'spec_needed_means' not in tri and len(tri['needs_intent_when']) == 4 and tri['criteria_status'].startswith('[설계 판단]') and 'well-bounded fix' in tri['criteria_status'])
cond("INTENTENTRY: yes -> whole chain, no -> implement directly, no spec-only branch", 'no spec-only branch' in tri['intent_needed_means'] and 'implement directly' in tri['intent_needed_means'] and 'No spec-only branch' in sp['spec_stage']['chain']['scope'])
cond("INTENTENTRY: interview one question per turn with a recommended answer, high impact first, no question cap (C-066), stop on five sections or user stop, leftovers to Open questions", all(k_ in itv['rules']['one_per_turn'] for k_ in ['one question per turn', 'recommended answer', 'highest impact first']) and itv['rules']['cap'].startswith('none') and 'at most 5' not in str(itv) and '최대 5' not in str(Y['view']) and not any(k_ in yaml.safe_dump(sp['spec_stage']['chain'], allow_unicode=True) for k_ in ['cap 5', 'at most 5', 'maximum of 5 questions']) and '최대 5' not in re.sub(r'원래:[^)]*\)', '', RULES10) and 'the user says stop' in itv['rules']['stop'] and '## Open questions' in itv['rules']['leftovers'] and 'S148' not in itv['tool'])
cond("INTENTENTRY: two-way override; skip on (d) -> warning + PR body line; gates unchanged; headless/Routines no interview; U2.non_interactive kept", 'both directions' in uo['rule'] and '`intent 생략: 사용자 지시`' in uo['skip_hard_to_reverse'] and all(k_ in uo['unchanged'] for k_ in ['guard deny/ask', 'hard gates', 'tier 2', 'human approval']) and 'no interview' in uo['headless'] and 'U2.non_interactive (pr_only_tier2) still applies' in uo['headless'] and 'intent 생략: 사용자 지시' in sp['work_loop']['pr_skill']['writes'])
cond("INTENTENTRY: view shows the new label (no 'spec 필요' left in view), the interview rule and the layout fits", 'spec 필요' not in str(Y['view']) and 'spec이 필요' not in str(Y['view']) and [n_ for n_ in vc['nodes'] if n_['id'] == 'CH.triage'][0]['title'] == 'intent.md 필요? (chain 진입 판단)' and [a_ for a_ in vc['arrows'] if a_['id'] == 'CH.a-intent'][0]['label'] == ['yes · intent.md 필요'] and [a_ for a_ in vc['arrows'] if a_['id'] == 'CH.a-small'][0]['label'] == ['no'] and any('**intent 인터뷰**' in x_ for x_ in vc['notes']) and any('**사용자 지시 우선 (양방향)**' in x_ for x_ in vc['notes']))

# --- C3 follow-up C-067 (2026-10-27): c3_followup D1-D5 + fix_without_decision
section('C3 follow-up (C-067)')
NEW67 = ['PRSKILLS', 'VERIFIERCTX', 'EVALOVERRIDE', 'COPILOTAPPROVAL']
all_dec_ids = [d_['id'] for k in ('accepted', 'decided', 'resolved', 'defaults', 'probes', 'removed') for d_ in Y['decisions'][k]]
cond("C-067: new accepted ids exist, decided by the user 2026-10-27, unique, not retired", all(acc.get(i_, {}).get('decided_by') == 'user' and acc[i_]['decided_at'] == '2026-10-27' for i_ in NEW67) and len(all_dec_ids) == len(set(all_dec_ids)) and not (set(NEW67) & removed_ids))
cond("C-067: amendments recorded (NOHUMAN revised, RVEVAL, RVEVALRUN, BIGTECH, INVOKE, PRFEEDBACK amended)", 'N-review D6' in acc['NOHUMAN'].get('revised', '') and all('2026-10-27' in acc[i_].get('amended', '') for i_ in ['RVEVAL', 'RVEVALRUN', 'BIGTECH', 'INVOKE', 'PRFEEDBACK']) and 'P6_P7' in acc['RVEVAL']['amended'])
rl = {f['id']: f for f in sp['repo_layer']['files']}
# D1
cond("D1: /pr and /distill are repo skills (f-pr, f-distill), gone from the plugin commands, recorded in removed_commands", all(k_ in rl for k_ in ['f-pr', 'f-distill']) and 'claude_pr' not in sp['plugin']['commands'] and 'claude_distill' not in sp['plugin']['commands'] and {'claude_pr', 'claude_distill'} <= set(sp['plugin']['removed_commands']))
_sp67 = copy.deepcopy(sp); _sp67['plugin'].pop('removed_commands'); _sp67['work_loop']['pr_feedback'].pop('replaces')
cond("D1: no /agentive:pr or /agentive:distill outside retirement records (spec, contracts, view)", not any(s_ in yaml.safe_dump({'s': _sp67, 'c': Y['contracts'], 'v': Y['view']}, allow_unicode=True) for s_ in ['/agentive:pr', '/agentive:distill']) and '/agentive:distill (moved' in txt)
cond("D1: invocation kept model-invocable for pr and distill (Claude and Codex written explicitly); one exception list incl. precommit-review, verify, janitor", all('disable-model-invocation: false' in inv[c_]['claude'] and 'allow_implicit_invocation: true' in inv[c_]['codex'] for c_ in ['pr', 'distill']) and all(k_ in inv['repo_skills'] for k_ in ['pr and distill', 'precommit-review', 'verify', 'janitor']) and all(k_ in sp['repo_checks']['agentive_lint']['subcommands']['skills'] for k_ in ['pr and distill', 'precommit-review, verify and janitor']))
cond("D1: cloud_sessions says every agent command in cloud/Routines is a repo skill; chain PRs opened by /pr; writer is the repo skill", 'is a repo skill: /pr, /distill, /pr-feedback' in sp['distribution']['cloud_sessions'] and 'repo skill /pr' in ch['commands'] and 'repo skill /pr' in sp['work_loop']['pr_explainer']['writer'] and sp['work_loop']['pr_skill']['cmd'] == '/pr')
cond("D1: no built-in clash recorded (S139 re-read); init writes the pr and distill skills", 'no built-in command or bundled skill is named /pr or /distill' in [s_ for s_ in Y['sources'] if s_['id'] == 'S139'][0]['title'] and 'pr, distill and pr-feedback skills' in sp['init_apply']['empty_dir']['writes'])
# D2
rv7 = [s_ for s_ in sp['review_pipeline']['stages'] if s_['id'] == 'RV7'][0]
cond("D2: RV7 drops the different-model requirement (fresh context, model: inherit); reviewer_selection local only, CI constant", 'different model is not required' in rv7['does'] and 'on a different model' not in rv7['does'] and 'local review only' in sp['work_loop']['reviewer_selection']['applies_to'] and 'CI always runs claude-code-action' in sp['work_loop']['reviewer_selection']['applies_to'])
cond("D2: view no longer says a different-model verifier (RV7 caption, C.RV7, RV7 row)", '다른 model의 verifier' not in str(Y['view']) and '다른 model verifier' not in str(Y['view']) and '다른 model의 fresh verifier' not in str(Y['view']))
cond("D2/ext: S91 no longer cited for verify/dedupe/rank (S16 instead)", '[S89][S91]' not in txt and '{S91}{S89}' not in txt)
# D3
ad = rve['adopt']
cond("D3: adoption = improves OR human merge with eval-override line; CI posts either way; reverses the strict part of P6_P7; line not a schema field", ad['id'] == 'EVALOVERRIDE' and '`eval-override: <reason>`' in ad['rule'] and 'either way' in ad['rule'] and 'P6_P7' in ad['reverses'] and 'not a schema field' in ad['line'] and 'eval-override' in S['reviewer-eval-case.schema.json']['description'] and 'only when it improves' not in S['reviewer-eval-case.schema.json']['description'])
cond("D3: newly_missed sets adopt_suggested false (human may still adopt); CI comment line on false", 'adopt_suggested false' in rve['scoring']['newly_missed'] and 'eval-override' in rve['ci']['merge'])
cond("D3/ext: official deviations recorded (no daily reviewer eval; implementer evals removed against the playbook recommendation)", 'differs from the official sources' in rve['when'] and '공식 권고와 다른 사용자 결정' in rem['implementer_evals']['reason'] and '공식 자료와 다른 사용자 결정' in acc['RVEVAL']['official'])
# D4
mt = sp['work_loop']['merge']['tier2']
cond("D4: GitHub fact corrected (Copilot approvals count when the setting is on), init guidance, no runtime check; S155 cited", 'GitHub counts human approvals only, so' not in mt and "Allow Copilot approvals to count toward merge requirements" in mt and 'no runtime approver check' in mt and '[S155]' in mt and 'Copilot approvals to count toward merge requirements' in sp['init_apply']['report'] and '사람 승인 자리를 채우지 않습니다' not in str(Y['view']))
# D5
nh = sp['no_human_run']
cond("D5: no_human_run = AGENTIVE_HEADLESS=1 OR CI/GITHUB_ACTIONS OR dontAsk where carried; CLAUDE_CODE_REMOTE not a signal; ambiguous = interactive; reverses N-review D6", all(k_ in nh['rule'] for k_ in ['AGENTIVE_HEADLESS=1', 'CI or GITHUB_ACTIONS', 'permission_mode is dontAsk', 'SessionStart does not', 'means a human is present', 'CLAUDE_CODE_REMOTE is not a signal']) and 'D6' in nh['revised']['reverses'] and 'interactive' in nh['ambiguous'])
cond("D5: every user of the definition updated (SessionStart, headless init/update, U2.non_interactive runs, standard command, janitor, view, schemas)", 'CLAUDE_CODE_REMOTE=true)' not in sp['init_apply']['session_start']['non_interactive'] and 'AGENTIVE_HEADLESS' in sp['init_apply']['session_start']['non_interactive'] and 'AGENTIVE_HEADLESS' in sp['init_apply']['headless_init_update']['detection'] and sp['context_management']['headless']['standard_cmd'].startswith('AGENTIVE_HEADLESS=1 claude -p') and 'AGENTIVE_HEADLESS=1' in sp['maintenance']['janitor']['run'] and not any('CLAUDE_CODE_REMOTE=true' in json.dumps(S[n_], ensure_ascii=False) for n_ in S) and 'CLAUDE_CODE_REMOTE=true`이면' not in str(Y['view']) and 'dontAsk · CLAUDE_CODE_REMOTE' not in str(Y['view']))
cond("D5: U2 human_confirmation rule unchanged; Routines wording re-verified (waits on ask rules, no 'without stopping for approval')", 'obtained only when a human answered the U2 confirmation in the run that made the change' in rv2['confirm_scope']['human_confirmation'] and 'stops and waits for an answer' in nh['routines'] and 'without stopping for approval' not in [s_ for s_ in Y['sources'] if s_['id'] == 'S75'][0]['title'])
# sources and UVs
cond("C-067: sources S155-S161 opened 2026-10-27; re-read sources carry the new fetched_at", all([s_ for s_ in Y['sources'] if s_['id'] == f'S{i}'][0]['fetched_at'] == '2026-10-27' for i in list(range(155, 162)) + [2, 3, 6, 7, 16, 24, 33, 39, 75, 76, 81, 84, 94]))
cond("C-067: UV-5, UV-14, UV-29 resolved and retired (not in unverified, not cited); UV-39, UV-40 new; ids unique", not ({'UV-5', 'UV-14', 'UV-29'} & set(uv_ids)) and {'UV-39', 'UV-40'} <= set(uv_ids) and len(uv_ids) == len(set(uv_ids)) and not re.search(r'\bUV-(5|14|29)\b', yaml.safe_dump({k: Y[k] for k in ('spec', 'view', 'unverified')}, allow_unicode=True)))
cond("C-067: UV-12 rescoped to the new signals; UV-1 partly resolved; UV-10/15 cite S81; UV-21 re-checked", all(k_ in [u_ for u_ in Y['unverified'] if u_['id'] == 'UV-12'][0]['claim'] for k_ in ['AGENTIVE_HEADLESS=1']) and '부분 해소' in [u_ for u_ in Y['unverified'] if u_['id'] == 'UV-1'][0]['why'] and all('{S81}' in [u_ for u_ in Y['unverified'] if u_['id'] == i_][0]['why'] for i_ in ['UV-10', 'UV-15']) and '2026-10-27 다시 확인' in [u_ for u_ in Y['unverified'] if u_['id'] == 'UV-21'][0]['why'])
# fix_without_decision
cond("F-05: reviewer prompt/role files and pipeline script defined; reviewer-change paths listed; CI prompt uses the repo reviewers", 'f-reviewers' in rl and '.agents/bin/agentive-review' in rl['f-reviewers']['scripts'] and '.agents/agents/**' in rve['reviewer_paths'] and 'not_used' in sp['work_loop']['ci_ai_review_floor']['claude'] and 'f-reviewers' in sp['work_loop']['ci_ai_review_floor']['claude']['prompt'])
cond("F-08: RV8 does not re-run on pull_request_review (spec, view, schema enum)", 'not on pull_request_review' in rv8['rerun'] and 'pull_request_review submitted' not in rv8['rerun'] and 'pull_request_review' not in S['review-report.schema.json']['properties']['rv8']['properties']['explainer']['properties']['trigger']['enum'] and "`pull_request_review` ·" not in str(Y['view']))
cond("F-11: harvest distill is a local session only (no CI job)", 'or a CI job' not in sp['maintenance']['harvest']['distill'] and 'no CI job can read it' in sp['maintenance']['harvest']['distill'])
cond("F-12: N-review references prefixed (no '(D6)', '(D8 2026-10-27)', 'D7 #', RV5 decided id N-D1)", '(D8 2026-10-27)' not in txt and 'D6)' not in txt.replace('N-D6', '') and 'D7 #' not in txt.replace('N-D7 #', '') and [s_ for s_ in sp['review_pipeline']['stages'] if s_['id'] == 'RV5'][0]['decided']['id'] == 'N-D1' and 'N-D1..N-D8' in Y['meta']['id_policy'])
cond("F-14: intent stage in a no-human run follows triage.user_override.headless (no handoff)", 'triage.user_override.headless wins' in sp['spec_stage']['insufficient_spec']['intent_stage'])
cond("F-15: no undefined names: derive-<slot> are internal steps, coding-standards path, no build dir, verbs.*.evidence.files", 'not separately invocable skills' in sp['init_apply']['contract']['derive_skills'] and 'docs/coding-standards.md' in sp['maintenance']['distill']['home']['judgment'] and 'build dir' not in txt and 'agentive manifest evidence.files' not in txt and 'derive-* skills' not in str(Y['view']))
cond("F-16: header comments in C-065 -> C-066 -> C-067 order", txt.index('# 2026-10-27 (C-065') < txt.index('# 2026-10-27 (C-066)') < txt.index('# 2026-10-27 (C-067'))
cond("F-17/F-18/F-19/F-20: EXPLAIN rule presence only; tally run by a human; intake origin note; config.default matches the schema", '존재만' in acc['EXPLAIN']['rule'] and '없거나 틀리면' not in acc['EXPLAIN']['rule'] and 'a human runs when needed' in rve['tally'] and 'reproduction_diff, injected_bug' in rve['intake']['positives'] and all(k_ in sp['review_pipeline']['config']['default'] for k_ in ['exclude_globs', 'shadow_reviewers', 'coverage', 'rules_file', 'criteria']))
cond("F-21/F-25: S148 kept for the record; Edit(~/.ssh/**) moved out of deny_read", 'kept for the record' in [s_ for s_ in Y['sources'] if s_['id'] == 'S148'][0]['title'] and 'Edit(~/.ssh/**)' not in sp['enforcement']['permissions']['deny_read'] and sp['enforcement']['permissions']['deny_edit_credentials'] == ['Edit(~/.ssh/**)'])
cond("F-22/F-07/F-05: rules-10.md stale lines corrected with dated markers (C-067)", RULES10 == '' or (RULES10.count('C-067') >= 6 and '→ evals로 확인. Codex' not in RULES10 and '.claude/agents/reviewer.md`)가' not in RULES10))
cond("ext: S39, deny-rule Bash coverage, plan-mode bypass exception, GitHub Pro note, S124 no-design marked design judgment", 'approval-required' in sp['work_loop']['ci_ai_review_floor']['pr_author'] and 'sed, tee' in sp['enforcement']['permissions']['deny_edit']['not_covered'] and 'bypass permissions available' in ch['flow'][2]['writer'] and 'GitHub Pro' in sp['repo_layer']['github_plan_note'] and '[설계 판단' in ch['interview']['rules']['no_design'] and '[설계 판단' in sp['spec_stage']['files']['intent'])
# schema fixtures for C-067 changes
GR = S['review-report.schema.json']['$defs']['gate_reason']['enum']
cond("F-24: gate_reason enum lists every RV8 reason the design names", all(r_ in GR for r_ in ['U2.non_interactive', 'shadow', 'explainer_missing', 'chain_order', 'chain_approval', 'finding_disputed', 'review_rounds_exceeded', 'pending_verbs', 'llm_raise', 'critical_cve_unchecked', 'coverage_unavailable']))
x = copy.deepcopy(sh); x['rv8']['reason_codes'] = ['shadow']; chk("review-report: rv8.reason_codes [shadow] (F-24)", "review-report.schema.json", x, True)
x = copy.deepcopy(sh); x['rv8']['reason_codes'] = ['shadow_mode']; chk("review-report: unknown rv8 reason code (F-24)", "review-report.schema.json", x, False)
x = copy.deepcopy(sh); x['rv8']['reason_codes'] = ['shadow', 'shadow']; chk("review-report: duplicate rv8 reason code", "review-report.schema.json", x, False)
x = copy.deepcopy(t2); x['rv8']['explainer']['trigger'] = 'pull_request_review'; chk("review-report: gate run by pull_request_review (retired trigger, F-08)", "review-report.schema.json", x, False)

print('ALL', 'OK' if all(RESULTS) else 'PROBLEMS', sum(RESULTS), '/', len(RESULTS))
sys.exit(0 if all(RESULTS) else 1)
