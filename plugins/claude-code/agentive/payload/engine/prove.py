# agentive-unit: eng.apply
# design: spec.init_apply.proof, decisions.defaults[D11] (pass and seeded fail), spec.init_apply.empty_dir.proofs
"""Slot proof harness: run a verb command in the repo and record run ids in manifest verbs.<v>.proof.

pass proof: the command exits 0 on the current tree. seeded fail (lint, typecheck, test): the skill names a seed
file and its content in --values seeds.<verb> (the LLM part: what violation fits this stack); the harness writes
it (the path must not exist), runs the command, expects a non-zero exit and deletes the seed again.
verify: passes when every inner verb proof passed (and its own command when that is not the wrapper itself).
A failed proof keeps the command with proof.pass_run_id `unproved` and prints AGENTIVE_PROOF_FAIL [설계 판단].
Logs: .agents/state/logs/proof-<verb>-<ts>.log. Pending verbs are never proved.
"""
import datetime
import os
import subprocess

__all__ = ["prove_verbs"]

TIMEOUT = 1800
SELF = ".agents/bin/verb verify"


def _ts():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _now():
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _run(root, verb, cmd, tag):
    logdir = os.path.join(root, ".agents", "state", "logs")
    old = os.umask(0o077)
    try:
        os.makedirs(logdir, exist_ok=True)
        run_id = f"proof-{verb}-{tag}-{_ts()}"
        rel = f".agents/state/logs/{run_id}.log"
        with open(os.path.join(root, rel), "wb") as log:
            log.write(f"$ {cmd}\n".encode())
            log.flush()
            try:
                rc = subprocess.run(["sh", "-c", cmd], cwd=root, stdout=log, stderr=subprocess.STDOUT, timeout=TIMEOUT).returncode
            except subprocess.TimeoutExpired:
                rc = 124
            except OSError:
                rc = 127
    finally:
        os.umask(old)
    return run_id, rc, rel


def prove_verbs(root, verbs, targets, seeds, out):
    """Prove `targets` (verb names) in place in the `verbs` dict. `out(line)` prints agent lines."""
    results = {}
    order = [v for v in ("setup", "lint", "typecheck", "build", "test", "run", "verify") if v in targets]
    for v in order:
        spec = verbs.get(v)
        if not isinstance(spec, dict) or not spec.get("cmd"):
            continue
        if v == "run":
            continue   # running the app is not proved by exit code (it may not exit) [설계 판단]
        if v == "verify":
            inner_ok = all(results.get(x, True) for x in ("lint", "typecheck", "test"))
            ok, rid = inner_ok, f"proof-verify-derived-{_ts()}"
            if spec["cmd"].strip() != SELF:
                rid, rc, rel = _run(root, v, spec["cmd"], "pass")
                ok = ok and rc == 0
                if rc != 0:
                    out(f"AGENTIVE_PROOF_FAIL verb=verify exit={rc} log={rel}; NEXT=fix the command in --values verbs.verify or the repo, then run /agentive:reprove")
            spec["proof"] = {"pass_run_id": rid if ok else "unproved", "at": _now()}
            results[v] = ok
            continue
        rid, rc, rel = _run(root, v, spec["cmd"], "pass")
        proof = {"pass_run_id": rid if rc == 0 else "unproved", "at": _now()}
        ok = rc == 0
        if not ok:
            out(f"AGENTIVE_PROOF_FAIL verb={v} exit={rc} log={rel}; NEXT=grep -n -m20 -E '(error|Error|fail|FAIL)' {rel}")
        seed = (seeds or {}).get(v)
        if ok and isinstance(seed, dict) and seed.get("path") and v in ("lint", "typecheck", "test"):
            sp = os.path.join(root, seed["path"])
            if os.path.exists(sp):
                out(f"AGENTIVE_WARN seed path {seed['path']} exists; seeded proof of {v} skipped")
            else:
                os.makedirs(os.path.dirname(sp) or root, exist_ok=True)
                with open(sp, "w", encoding="utf-8", newline="\n") as f:
                    f.write(seed.get("content", ""))
                try:
                    srid, src_rc, srel = _run(root, v, spec["cmd"], "seeded")
                finally:
                    os.unlink(sp)
                if src_rc != 0:
                    proof["seeded_fail_run_id"] = srid
                else:
                    ok = False
                    proof["pass_run_id"] = "unproved"
                    out(f"AGENTIVE_PROOF_FAIL verb={v} seeded violation passed (the command checks nothing) log={srel}; NEXT=fix verbs.{v}.cmd in --values")
        elif ok and v in ("lint", "typecheck", "test"):
            out(f"AGENTIVE_WARN verb={v} has a pass proof only (no seed given in --values seeds.{v})")
        spec["proof"] = proof
        results[v] = ok
    return results
