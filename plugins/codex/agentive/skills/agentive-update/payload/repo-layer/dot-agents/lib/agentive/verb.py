# agentive-unit: lib.agentive
# design: spec.verb_wrapper, contracts[verify-result], spec.init_apply.empty_dir.pending.consumers · owner B
"""`.agents/bin/verb <verb> [-- args...]`: the one wrapper every verb runs through.

- reads the agentive manifest verbs.<verb>; a pending verb prints `PENDING verb=<v> reason=no_evidence` and exits 0
- runs `<cmd> [args]` with `sh -c`, no pipe: all output goes to .agents/state/logs/<verb>-<ts>.log (umask 077,
  latest 20 per verb kept); prints an excerpt: success = last 5 lines, failure = last 40 lines and at most 4,000
  bytes (whole lines only; no per-line cut, which breaks UTF-8)
- failure: last stdout line `AGENTIVE_FAIL verb=<v> exit=<n> log=<path>; NEXT=<one read command>`
- failures = `grep -c -E <fail_count>` on the log when the manifest sets fail_count (at least 1 on a non-zero
  exit), else 0/1 (documented limitation)
- `verb verify` runs every inner-tier verb with a command (lint, typecheck, build, test order) and then the verify
  verb's own command when it is not the wrapper itself (for example a wired secret scanner), and writes
  .agents/state/verify.json (pending[], head_sha, worktree_dirty, history of the last 10 runs)
- extra args after `--` are appended shell-quoted [설계 판단: lets the test-rewrite hook keep arguments]
Logs are not redacted: do not paste or commit them.
"""
import datetime
import os
import re
import shlex
import subprocess
import sys
import time

from . import common, hookio

__all__ = ["main", "run_verb", "VERB_ORDER"]

VERB_ORDER = ["setup", "build", "lint", "typecheck", "test", "verify", "run"]
VERIFY_ORDER = ["lint", "typecheck", "build", "test"]
SELF = ".agents/bin/verb verify"
KEEP_LOGS = 20


def _ts():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _prune(logdir, verb):
    logs = sorted(n for n in os.listdir(logdir) if n.startswith(verb + "-") and n.endswith(".log"))
    for n in logs[:-KEEP_LOGS]:
        try:
            os.unlink(os.path.join(logdir, n))
        except OSError:
            pass


def _excerpt(data, ok):
    lines = data.decode("utf-8", "replace").splitlines()
    if ok:
        return "\n".join(lines[-5:])
    picked, size = [], 0
    for ln in reversed(lines[-40:]):
        b = len(ln.encode("utf-8")) + 1
        if size + b > 4000:
            break
        picked.append(ln)
        size += b
    return "\n".join(reversed(picked))


def run_verb(root, manifest, verb, extra=None, quiet=False):
    """Run one verb. Returns dict {ok, failures, duration_ms, cmd, log, rc, pending, fail_line}."""
    spec = (manifest.get("verbs") or {}).get(verb)
    if not isinstance(spec, dict) or spec.get("status") == "pending" or not spec.get("cmd"):
        if not quiet:
            print(f"PENDING verb={verb} reason=no_evidence")
        return {"pending": True}
    cmd = spec["cmd"]
    if extra:
        cmd = cmd + " " + " ".join(shlex.quote(a) for a in extra)
    logdir = os.path.join(root, ".agents", "state", "logs")
    old = os.umask(0o077)
    try:
        os.makedirs(logdir, exist_ok=True)
        rel = f".agents/state/logs/{verb}-{_ts()}.log"
        logp = os.path.join(root, rel)
        t0 = time.monotonic()
        with open(logp, "wb") as log:
            log.write(f"$ {cmd}\n".encode("utf-8"))
            log.flush()
            env = dict(os.environ, AGENTIVE_VERB=verb)
            try:
                rc = subprocess.run(["sh", "-c", cmd], cwd=root, stdout=log, stderr=subprocess.STDOUT, env=env).returncode
            except OSError as e:
                log.write(f"agentive: could not start sh: {e}\n".encode("utf-8"))
                rc = 127
        dur = int((time.monotonic() - t0) * 1000)
    finally:
        os.umask(old)
    _prune(logdir, verb)
    with open(logp, "rb") as f:
        data = f.read()
    ok = rc == 0
    failures = 0 if ok else 1
    pat = spec.get("fail_count")
    if pat:
        try:
            n = sum(1 for ln in data.decode("utf-8", "replace").splitlines() if re.search(pat, ln))
            failures = n if (n or ok) else 1
        except re.error:
            pass
    res = {"ok": ok, "failures": failures, "duration_ms": dur, "cmd": cmd, "log": rel, "rc": rc, "pending": False}
    if not quiet:
        ex = _excerpt(data, ok)
        if ex:
            print(ex)
    if not ok:
        grep = pat or r"(error|Error|ERROR|fail|FAIL|Fail)"
        res["fail_line"] = common.fail_line("AGENTIVE_FAIL", f"grep -n -m20 -E {shlex.quote(grep)} {rel}",
                                            verb=verb, exit=rc, log=rel)
    return res


