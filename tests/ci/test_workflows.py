# agentive-unit: selftest.component
# design: spec.repo_layer.files[f-ci], spec.review_pipeline.stages[RV8].rerun, spec.review_pipeline.gate_protection, spec.outer_loop.triggers, spec.review_pipeline.reviewer_eval.ci, spec.framework_repo.self_tests
"""The user-repo workflow templates (src/repo-layer/dot-github/workflows/) and the framework repo's own workflows:
triggers, job names, no schedule, the gate never skipped or path-filtered, actions the design names."""
import os
import re
import sys
import unittest

import yaml  # dev-time only

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "src", "repo-layer", "dot-agents", "lib"))
sys.path.insert(0, os.path.join(ROOT, "src", "engine"))
from agentive import ayaml  # noqa: E402
import apply as engine_apply  # noqa: E402  (CI_TOOLS, ci_llm_step_text: what the engine renders into the workflows)
import cisetup as engine_cisetup  # noqa: E402
import render as engine_render  # noqa: E402

WF = os.path.join(ROOT, "src", "repo-layer", "dot-github", "workflows")
DESIGN_JOBS = {"outer-verbs", "ai-review", "agentive-independence", "agentive-review-gate", "agentive-lint", "dependency-review", "reviewer-eval"}
BANNED = r"\bun" + r"attended\b|" + "\ubb34\uc778"      # AGENTS.md Words; spelled so this file does not match itself
AGENT_TRIGGERS = {"schedule", "workflow_run", "issues", "issue_comment", "repository_dispatch", "workflow_dispatch", "pull_request_target"}


TOOLS = ("claude", "codex")
OAUTH_FIRST = "${{ secrets.CLAUDE_CODE_OAUTH_TOKEN == '' && secrets.ANTHROPIC_API_KEY || '' }}"   # E2E-12 (OAuth token first)


def load(path, tool=None, setup_root=None, ecosystems=("node",), auth=None):
    """(text, parsed). The user-repo templates hold install-time placeholders (CITOOL, CIAUTH): they are rendered for the CI tool
    first, exactly as the engine does at init and update (tests/engine checks the engine end to end)."""
    with open(path, encoding="utf-8") as f:
        text = f.read()
    if tool:
        ci = engine_apply.CI_TOOLS[tool]
        blocks = {"render.ci_tool": tool, "render.ci_llm_step": engine_apply.ci_llm_step_text(tool, auth),
                  "render.ci_cli_name": ci["name"], "render.ci_cli_package": ci["package"],
                  "render.ci_setup_steps": engine_cisetup.render(setup_root or os.path.join(ROOT, "fixtures", "ts"), list(ecosystems)),
                  "render.ci_id_token": "id-token: write" if tool == "claude" else ""}
        blocks.update(engine_apply.ci_secret_blocks(tool, auth))
        text = engine_render.render(path, text, {"blocks": blocks})
    return text, ayaml.loads(text)


