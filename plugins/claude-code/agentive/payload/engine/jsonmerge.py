# agentive-unit: eng.apply
# design: decisions.accepted[JSONMERGE], spec.init_apply.managed_regions.file_classes.whole.json_note, spec.init_apply.conflicts (D10), contracts[manifest], docs/impl/B.md §2
"""Key-level merge of JSON settings files that the user also edits (.claude/settings.json, .codex/hooks.json).

The framework owns only a fixed list of top-level keys (OWNED). Everything else in the file is the user's (or
another tool's, for example `enabledPlugins` written by `claude plugin install`) and is preserved value for value.

  hooks        every agentive handler carries an ownership marker: its command (or its first argument) names a
               script under `.agents/hooks/` [design judgment]. A run removes the agentive handlers it finds and puts the
               current ones back at the place of the first removed one (else at the end of the event list).
               Handlers without the marker are the user's and stay.
  permissions  allow / deny / ask arrays: union, de-duplicated, existing entries first, then agentive entries in
               template order. Other permission keys stay.
  sandbox, env, plansDirectory (and any other owned mapping)
               a key that is absent is added; mappings merge recursively; lists are united; a scalar that
               exists with a different value is KEPT and reported as a conflict.
The baseline (manifest.generated) is the sha256 of the agentive-owned part: the agentive hook handlers by event.
Editing any other key never counts as drift. A file that does not parse as strict JSON (a `//` comment, a trailing
comma) is never written.
"""
import hashlib
import json
import re

OWNED = {
    ".claude/settings.json": ["hooks", "permissions", "sandbox", "plansDirectory", "env"],
    ".codex/hooks.json": ["hooks"],
}
ADD_IF_ABSENT = ["$schema", "description"]    # informational keys: added when missing, never a conflict
PERM_ARRAYS = ("allow", "deny", "ask")
MARKER = re.compile(r"(^|[\\/\s\"'])\.agents/hooks/[A-Za-z0-9_.-]+\.py")


class Unparsable(Exception):
    pass


def parse(text):
    """Strict JSON object, or Unparsable with the position of the first problem."""
    try:
        doc = json.loads(text)
    except ValueError as e:
        raise Unparsable(str(e))
    if not isinstance(doc, dict):
        raise Unparsable("the top level is not a JSON object")
    return doc


def dumps(doc):
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"


def is_agentive_handler(h):
    if not isinstance(h, dict):
        return False
    parts = [str(h.get("command", ""))] + [str(a) for a in (h.get("args") or []) if isinstance(a, str)]
    return any(MARKER.search(p) for p in parts)


def owned_handlers(doc):
    """{event: [handler, ...]} of the agentive handlers in a document, in file order."""
    out = {}
    hooks = doc.get("hooks")
    if not isinstance(hooks, dict):
        return out
    for ev, groups in hooks.items():
        if not isinstance(groups, list):
            continue
        for g in groups:
            if isinstance(g, dict) and isinstance(g.get("hooks"), list):
                for h in g["hooks"]:
                    if is_agentive_handler(h):
                        out.setdefault(ev, []).append({"matcher": g.get("matcher"), "handler": h})
    return out


def owned_sha(doc):
    return hashlib.sha256(json.dumps(owned_handlers(doc), sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def handlers_text(doc):
    return json.dumps(owned_handlers(doc), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _merge_hooks(cur, tpl):
    """Existing `hooks` value + template `hooks` value, user handlers kept, agentive ones replaced."""
    cur = cur if isinstance(cur, dict) else {}
    out = {}
    for ev, groups in cur.items():
        out[ev] = groups
        if ev not in (tpl or {}) and isinstance(groups, list):    # an event the template no longer uses: drop only our handlers
            kept = []
            for g in groups:
                if isinstance(g, dict) and isinstance(g.get("hooks"), list) and any(is_agentive_handler(h) for h in g["hooks"]):
                    rest = [h for h in g["hooks"] if not is_agentive_handler(h)]
                    if rest:
                        kept.append(dict(g, hooks=rest))
                else:
                    kept.append(g)
            out[ev] = kept
    for ev, tgroups in (tpl or {}).items():
        groups = cur.get(ev)
        if not isinstance(groups, list):
            out[ev] = json.loads(json.dumps(tgroups))
            continue
        res, at = [], None
        for g in groups:
            if isinstance(g, dict) and isinstance(g.get("hooks"), list) and any(is_agentive_handler(h) for h in g["hooks"]):
                if at is None:
                    at = len(res)
                rest = [h for h in g["hooks"] if not is_agentive_handler(h)]
                if rest:
                    keep = dict(g)
                    keep["hooks"] = rest
                    res.append(keep)
            else:
                res.append(g)
        new = json.loads(json.dumps(tgroups))
        out[ev] = res + new if at is None else res[:at] + new + res[at:]
    return out


def _union(cur, tpl):
    res = list(cur)
    for x in tpl:
        if x not in res:
            res.append(json.loads(json.dumps(x)))
    return res


def _merge_value(cur, tpl, path, conflicts):
    """Absent -> template value; mapping+mapping -> recursive; list+list -> union; scalar differs -> keep, conflict."""
    if isinstance(cur, dict) and isinstance(tpl, dict):
        out = dict(cur)
        for k, v in tpl.items():
            if k in cur:
                out[k] = _merge_value(cur[k], v, path + [k], conflicts)
            else:
                out[k] = json.loads(json.dumps(v))
        return out
    if isinstance(cur, list) and isinstance(tpl, list):
        return _union(cur, tpl)
    if cur != tpl:
        conflicts.append({"key": ".".join(path), "existing": cur, "proposed": tpl})
    return cur


def _merge_permissions(cur, tpl, conflicts):
    if not isinstance(cur, dict):
        conflicts.append({"key": "permissions", "existing": cur, "proposed": tpl})
        return cur
    out = dict(cur)
    for k, v in tpl.items():
        if k in PERM_ARRAYS and isinstance(v, list):
            have = cur.get(k)
            if have is None:
                if v:
                    out[k] = list(v)
            elif isinstance(have, list):
                out[k] = _union(have, v)
            else:
                conflicts.append({"key": f"permissions.{k}", "existing": have, "proposed": v})
        elif k in cur:
            out[k] = _merge_value(cur[k], v, ["permissions", k], conflicts)
        else:
            out[k] = json.loads(json.dumps(v))
    return out


def merge(path, current, template, hooks_mode="replace"):
    """Merge `template` (the rendered agentive document) into `current` (the user's parsed file).

    hooks_mode: replace (agentive handlers are replaced) | keep (the user changed an agentive handler: hooks stay
    untouched and the run reports drift). Returns (new_document, conflicts). `current` is not modified."""
    owned = OWNED[path]
    out = json.loads(json.dumps(current))
    conflicts = []
    for k in ADD_IF_ABSENT:
        if k in template and k not in out:
            out[k] = template[k]
    for k in owned:
        if k not in template:
            continue
        v = template[k]
        if k == "hooks":
            if hooks_mode == "replace":
                out["hooks"] = _merge_hooks(out.get("hooks"), v)
        elif k == "permissions":
            if k in out:
                out[k] = _merge_permissions(out[k], v, conflicts)
            else:
                out[k] = json.loads(json.dumps(v))
        elif k in out:
            out[k] = _merge_value(out[k], v, [k], conflicts)
        else:
            out[k] = json.loads(json.dumps(v))
    return out, conflicts


def describe_handler(h):
    """Short `command args` text of a handler, for the run report."""
    cmd = str(h.get("command", ""))
    args = " ".join(str(a) for a in (h.get("args") or []))
    return (cmd + " " + args).strip()