def _verify(root, manifest):
    verbs = manifest.get("verbs") or {}
    results, pending, fails = {}, [], []
    for v in VERIFY_ORDER:
        spec = verbs.get(v)
        if not isinstance(spec, dict):
            continue
        if spec.get("status") == "pending":
            pending.append(v)
            continue
        if spec.get("tier", "inner") != "inner":
            continue
        print(f"== {v}: {spec['cmd']}")
        r = run_verb(root, manifest, v)
        results[v] = {k: r[k] for k in ("ok", "failures", "duration_ms", "cmd", "log")}
        if not r["ok"]:
            fails.append(r["fail_line"])
    vspec = verbs.get("verify")
    if isinstance(vspec, dict) and vspec.get("status") == "pending":
        pending.append("verify")
    elif isinstance(vspec, dict) and vspec.get("cmd") and vspec["cmd"].strip() != SELF:
        print(f"== verify: {vspec['cmd']}")
        r = run_verb(root, manifest, "verify")
        results["verify"] = {k: r[k] for k in ("ok", "failures", "duration_ms", "cmd", "log")}
        if not r["ok"]:
            fails.append(r["fail_line"])
    for v in ("setup", "run"):
        if isinstance(verbs.get(v), dict) and verbs[v].get("status") == "pending":
            pending.append(v)
    total = sum(r["failures"] for r in results.values())
    prev = common.read_json(os.path.join(root, ".agents", "state", "verify.json"), {}) or {}
    run_id = "verify-" + _ts()
    history = (list(prev.get("history") or []) + [{"run_id": run_id, "total_failures": total}])[-10:]
    head = hookio.head_sha(root) or "0" * 40
    porcelain = hookio.git(root, "status", "--porcelain") or ""
    doc = {"run_id": run_id, "ts": common.utc_now(), "verbs": results, "total_failures": total,
           "history": history, "pending": sorted(set(pending), key=VERB_ORDER.index), "head_sha": head,
           "worktree_dirty": bool(porcelain.strip())}
    common.write_json(os.path.join(root, ".agents", "state", "verify.json"), doc)
    for line in fails:
        print(line)
    if not results:
        print("PENDING verify: no verb has a command yet (근거 없음 · 대기); NEXT=scaffold the project, then run /agentive:update")
    return 1 if fails else 0


def main(argv):
    root = common.repo_root(os.getcwd()) or os.getcwd()
    if not argv or argv[0] in ("-h", "--help"):
        print("usage: .agents/bin/verb <" + "|".join(VERB_ORDER) + "> [-- args...]")
        return 2
    verb, rest = argv[0], argv[1:]
    if rest and rest[0] == "--":
        rest = rest[1:]
    if verb not in VERB_ORDER:
        print(common.fail_line("AGENTIVE_FAIL", "use one of " + ",".join(VERB_ORDER), verb=verb, exit=2, log="none"))
        return 2
    manifest = hookio.load_manifest(root)
    if manifest is None:
        print(common.fail_line("AGENTIVE_FAIL", "ask the user to run /agentive:init (Codex: $agentive-init)",
                               verb=verb, exit=2, log="none"))
        return 2
    if verb == "verify" and not rest:
        return _verify(root, manifest)
    r = run_verb(root, manifest, verb, rest)
    if r.get("pending"):
        return 0
    if not r["ok"]:
        print(r["fail_line"])
        return r["rc"] if 0 < r["rc"] < 256 else 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