class UserWorkflowsTest(unittest.TestCase):
    def setUp(self):
        self.by_tool = {t: {f: load(os.path.join(WF, f), t) for f in sorted(os.listdir(WF))} for t in TOOLS}
        self.files = self.by_tool["claude"]

    def test_parse_identically_with_ayaml_and_pyyaml(self):
        for tool, files in self.by_tool.items():
            for name, (text, doc) in files.items():
                self.assertEqual(doc, yaml.safe_load(text), (tool, name))       # the engine merges .agents/custom overrides with ayaml
                self.assertIn("agentive-unit: ", text.splitlines()[0])

    def test_jobs_are_the_design_jobs(self):
        for files in self.by_tool.values():
            jobs = {j for _, d in files.values() for j in d["jobs"]}
            self.assertEqual(jobs, DESIGN_JOBS)

    def test_both_tools_render_the_same_job_structure(self):
        """CITOOL: only the LLM step (and the reviewer-eval install and secret lines) differ between the two tools."""
        a, b = (self.by_tool[t]["agentive-review.yml"][1] for t in TOOLS)
        self.assertEqual(a["jobs"].keys(), b["jobs"].keys())
        for job in a["jobs"]:
            for key in set(a["jobs"][job]) | set(b["jobs"][job]):
                if key == "steps":
                    continue
                if (job, key) == ("ai-review", "permissions"):      # CISPLIT: id-token: write only on the claude-code-action path
                    self.assertEqual({k: v for k, v in a["jobs"][job][key].items() if k != "id-token"}, b["jobs"][job][key])
                    continue
                self.assertEqual(a["jobs"][job].get(key), b["jobs"][job].get(key), (job, key))
        sa, sb = a["jobs"]["ai-review"]["steps"], b["jobs"]["ai-review"]["steps"]
        self.assertEqual([x.get("name") or x.get("id") for x in sa], [x.get("name") or x.get("id") for x in sb])
        diff = [i for i in range(len(sa)) if sa[i] != sb[i]]
        # the LLM step itself, plus (E2E-42 / E2E-43) the two steps that pass the credential secret check, which names the tool's secrets
        self.assertEqual([sa[i]["id"] for i in diff], ["llm", "llm-state", "finish"])
        self.assertEqual(sa[diff[0]]["name"], "LLM stages with the repo reviewer subagents")
        for i in diff[1:]:
            ea, eb = dict(sa[i]["env"]), dict(sb[i]["env"])
            self.assertNotEqual(ea.pop("AGENTIVE_CI_SECRET_SET"), eb.pop("AGENTIVE_CI_SECRET_SET"))
            self.assertEqual(ea, eb)
            self.assertEqual({k: v for k, v in sa[i].items() if k != "env"}, {k: v for k, v in sb[i].items() if k != "env"})
        self.assertEqual(a["jobs"]["agentive-review-gate"], b["jobs"]["agentive-review-gate"])
        for t in TOOLS:   # no tool-neutral job names a secret
            for job in ("outer-verbs", "dependency-review", "agentive-independence", "agentive-review-gate"):
                dump = str(self.by_tool[t]["agentive-review.yml"][1]["jobs"][job])
                self.assertNotIn("API_KEY", dump)

    def test_no_trigger_starts_agent_work(self):
        for name, (_, doc) in self.files.items():
            self.assertFalse(set(doc["on"]) & AGENT_TRIGGERS, name)   # G2: PR events only, never a schedule
            for ev in doc["on"].values() if isinstance(doc["on"], dict) else []:
                self.assertFalse(isinstance(ev, dict) and ("paths" in ev or "paths-ignore" in ev), name)

    def test_gate_triggers_and_never_skipped(self):
        _, doc = self.files["agentive-review.yml"]
        on = doc["on"]
        self.assertEqual(on["pull_request"]["types"], ["opened", "synchronize", "reopened", "edited", "labeled"])
        self.assertEqual(on["pull_request_review_comment"]["types"], ["created", "edited", "deleted"])
        self.assertIn("merge_group", on)
        self.assertNotIn("pull_request_review", on)
        gate = doc["jobs"]["agentive-review-gate"]
        self.assertEqual(gate["if"], "always()")
        self.assertEqual(set(gate["needs"]), {"outer-verbs", "dependency-review", "ai-review", "agentive-independence"})
        self.assertEqual(gate["permissions"]["issues"], "write")         # demotion issue
        self.assertEqual(gate["permissions"]["pull-requests"], "write")  # body, labels, auto-merge
        run = "\n".join(s.get("run", "") for s in gate["steps"])
        self.assertIn('git worktree add --detach "$RUNNER_TEMP/agentive-base" "$BASE_SHA"', run)   # gate script from the base
        self.assertIn("gate \"$PR_NUMBER\"", run)
        self.assertIn("--apply", run)

    # ---- LABELTRIGGER (2026-10-29): label events and the allowlist
    @staticmethod
    def holds(expr, event_name="pull_request", action="opened", label=None):
        """Evaluate a job `if` of the review workflow for one event (the GitHub expression subset the template uses)."""
        py = expr.replace("&&", " and ").replace("||", " or ").replace("always()", "True")
        py = py.replace("github.event_name", "EVENT").replace("github.event.action", "ACTION").replace("github.event.label.name", "LABEL")
        env = {"EVENT": event_name, "ACTION": action, "LABEL": label or "", "startsWith": lambda x, y: str(x).startswith(str(y))}
        return bool(eval(py, {"__builtins__": {}}, env))      # noqa: S307  (a fixed template string, never user input)

    def test_label_events_use_an_exact_allowlist_and_never_skip_the_gate(self):
        _, doc = self.files["agentive-review.yml"]
        jobs = doc["jobs"]
        names = ["outer-verbs", "dependency-review", "ai-review", "agentive-independence", "agentive-review-gate"]
        run = {"allowed": ("labeled", "agentive:full-review"), "janitor": ("labeled", "agentive-janitor:lint-fixes")}
        other = {"unrelated": ("labeled", "bug"), "gate's own label": ("labeled", "agentive:shadow-auto-merge"),
                 "prefix lookalike": ("labeled", "agentive-janitorx"), "full-review lookalike": ("labeled", "agentive:full-review-later"),
                 "no label name": ("labeled", None)}
        for why, (act, lab) in run.items():
            got = {n: self.holds(jobs[n]["if"], action=act, label=lab) for n in names}
            self.assertEqual(got, {"outer-verbs": False, "dependency-review": False, "ai-review": True, "agentive-independence": True,
                                   "agentive-review-gate": True}, why)
        for why, (act, lab) in other.items():
            got = {n: self.holds(jobs[n]["if"], action=act, label=lab) for n in names}
            self.assertEqual(got, {"outer-verbs": False, "dependency-review": False, "ai-review": False, "agentive-independence": False,
                                   "agentive-review-gate": True}, why)
        for act in ("opened", "synchronize", "reopened"):
            self.assertTrue(all(self.holds(jobs[n]["if"], action=act) for n in names), act)
        edited = {n: self.holds(jobs[n]["if"], action="edited") for n in names}
        self.assertEqual(edited, {"outer-verbs": False, "dependency-review": False, "ai-review": True, "agentive-independence": True, "agentive-review-gate": True})
        mq = {n: self.holds(jobs[n]["if"], event_name="merge_group", action="") for n in names}
        self.assertEqual(mq, {"outer-verbs": True, "dependency-review": False, "ai-review": False, "agentive-independence": False, "agentive-review-gate": True})
        rc = {n: self.holds(jobs[n]["if"], event_name="pull_request_review_comment", action="created") for n in names}
        self.assertEqual((rc["ai-review"], rc["agentive-review-gate"]), (False, True))
        # the gate has no label condition at all (a skipped required check counts as success) and unlabeled is not a trigger
        self.assertEqual(jobs["agentive-review-gate"]["if"], "always()")
        self.assertNotIn("unlabeled", doc["on"]["pull_request"]["types"])

    def test_a_reply_run_looks_the_review_artifact_up_by_name_instead_of_in_its_own_run(self):
        """E2E-61: a pull_request_review_comment run has no ai-review job, so download-artifact (its own run only) always failed with
        "Artifact not found"; the independence command and the gate's --fetch-prior fetch the push run's artifact and wait for it."""
        text, doc = self.files["agentive-review.yml"]
        jobs = doc["jobs"]
        for job in ("agentive-independence", "agentive-review-gate"):
            step = next(st for st in jobs[job]["steps"] if st.get("id") == "download")
            self.assertTrue(step["uses"].startswith("actions/download-artifact@v4"))
            ev = lambda name: self.holds(step.get("if", "True"), event_name=name, action="synchronize" if name == "pull_request" else "created")   # noqa: E731
            self.assertTrue(ev("pull_request"), job)
            self.assertFalse(ev("pull_request_review_comment"), job)
        gate_run = "\n".join(st.get("run", "") for st in jobs["agentive-review-gate"]["steps"])
        self.assertIn("--fetch-prior", gate_run)
        ind = next(st for st in jobs["agentive-independence"]["steps"] if st.get("id") == "independence")
        self.assertIn('independence "$PR_NUMBER"', ind["run"])
        # no workflow-level concurrency: a newer pending run would cancel the older pending push run that makes the artifact
        self.assertNotIn("concurrency", doc)

    def test_label_run_concurrency_env_and_unique_names(self):
        text, doc = self.files["agentive-review.yml"]
        ai = doc["jobs"]["ai-review"]["concurrency"]
        self.assertIn("github.event.pull_request.number", ai["group"])
        self.assertIs(ai["cancel-in-progress"], False)
        self.assertIs(doc["jobs"]["agentive-review-gate"]["concurrency"]["cancel-in-progress"], False)
        self.assertEqual(doc["env"]["AGENTIVE_EVENT_LABEL"].split()[0], "${{")
        self.assertIn("github.event.label.name", doc["env"]["AGENTIVE_EVENT_LABEL"])
        code = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
        self.assertNotIn("github.event.pull_request.labels", code)                  # labels come from the API, not the event snapshot
        gate_run = "\n".join(st.get("run", "") for st in doc["jobs"]["agentive-review-gate"]["steps"])
        # an unrelated label skips agentive-independence: the gate evaluates it itself instead of counting a skip as failed or passed
        self.assertIn('"$AGENTIVE_EVENT_ACTION" = "labeled"', gate_run)
        self.assertIn('"$GATE" independence "$PR_NUMBER"', gate_run)
        self.assertLess(gate_run.index('"$GATE" independence'), gate_run.index("--must-run agentive-independence"))
        every = [j for _, (t, d) in self.files.items() for j in d["jobs"]]
        self.assertEqual(len(every), len(set(every)))                               # unique job names: one required check, one meaning
        self.assertNotIn("unlabeled", code.split("jobs:")[0].split('"on":')[1])

    def test_janitor_labels_after_creation_and_verifies_with_a_real_token(self):
        base = os.path.join(ROOT, "src", "repo-layer", "dot-agents", "skills", "janitor")
        with open(os.path.join(base, "SKILL.md"), encoding="utf-8") as f:
            t = f.read()
        with open(os.path.join(base, "scripts", "label-pr.sh"), encoding="utf-8") as f:
            sh = f.read()
        # SKILLGUIDE: the skill calls label-pr.sh; the REST calls (POST, then GET read-back) live in the script
        self.assertIn("sh .agents/skills/janitor/scripts/label-pr.sh <id> <pr number>", t)      # E2E-40: the repair; /pr publish --label labels first
        self.assertIn("--label agentive-janitor:<id>", t)
        self.assertIn("issues/$pr/labels", sh)
        self.assertIn("-X POST", sh)
        self.assertIn("-X GET", sh)
        self.assertIn("--jq '.[].name'", sh)                                         # the label is verified after it is added
        self.assertIn("GITHUB_TOKEN", t)
        self.assertIn("GITHUB_TOKEN", sh)
        self.assertNotIn("gh pr create", t + sh)

    def test_ai_review_uses_claude_code_action_and_the_pipeline(self):
        text, doc = self.by_tool["claude"]["agentive-review.yml"]
        steps = doc["jobs"]["ai-review"]["steps"]
        act = [s for s in steps if str(s.get("uses", "")).startswith("anthropics/claude-code-action")]
        self.assertEqual([s["uses"] for s in act], ["anthropics/claude-code-action@v1"])
        self.assertIn(".agents/bin/agentive-review ci", act[0]["with"]["prompt"])
        self.assertEqual(act[0]["with"]["anthropic_api_key"], OAUTH_FIRST)        # E2E-12: OAuth token first
        self.assertEqual(act[0]["with"]["claude_code_oauth_token"], "${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}")    # CIAUTH default both
        self.assertFalse([s for s in steps if "codex-action" in str(s.get("uses", ""))])
        self.assertNotIn("--bare", text)
        self.assertNotIn("/code-review:code-review", text)
        dr = [s for s in doc["jobs"]["dependency-review"]["steps"] if "uses" in s][0]
        self.assertEqual((dr["uses"].split("@")[0], dr["with"]["fail-on-severity"]), ("actions/dependency-review-action", "critical"))

    def test_e2e_20_dependency_review_is_pinned_to_a_full_sha_and_informational(self):
        """E2E-20 (DEPHUMAN): the scanner action is pinned to a full commit SHA (v5.0.0) and is information only."""
        for tool in TOOLS:
            text, doc = self.by_tool[tool]["agentive-review.yml"]
            dr = [s for s in doc["jobs"]["dependency-review"]["steps"] if "uses" in s][0]
            self.assertEqual(dr["uses"], "actions/dependency-review-action@a1d282b36b6f3519aa1f3fc636f609c47dddb294")
            self.assertTrue(dr.get("continue-on-error"))
            self.assertIn("::notice title=dependency review unavailable::", text)
            self.assertIn("Information only: a PR that changes dependencies goes to a human either way.", text)

    def test_ai_review_uses_codex_action_and_the_same_pipeline(self):
        text, doc = self.by_tool["codex"]["agentive-review.yml"]
        steps = doc["jobs"]["ai-review"]["steps"]
        act = [s for s in steps if str(s.get("uses", "")).startswith("openai/codex-action")]
        self.assertEqual([s["uses"] for s in act], ["openai/codex-action@v1"])
        w = act[0]["with"]
        self.assertEqual(w["openai-api-key"], "${{ secrets.OPENAI_API_KEY }}")
        self.assertEqual(w["sandbox"], "workspace-write")
        self.assertIn(".agents/bin/agentive-review ci", w["prompt"])
        self.assertIn(".codex/agents/<agent>.toml", w["prompt"])
        self.assertTrue(act[0]["continue-on-error"])
        self.assertEqual(act[0]["if"], "steps.plan.outputs.needs_llm == 'true'")
        self.assertFalse([s for s in steps if "claude-code-action" in str(s.get("uses", ""))])
        self.assertNotIn("ANTHROPIC", "\n".join(str(s) for j in doc["jobs"].values() for s in j["steps"]))
        self.assertNotIn("--bare", text)

    def llm_step(self, tool):
        steps = self.by_tool[tool]["agentive-review.yml"][1]["jobs"]["ai-review"]["steps"]
        return next(x for x in steps if x.get("id") == "llm")

    def test_ai_review_job_executes_no_pr_code_on_either_tool_path(self):
        """CISPLIT: the LLM step has no execution tool; the verifier reads the results of the outer-verbs job."""
        for tool in TOOLS:
            text, doc = self.by_tool[tool]["agentive-review.yml"]
            step = self.llm_step(tool)
            dump = str(step)
            self.assertNotIn(".agents/bin/verb", dump, tool)
            self.assertNotIn("Bash(.agents/bin/verb", text, tool)
            self.assertIn("--outer-results", step["with"]["prompt"], tool)
            if tool == "claude":
                tools = step["with"]["claude_args"]
                self.assertNotIn("Bash(.agents/bin/verb", tools)
                allowed = re.findall(r"Bash\(([^)]*)\)", tools)                                     # the pipeline itself and read-only git, nothing that runs project code
                self.assertEqual(sorted(allowed), sorted([".agents/bin/agentive-review *", "git diff *", "git show *", "git log *", "git blame *"]))
            # no run step of ai-review runs the project's own scripts (npm/pytest/make/verb)
            for st in doc["jobs"]["ai-review"]["steps"]:
                self.assertNotRegex(str(st.get("run", "")), r"\b(npm|pnpm|yarn|pytest|make|\.agents/bin/verb)\b", (tool, st.get("id")))

    def test_id_token_write_only_on_the_claude_path(self):
        perm = {t: self.by_tool[t]["agentive-review.yml"][1]["jobs"]["ai-review"]["permissions"] for t in TOOLS}
        self.assertEqual(perm["claude"].get("id-token"), "write")
        self.assertNotIn("id-token", perm["codex"])
        for t in TOOLS:
            for job, d in self.by_tool[t]["agentive-review.yml"][1]["jobs"].items():
                if job != "ai-review":
                    self.assertNotIn("id-token", d.get("permissions") or {}, (t, job))
            self.assertNotIn("id-token", self.by_tool[t]["agentive-review.yml"][1].get("permissions") or {}, t)

    def test_outer_verbs_is_the_secretless_job_that_hands_results_over(self):
        for t in TOOLS:
            doc = self.by_tool[t]["agentive-review.yml"][1]
            job = doc["jobs"]["outer-verbs"]
            self.assertEqual(job["permissions"], {"contents": "read"}, t)
            checkout = next(x for x in job["steps"] if x.get("id") == "checkout")
            self.assertIs(checkout["with"]["persist-credentials"], False)
            dump = str(job)
            self.assertNotIn("secrets.", dump, t)
            up = next(x for x in job["steps"] if x.get("id") == "upload-results")
            self.assertTrue(up["uses"].startswith("actions/upload-artifact@"))
            self.assertEqual(up["with"]["name"], "agentive-outer-verbs-${{ github.event.pull_request.head.sha }}")
            down = next(x for x in doc["jobs"]["ai-review"]["steps"] if x.get("id") == "outer-results")
            self.assertEqual(down["with"]["name"], up["with"]["name"])
            self.assertTrue(down["uses"].startswith("actions/download-artifact@"))
        # PR text and job outputs never reach a script body: they pass through env
        text = self.files["agentive-review.yml"][0]
        self.assertNotRegex(text, r"run:[^\n]*\$\{\{ *needs\.[^}]*outputs")
        self.assertNotRegex(text, r"run:[^\n]*\$\{\{ *github\.event\.merge_group")

    def test_task_outputs_hash_is_recorded_and_verified_in_the_gate(self):
        doc = self.files["agentive-review.yml"][1]
        ai, gate = doc["jobs"]["ai-review"], doc["jobs"]["agentive-review-gate"]
        self.assertEqual(ai["outputs"]["task_outputs_sha256"], "${{ steps.outputs-hash.outputs.sha256 }}")
        ids = [x.get("id") for x in ai["steps"]]
        self.assertLess(ids.index("llm"), ids.index("outputs-hash"))
        self.assertLess(ids.index("outputs-hash"), ids.index("finish"))
        gids = [x.get("id") for x in gate["steps"]]
        self.assertLess(gids.index("verify-outputs"), gids.index("gate"))
        verify = next(x for x in gate["steps"] if x["id"] == "verify-outputs")
        self.assertEqual(verify["env"]["EXPECT"], "${{ needs.ai-review.outputs.task_outputs_sha256 }}")
        self.assertIn("outputs-hash", verify["run"])
        fin = next(x for x in ai["steps"] if x["id"] == "finish")
        self.assertEqual(fin["env"]["AGENTIVE_ACTION_SESSION_ID"], "${{ steps.llm.outputs.session_id }}")

    def test_gate_counts_a_skipped_independence_job_as_failed(self):
        """BOTPR: GitHub treats a skipped required check as success; the gate asks --must-run for it."""
        gate = self.files["agentive-review.yml"][1]["jobs"]["agentive-review-gate"]
        step = next(x for x in gate["steps"] if x["id"] == "gate")
        self.assertIn("--must-run agentive-independence", step["run"])
        self.assertEqual(step["env"]["INDEPENDENCE"], "${{ needs.agentive-independence.result }}")
        self.assertEqual(gate["if"], "always()")

    def test_every_step_has_a_stable_id_for_custom_insertion(self):
        """CISETUP / UV-24: .agents/custom/ inserts steps by id (before:/after:), so every rendered step needs one."""
        for t in TOOLS:
            for name, (_, doc) in self.by_tool[t].items():
                if name != "agentive-review.yml":
                    continue
                for job, d in doc["jobs"].items():
                    ids = [x.get("id") for x in d["steps"]]
                    self.assertNotIn(None, ids, (t, job, ids))
                    self.assertEqual(len(ids), len(set(ids)), (t, job))

    def test_outer_verbs_toolchain_steps_come_before_the_verbs(self):
        for eco, root, want in (("node", "ts", ["toolchain-node"]), ("python", "py", ["toolchain-python", "toolchain-uv"]), ("go", "go", ["toolchain-go"]),
                                ("java", "java", ["toolchain-java"])):
            for tool in TOOLS:
                _, doc = load(os.path.join(WF, "agentive-review.yml"), tool, os.path.join(ROOT, "fixtures", root), [eco])
                ids = [x["id"] for x in doc["jobs"]["outer-verbs"]["steps"]]
                self.assertEqual(ids, ["checkout"] + want + ["verbs", "upload-results"], (eco, tool))
                self.assertEqual(yaml.safe_load(load(os.path.join(WF, "agentive-review.yml"), tool, os.path.join(ROOT, "fixtures", root), [eco])[0]),
                                 load(os.path.join(WF, "agentive-review.yml"), tool, os.path.join(ROOT, "fixtures", root), [eco])[1])
        # no stack evidence: only the commented placeholder, the job still parses
        text, doc = load(os.path.join(WF, "agentive-review.yml"), "claude", os.path.join(ROOT, "fixtures", "ts"), [])
        self.assertEqual([x["id"] for x in doc["jobs"]["outer-verbs"]["steps"]], ["checkout", "verbs", "upload-results"])
        self.assertIn("# - id: toolchain", text)

    def test_custom_step_inserted_before_a_toolchain_step_by_id(self):
        _, doc = load(os.path.join(WF, "agentive-review.yml"), "claude", os.path.join(ROOT, "fixtures", "py"), ["python"])
        merged = engine_render.merge(doc, {"jobs": {"outer-verbs": {"steps": [
            {"id": "apt", "run": "sudo apt-get install -y libpq-dev", "before": "toolchain-python"},
            {"id": "cache", "uses": "actions/cache@v4", "after": "toolchain-uv"}]}}})
        self.assertEqual([x["id"] for x in merged["jobs"]["outer-verbs"]["steps"]],
                         ["checkout", "apt", "toolchain-python", "toolchain-uv", "cache", "verbs", "upload-results"])

    def test_reviewer_eval_installs_and_authenticates_the_selected_tool(self):
        claude_check = "${{ secrets.ANTHROPIC_API_KEY != '' || secrets.CLAUDE_CODE_OAUTH_TOKEN != '' }}"
        claude_env = {"AGENTIVE_CI_API_KEY": OAUTH_FIRST, "AGENTIVE_CI_OAUTH_TOKEN": "${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}"}
        for tool, pkg, check, env in (("claude", "@anthropic-ai/claude-code", claude_check, claude_env),
                                      ("codex", "@openai/codex", "${{ secrets.OPENAI_API_KEY != '' }}",
                                       {"AGENTIVE_CI_API_KEY": "${{ secrets.OPENAI_API_KEY }}"})):
            _, doc = self.by_tool[tool]["reviewer-eval.yml"]
            job = doc["jobs"]["reviewer-eval"]
            self.assertEqual(job["env"]["HAS_KEY"], check)
            self.assertEqual([s["run"] for s in job["steps"] if str(s.get("run", "")).startswith("npm install")], ["npm install -g " + pkg])
            self.assertEqual([s["env"] for s in job["steps"] if "AGENTIVE_CI_API_KEY" in (s.get("env") or {})], [env])

    def test_e2e_12_ci_auth_renders_only_the_chosen_claude_credential(self):
        """CIAUTH (user 2026-10-30): review.pipeline.ci_auth api_key | oauth_token keeps one input and one env line."""
        for auth, secret, inp, env in (("api_key", "ANTHROPIC_API_KEY", "anthropic_api_key", "AGENTIVE_CI_API_KEY"),
                                       ("oauth_token", "CLAUDE_CODE_OAUTH_TOKEN", "claude_code_oauth_token", "AGENTIVE_CI_OAUTH_TOKEN")):
            text, doc = load(os.path.join(WF, "agentive-review.yml"), "claude", auth=auth)
            act = [s for s in doc["jobs"]["ai-review"]["steps"] if str(s.get("uses", "")).startswith("anthropics/claude-code-action")][0]
            self.assertEqual({k: v for k, v in act["with"].items() if k in ("anthropic_api_key", "claude_code_oauth_token")},
                             {inp: "${{ secrets.%s }}" % secret}, auth)
            _, ev = load(os.path.join(WF, "reviewer-eval.yml"), "claude", auth=auth)
            job = ev["jobs"]["reviewer-eval"]
            self.assertEqual(job["env"]["HAS_KEY"], "${{ secrets.%s != '' }}" % secret)
            run = [s for s in job["steps"] if str(s.get("name", "")).startswith("before and after")][0]
            self.assertEqual(run["env"], {env: "${{ secrets.%s }}" % secret})
        _, doc = load(os.path.join(WF, "agentive-review.yml"), "codex", auth="oauth_token")      # codex ignores ci_auth
        act = [s for s in doc["jobs"]["ai-review"]["steps"] if str(s.get("uses", "")).startswith("openai/codex-action")][0]
        self.assertEqual(act["with"]["openai-api-key"], "${{ secrets.OPENAI_API_KEY }}")

    def test_e2e_12_both_secrets_pass_only_the_oauth_token(self):
        """E2E-12 (user 2026-10-30, option 1: OAuth token first): with both secrets stored, the default ci_auth passes only
        CLAUDE_CODE_OAUTH_TOKEN; ANTHROPIC_API_KEY reaches the action and the eval run only when the token secret is empty.
        The expression is evaluated here the way GitHub evaluates `a == '' && b || ''` (an unset secret is '')."""
        _, doc = load(os.path.join(WF, "agentive-review.yml"), "claude")
        act = [s for s in doc["jobs"]["ai-review"]["steps"] if str(s.get("uses", "")).startswith("anthropics/claude-code-action")][0]
        _, ev = load(os.path.join(WF, "reviewer-eval.yml"), "claude")
        run = [s for s in ev["jobs"]["reviewer-eval"]["steps"] if str(s.get("name", "")).startswith("before and after")][0]
        for expr in (act["with"]["anthropic_api_key"], run["env"]["AGENTIVE_CI_API_KEY"]):
            m = re.fullmatch(r"\$\{\{ secrets\.(\w+) == '' && secrets\.(\w+) \|\| '' \}\}", expr)
            self.assertIsNotNone(m, expr)
            self.assertEqual(m.groups(), ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY"))
            for token, key, passed in (("t", "k", ""), ("", "k", "k"), ("t", "", ""), ("", "", "")):
                self.assertEqual((token == "" and key) or "", passed)
        self.assertEqual(act["with"]["claude_code_oauth_token"], "${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}")
        for auth in ("api_key", "oauth_token"):           # a pinned credential is passed as is
            _, d = load(os.path.join(WF, "agentive-review.yml"), "claude", auth=auth)
            a = [s for s in d["jobs"]["ai-review"]["steps"] if str(s.get("uses", "")).startswith("anthropics/claude-code-action")][0]
            self.assertNotIn("== ''", str(a["with"]))

    def test_e2e_10b_llm_state_step_records_the_llm_step_without_text(self):
        """E2E-10b (AISTEPFAIL): right after the LLM step, on both tools, a step records outcome, conclusion and the execution
        file path through env and runs `agentive-review llm-state`; show_full_output and display_report are never set."""
        for tool in TOOLS:
            text, doc = self.by_tool[tool]["agentive-review.yml"]
            steps = doc["jobs"]["ai-review"]["steps"]
            ids = [x["id"] for x in steps]
            self.assertEqual(ids.index("llm-state"), ids.index("llm") + 1, tool)
            st = steps[ids.index("llm-state")]
            self.assertEqual(st["if"], "always() && steps.plan.outputs.needs_llm == 'true'")
            env = dict(st["env"])
            # E2E-43: whether any credential secret is set (names only, never a value) tells a missing secret from other failures
            check = env.pop("AGENTIVE_CI_SECRET_SET")
            self.assertRegex(check, r"^\$\{\{ secrets\.[A-Z_]+ != ''( \|\| secrets\.[A-Z_]+ != '')* \}\}$", tool)
            self.assertEqual(env, {"AGENTIVE_LLM_OUTCOME": "${{ steps.llm.outcome }}",
                                   "AGENTIVE_LLM_CONCLUSION": "${{ steps.llm.outputs.conclusion }}",
                                   "AGENTIVE_LLM_EXECUTION_FILE": "${{ steps.llm.outputs.execution_file }}"})
            self.assertEqual(st["run"], '.agents/bin/agentive-review llm-state --work "$WORK"')
            for x in steps:                                         # never switched on (public logs)
                self.assertFalse({"show_full_output", "display_report"} & set(x.get("with") or {}), x.get("id"))
                self.assertNotIn("GITHUB_STEP_SUMMARY", str(x.get("run", "")))    # no excerpt written by the workflow itself

    def test_e2e_11_gate_knows_the_default_branch_and_the_head_repository(self):
        """E2E-11 (WFCHANGE): the workflow-level env gives the gate and the independence job the default branch and the head
        repository (data in env, never in script text); the gate checkout is the merge commit (no ref) with full history;
        there is no pull_request_target trigger."""
        for tool in TOOLS:
            text, doc = self.by_tool[tool]["agentive-review.yml"]
            self.assertEqual(doc["env"]["AGENTIVE_DEFAULT_BRANCH"], "${{ github.event.repository.default_branch }}")
            self.assertEqual(doc["env"]["AGENTIVE_HEAD_REPO"], "${{ github.event.pull_request.head.repo.full_name }}")
            gate = doc["jobs"]["agentive-review-gate"]["steps"][0]
            self.assertEqual((gate["id"], gate["with"]), ("checkout", {"fetch-depth": 0}))
            self.assertNotIn("pull_request_target", doc["on"])
            for job in doc["jobs"].values():
                for st in job["steps"]:
                    self.assertNotIn("head.repo.full_name", str(st.get("run", "")))

    def test_reviewer_eval_is_change_triggered_and_non_blocking(self):
        text, doc = self.files["reviewer-eval.yml"]
        self.assertEqual(set(doc["on"]), {"pull_request"})
        job = doc["jobs"]["reviewer-eval"]
        self.assertTrue(job["continue-on-error"])
        self.assertEqual(doc["permissions"]["pull-requests"], "write")
        self.assertIn("agentive-reviewer-eval detect", text)
        self.assertIn("--n 3", text)
        self.assertIn("--not-run", text)

    def test_e2e_16_first_install_runs_nothing_and_scores_no_baseline(self):
        """E2E-16 (NOBASELINE): the CLI install and the before/after runs need detect's baseline != none; the score step passes
        --no-baseline when it is none (the output arrives through env, not inlined into the script)."""
        for tool, files in self.by_tool.items():
            _, doc = files["reviewer-eval.yml"]
            steps = doc["jobs"]["reviewer-eval"]["steps"]
            runs = [st for st in steps if str(st.get("run", "")).lstrip().startswith(("npm install", ".agents/bin/agentive-reviewer-eval run"))]
            self.assertEqual(len(runs), 2, tool)
            for st in runs:
                self.assertIn("steps.detect.outputs.baseline != 'none'", st["if"], tool)
            score = [st for st in steps if "agentive-reviewer-eval score" in str(st.get("run", ""))][0]
            self.assertEqual(score["env"]["BASELINE"], "${{ steps.detect.outputs.baseline }}")
            self.assertIn('if [ "$BASELINE" = "none" ]; then nb="--no-baseline"', score["run"])
            self.assertIn("$nr $nb", score["run"])
            self.assertNotIn("steps.detect", score["run"])

    def test_terminology(self):
        for files in self.by_tool.values():
            for name, (text, _) in files.items():
                self.assertIsNone(re.search(BANNED, text, re.I), name)


class FrameworkWorkflowsTest(unittest.TestCase):
    def test_framework_ci_never_scheduled(self):
        d = os.path.join(ROOT, ".github", "workflows")
        for f in sorted(os.listdir(d)):
            _, doc = load(os.path.join(d, f))
            self.assertNotIn("schedule", doc["on"], f)

    def test_python_floor_is_one_number_everywhere(self):
        """PYFLOOR (user 2026-11-01): python3 >= 3.13 in the engine check, the README, the NEXT lines and the CI floor job."""
        def rd(*p):
            with open(os.path.join(ROOT, *p), encoding="utf-8") as f:
                return f.read()
        self.assertIn("sys.version_info < (3, 13)", rd("src", "engine", "apply.py"))
        readme = rd("README.md")
        self.assertIn("`python3` 3.13 or newer", readme)
        self.assertIn("NEXT=install python3 >= 3.13", readme)
        for base, _, names in os.walk(os.path.join(ROOT, "src")):
            for n in names:
                with open(os.path.join(base, n), encoding="utf-8", errors="replace") as f:
                    t = f.read()
                self.assertIsNone(re.search(r"python3? *(>=|<) *3\.(9|10)\b", t, re.I), os.path.join(base, n))
        _, doc = load(os.path.join(ROOT, ".github", "workflows", "ci.yml"))
        py = {j: [st["with"]["python-version"] for st in doc["jobs"][j]["steps"] if "setup-python" in st.get("uses", "")]
              for j in ("runtime-python-floor", "runtime-python-latest")}
        self.assertEqual(py, {"runtime-python-floor": ["3.13"], "runtime-python-latest": ["3.15"]})
        self.assertEqual(doc["jobs"]["runtime-python-floor"]["runs-on"], "ubuntu-latest")

    def test_e2e_41_every_python_version_in_workflows_is_at_least_the_floor(self):
        """E2E-41 (high): the selftest job of .github/workflows/ci.yml ran Python 3.12 while the engine stops below 3.13 (73 failures,
        4 errors in CI). Every `python-version:` of the framework workflows, the installed workflow templates and the engine's CI
        step sources is at least the floor read from the engine itself (`sys.version_info < (3, N)` in src/engine/apply.py)."""
        with open(os.path.join(ROOT, "src", "engine", "apply.py"), encoding="utf-8") as f:
            floor = tuple(int(x) for x in re.search(r"sys\.version_info < \((\d+), (\d+)\)", f.read()).groups())
        self.assertEqual(floor, (3, 13))
        dirs = [os.path.join(ROOT, ".github", "workflows"), WF, os.path.join(ROOT, "src", "engine", "ci")]
        seen = []
        for d in dirs:
            for name in sorted(os.listdir(d)):
                if not name.endswith((".yml", ".yaml")):
                    continue
                with open(os.path.join(d, name), encoding="utf-8") as f:
                    text = f.read()
                for m in re.finditer(r"^\s*(?:-\s*)?python-version:\s*[\"']?([0-9]+(?:\.[0-9]+)*)", text, re.M):
                    got = tuple(int(x) for x in m.group(1).split("."))
                    seen.append((name, m.group(1)))
                    self.assertGreaterEqual(got, floor, f"{os.path.join(d, name)}: python-version {m.group(1)} is below the floor")
        names = {n for n, _ in seen}
        self.assertTrue({"ci.yml", "selftest-agent-run.yml"} <= names, names)
        self.assertEqual(sum(1 for n, _ in seen if n == "ci.yml"), 3)      # selftest, runtime-python-floor, runtime-python-latest
        # the same check on the parsed jobs: each setup-python step of ci.yml names a version
        _, doc = load(os.path.join(ROOT, ".github", "workflows", "ci.yml"))
        for job, body in doc["jobs"].items():
            for st in body["steps"]:
                if "setup-python" in st.get("uses", ""):
                    v = tuple(int(x) for x in str(st["with"]["python-version"]).split("."))
                    self.assertGreaterEqual(v, floor, job)


class TerminologyTest(unittest.TestCase):
    def test_owner_d_files(self):
        roots = [os.path.join(ROOT, "src", "repo-layer", "dot-agents", p) for p in ("bin", "agents", os.path.join("lib", "agentive", "review"), os.path.join("lib", "agentive", "lint"))]
        roots += [WF, os.path.join(ROOT, "tests", "review"), os.path.join(ROOT, "tests", "sample_repo"), os.path.join(ROOT, "docs", "impl")]
        for r in roots:
            for base, _, files in os.walk(r):
                for f in files:
                    if f.endswith((".pyc",)):
                        continue
                    p = os.path.join(base, f)
                    if r.endswith("impl") and f != "D.md":
                        continue
                    with open(p, encoding="utf-8", errors="replace") as fh:
                        self.assertIsNone(re.search(BANNED, fh.read(), re.I), p)


if __name__ == "__main__":
    unittest.main()
