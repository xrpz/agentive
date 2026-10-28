# agentive-unit: lib.agentive
# design: spec.repo_layer.files[f-reviewers].scripts, spec.review_pipeline.principles, spec.context_management.subagent_output (owner D)
"""LLM task protocol between `.agents/bin/agentive-review` (deterministic) and the agent that runs it [설계 판단].

The script never calls a model. When an LLM stage has work, it writes one task input file per subagent call and
prints, last on stdout:

  AGENTIVE_REVIEW_TASK id=<task> agent=<agent> input=<input path> output=<output path>     (one line per task)
  AGENTIVE_REVIEW=tasks:<n> stage=<RVx> NEXT=<what to do>

The agent runs each task with the named project subagent (.claude/agents/<agent>.md, rendered from
.agents/agents/<agent>.md; Codex .codex/agents/<agent>.toml) in a fresh context, with the prompt
`Read <input path> and do the task it describes. Reply with the JSON object only.`, writes the subagent's reply
verbatim to the output path, and runs the same command again. Tasks of one stage are independent (run them in
parallel). The script validates every output; a missing or invalid output is handled fail-safe by the stage
(RV3 -> class other, RV4 -> tier 2, RV5/RV6 -> human_required, RV7 -> inconclusive).

Each task gets a fresh session_id (uuid4) written into its input; the report records it as the reviewer's session
(review-report rv5.reviewers[].session_id, finding.verification.verifier_session_id), which agentive-independence
compares with the implementer session.
"""
import json
import os
import uuid

from .. import common

AGENTS = {"rv3": "classifier", "rv4": "raise-only", "rv7": "verifier", "rv6": "coordinator"}


class Tasks:
    def __init__(self, work_dir):
        self.dir = os.path.join(work_dir, "tasks")
        self.pending = []

    def _paths(self, tid):
        return os.path.join(self.dir, tid + ".input.json"), os.path.join(self.dir, tid + ".output.json")

    def output(self, tid):
        """(parsed output or None, session_id or None, problem or None) of a task that has an input file."""
        ip, op = self._paths(tid)
        inp = common.read_json(ip)
        if inp is None:
            return None, None, None
        sid = inp.get("session_id")
        if not os.path.exists(op):
            return None, sid, "no output"
        try:
            with open(op, encoding="utf-8") as f:
                text = f.read().strip()
        except OSError as e:
            return None, sid, str(e)
        out = _parse_json_reply(text)
        if out is None:
            return None, sid, "output is not a JSON object"
        return out, sid, None

    def has_input(self, tid):
        return os.path.exists(self._paths(tid)[0])

    def has_output(self, tid):
        return os.path.exists(self._paths(tid)[1])

    def request(self, tid, agent, payload):
        """Write the task input (once; a rerun keeps the session id) and queue the task."""
        ip, op = self._paths(tid)
        if not os.path.exists(ip):
            data = dict(payload)
            data.update({"task": tid, "agent": agent, "session_id": str(uuid.uuid4()), "output_path": op,
                         "reply": "Reply with exactly one JSON object as described in output_format; no prose, no code fence."})
            common.write_json(ip, data)
        self.pending.append((tid, agent, ip, op))

    def lines(self, stage, rerun_cmd):
        out = [f"AGENTIVE_REVIEW_TASK id={t} agent={a} input={i} output={o}" for t, a, i, o in self.pending]
        out.append(f"AGENTIVE_REVIEW=tasks:{len(self.pending)} stage={stage} NEXT=run each AGENTIVE_REVIEW_TASK with its "
                   f"agent as a subagent in a fresh context (prompt: Read <input> and do the task it describes. Reply with the "
                   f"JSON object only.), write each reply verbatim to its output path, then run: {rerun_cmd}")
        return out


def _parse_json_reply(text):
    """The JSON object in a subagent reply (tolerates a ```json fence around it)."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else ""
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    try:
        v = json.loads(t)
    except ValueError:
        start, end = t.find("{"), t.rfind("}")
        if start == -1 or end <= start:
            return None
        try:
            v = json.loads(t[start:end + 1])
        except ValueError:
            return None
    return v if isinstance(v, dict) else None


OUTPUT_FORMATS = {
    "classifier": {"classes": "non-empty list from: refactor, feature, fix, security, deps, config, test, other "
                              "(never docs, format, lockfile or generated: only rules may give those)",
                   "why": "one sentence"},
    "raise-only": {"human_needed": "true or false",
                   "criteria": "list of a | b | c that apply (non-empty when human_needed is true)",
                   "why": "one sentence naming the code and the criterion"},
    "reviewer": {"findings": [{"file": "repo-relative path", "line": "integer line in the head tree",
                               "trigger": "the condition that makes it a bug (required)",
                               "summary": "one sentence", "severity": "important | nit | pre_existing",
                               "bug_class": "short name of the bug class", "evidence": "how you know (code ref, command)",
                               "instances": [{"file": "path", "line": "integer"}],
                               "same_as": "the id of an open finding this is the same issue as (optional)"}],
                 "open_findings": [{"id": "open finding id from the input", "status": "still_present | fixed"}]},
    "verifier": {"verdicts": [{"id": "finding id", "verdict": "confirmed | refuted | inconclusive",
                               "method": "execution | adversarial_fresh_context",
                               "note": "the command you ran and its result, or why the claim holds or fails"}]},
    "coordinator": {"findings": [{"id": "finding id kept", "merged_from": ["ids merged into it"],
                                  "severity": "important | nit | pre_existing", "bug_class": "short name"}]},
}
