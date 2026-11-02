# agentive-unit: eng.apply
# design: spec.init_apply.proof, decisions.defaults[D11] (pass and seeded fail), spec.init_apply.empty_dir.proofs
"""Slot proof harness: run a verb command in the repo and record run ids in manifest verbs.<v>.proof.

pass proof: the command exits 0 on the current tree. seeded fail (lint, typecheck, test): the skill names a seed
file and its content in --values seeds.<verb> (the LLM part: what violation fits this stack); the harness writes
it (the path must not exist), runs the command, expects a non-zero exit and deletes the seed again.
verify: passes when every inner verb proof passed (and its own command when that is not the wrapper itself).
A failed proof keeps the command with proof.pass_run_id `unproved` and prints AGENTIVE_PROOF_FAIL [design judgment].
E2E-47: the directories (depth 1 and 2) that the build proof created are removed again (AGENTIVE_PROOF_CLEAN) [design judgment].
Logs: .agents/state/logs/proof-<verb>-<ts>.log. Pending verbs are never proved.
"""
import datetime
import os
import shutil
import subprocess

__all__ = ["prove_verbs"]

TIMEOUT = 1800
SELF = ".agents/bin/verb verify"
CLEAN_SKIP = {".git", ".agents", ".claude", ".codex", ".venv", "venv", "node_modules", "__pycache__"}


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


def _dirs(root):
    """Directories at depth 1 and 2 of the work tree (relative paths) that exist now. E2E-47: a build proof makes `dist/`, `build/`,
    `target/` or `src/<pkg>.egg-info/`; nothing deeper is looked at. .git, .agents and dependency stores (.venv, node_modules) are
    never entered: the proof of `setup` makes them on purpose and later verbs need them."""
    found = set()
    for top in os.listdir(root):
        p = os.path.join(root, top)
        if top in CLEAN_SKIP or os.path.islink(p) or not os.path.isdir(p):
            continue
        found.add(top)
        try:
            names = os.listdir(p)
        except OSError:
            continue
        for sub in names:
            q = os.path.join(p, sub)
            if sub not in CLEAN_SKIP and not os.path.islink(q) and os.path.isdir(q):
                found.add(f"{top}/{sub}")
    return found


def _clean_build_output(root, before, out):
    """E2E-47 [design judgment: the design says only that build `produces its artifact`]: after the build proof, remove the
    directories (depth 1 and 2) that did not exist before it, so init leaves the tree as it found it and a later `update` sees
    no `dist/` it would record as generated. Only directories, only new ones; files, symlinks and pre-existing paths stay."""
    new = sorted(_dirs(root) - before)
    top = [d for d in new if not any(d.startswith(o + "/") for o in new)]
    gone = []
    for d in top:
        p = os.path.join(root, *d.split("/"))
        if os.path.islink(p) or not os.path.isdir(p):
            continue
        try:
            shutil.rmtree(p)
            gone.append(d)
        except OSError:
            out(f"AGENTIVE_WARN build proof output {d}/ could not be removed; remove it by hand")
    if gone:
        out(f"AGENTIVE_PROOF_CLEAN verb=build removed={','.join(gone)}; the build proof made these directories, they are gone again")


def prove_verbs(root, verbs, targets, seeds, out):
    """Prove `targets` (verb names) in place in the `verbs` dict. `out(line)` prints agent lines."""
    results = {}
    order = [v for v in ("setup", "lint", "typecheck", "build", "test", "run", "verify") if v in targets]
    for v in order:
        spec = verbs.get(v)
        if not isinstance(spec, dict) or not spec.get("cmd"):
            continue
        if v == "run":
            continue   # running the app is not proved by exit code (it may not exit) [design judgment]
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
        before = _dirs(root) if v == "build" else None
        rid, rc, rel = _run(root, v, spec["cmd"], "pass")
        if before is not None:
            _clean_build_output(root, before, out)
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
