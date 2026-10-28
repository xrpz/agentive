# agentive-unit: eng.apply
# design: spec.init_apply.update.custom_overrides (merge rules, UV-24), docs/IMPLEMENTATION.md §3 (install-time placeholders), docs/impl/B.md §1
"""Install-time rendering: `%%agentive:<key>%%` placeholders, `when` conditions and .agents/custom/ overrides.

Placeholders (docs/impl/B.md §1): block (alone on a line; empty value removes the line), inline (one line), and in
JSON templates a string that is exactly one placeholder becomes the JSON value. Unknown keys raise RenderError.
Overrides (BMAD rules [S60]): scalar -> override wins; mapping -> deep merge; list of mappings where every item
has id (base and override) -> merge by id, a new id appends; any other list -> append (base first); a shape
mismatch (scalar over mapping, ...) or a parse error -> InvalidOverride for that unit only. Text files: the
override text is appended after the generated content; a fenced block in the override whose info string equals
one in the generated text (```yaml agentive-review-rules) is merged into it structurally.
"""
import json
import re
import sys

__all__ = ["RenderError", "InvalidOverride", "render_text", "eval_when", "merge", "apply_override", "toml_loads", "toml_dumps"]

PH = re.compile(r"%%agentive:([A-Za-z0-9_.<>\[\]-]+)%%")


class RenderError(Exception):
    def __init__(self, key, msg):
        super().__init__(msg)
        self.key = key


class InvalidOverride(Exception):
    pass


def _lookup(key, ctx):
    if key in ctx["blocks"]:
        return ctx["blocks"][key]
    for src in (ctx.get("manifest") or {}, ctx.get("config") or {}):
        node, ok = src, True
        for part in key.split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                ok = False
                break
        if ok and node is not None and not isinstance(node, dict):
            return node
    raise RenderError(key, f"placeholder %%agentive:{key}%% does not resolve")


def _as_text(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (list, tuple)):
        return " ".join(str(x) for x in v)
    return str(v)


def render_text(text, ctx):
    out = []
    for line in text.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        nl = line[len(body):]
        m = PH.fullmatch(body.strip())
        if m:
            val = _as_text(_lookup(m.group(1), ctx))
            if val == "":
                continue
            indent = body[:len(body) - len(body.lstrip())]
            out.append((nl or "\n").join(indent + vl for vl in val.split("\n")) + nl)
            continue

        def rep(mm):
            v = _as_text(_lookup(mm.group(1), ctx))
            if "\n" in v:
                raise RenderError(mm.group(1), f"multi-line value for inline placeholder %%agentive:{mm.group(1)}%%")
            return v
        out.append(PH.sub(rep, body) + nl)
    return "".join(out)


def _render_json_node(node, ctx):
    if isinstance(node, dict):
        return {k: _render_json_node(v, ctx) for k, v in node.items()}
    if isinstance(node, list):
        out = []
        for x in node:
            if isinstance(x, str) and PH.fullmatch(x) and x.startswith("%%agentive:render.") and isinstance(_lookup(PH.fullmatch(x).group(1), ctx), list):
                out.extend(_lookup(PH.fullmatch(x).group(1), ctx))   # a list placeholder inside a list is spliced
            else:
                out.append(_render_json_node(x, ctx))
        return out
    if isinstance(node, str):
        m = PH.fullmatch(node)
        if m:
            return _lookup(m.group(1), ctx)
        return PH.sub(lambda mm: _as_text(_lookup(mm.group(1), ctx)), node)
    return node


def render_json(text, ctx):
    doc = json.loads(text)
    return json.dumps(_render_json_node(doc, ctx), ensure_ascii=False, indent=2) + "\n"


def render(path, text, ctx):
    if not PH.search(text):
        return text
    if path.endswith(".json"):
        return render_json(text, ctx)
    return render_text(text, ctx)


# ------------------------------------------------------------------ when
def _truthy(v):
    if isinstance(v, str):
        return v.strip().lower() not in ("", "false", "no", "0", "none", "pending")
    return bool(v)


def _atom(a, ctx):
    a = a.strip()
    if a.startswith("not "):
        return not _atom(a[4:], ctx)
    if a == "evidence":
        return bool(ctx["ecosystems"])
    if a.startswith("stack:"):
        return a[6:] in ctx["ecosystems"]
    if a.startswith("verb:"):
        v = ((ctx.get("manifest") or {}).get("verbs") or {}).get(a[5:])
        return isinstance(v, dict) and bool(v.get("cmd")) and v.get("status") != "pending"
    for src in (ctx.get("config") or {}, ctx.get("manifest") or {}):
        node = src
        for part in a.split("."):
            node = node.get(part) if isinstance(node, dict) else None
        if node is not None:
            return _truthy(node)
    return False


def eval_when(expr, ctx):
    if not expr:
        return True
    return any(all(_atom(x, ctx) for x in re.split(r"\s+and\s+", part)) for part in re.split(r"\s+or\s+", expr.strip()))


# ------------------------------------------------------------------ structured merge
def merge(base, over, where="<root>"):
    if isinstance(base, dict):
        if not isinstance(over, dict):
            raise InvalidOverride(f"{where}: override is a {type(over).__name__}, base is a mapping")
        out = dict(base)
        for k, v in over.items():
            out[k] = merge(base[k], v, f"{where}.{k}") if k in base else v
        return out
    if isinstance(base, list):
        if not isinstance(over, list):
            raise InvalidOverride(f"{where}: override is a {type(over).__name__}, base is a list")
        if base and over and all(isinstance(x, dict) and "id" in x for x in base + over):
            out = [dict(x) for x in base]
            idx = {x["id"]: i for i, x in enumerate(out)}
            for x in over:
                if x["id"] in idx:
                    out[idx[x["id"]]] = merge(out[idx[x["id"]]], x, f"{where}[{x['id']}]")
                else:
                    out.append(x)
            return out
        return list(base) + list(over)
    if isinstance(over, (dict, list)):
        raise InvalidOverride(f"{where}: override is a {type(over).__name__}, base is a scalar")
    return over


def _ayaml():
    from agentive import ayaml
    return ayaml


def _header(text):
    out = []
    for ln in text.splitlines():
        if ln.startswith("#") or (not ln.strip() and out):
            out.append(ln)
        else:
            break
    while out and not out[-1].strip():
        out.pop()
    return "\n".join(out) + ("\n" if out else "")


FENCE = re.compile(r"^```([^\n`]*)\n(.*?)^```[ \t]*$", re.S | re.M)


def _merge_text(base, over):
    blocks = {m.group(1).strip(): m for m in FENCE.finditer(base) if m.group(1).strip().startswith("yaml ")}
    rest = over
    for m in list(FENCE.finditer(over)):
        info = m.group(1).strip()
        if info in blocks:
            ay = _ayaml()
            try:
                b, o = ay.loads(blocks[info].group(2)), ay.loads(m.group(2))
            except Exception as e:  # noqa: BLE001
                raise InvalidOverride(f"fenced block {info}: {e}")
            merged = ay.dumps(merge(b, o, info))
            bm = blocks[info]
            base = base[:bm.start(2)] + merged + base[bm.end(2):]
            blocks = {mm.group(1).strip(): mm for mm in FENCE.finditer(base) if mm.group(1).strip().startswith("yaml ")}
            rest = rest.replace(m.group(0), "", 1)
    rest = rest.strip("\n")
    if rest.strip():
        base = base.rstrip("\n") + "\n\n" + rest + "\n"
    return base


def apply_override(path, base_text, over_text):
    """Merged text of a generated file and its .agents/custom/ override (raises InvalidOverride)."""
    low = path.lower()
    try:
        if low.endswith(".json"):
            return json.dumps(merge(json.loads(base_text), json.loads(over_text)), ensure_ascii=False, indent=2) + "\n"
        if low.endswith((".yaml", ".yml")):
            ay = _ayaml()
            return _header(base_text) + ay.dumps(merge(ay.loads(base_text), ay.loads(over_text)))
        if low.endswith(".toml"):
            return _header(base_text) + toml_dumps(merge(toml_loads(base_text), toml_loads(over_text)))
    except InvalidOverride:
        raise
    except Exception as e:  # noqa: BLE001
        raise InvalidOverride(f"does not parse: {e}")
    return _merge_text(base_text, over_text)


# ------------------------------------------------------------------ TOML (overrides of .codex/config.toml)
def toml_loads(text):
    if sys.version_info >= (3, 11):
        import tomllib
        return tomllib.loads(text)
    raise InvalidOverride("TOML overrides need python >= 3.11 (tomllib)")


def _tv(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, list):
        return "[" + ", ".join(_tv(x) for x in v) + "]"
    raise InvalidOverride(f"TOML value {v!r} cannot be written")


def toml_dumps(doc, prefix=""):
    lines, tables = [], []
    for k, v in doc.items():
        if isinstance(v, dict):
            tables.append((k, v))
        else:
            lines.append(f"{k} = {_tv(v)}")
    for k, v in tables:
        name = f"{prefix}{k}"
        sub = toml_dumps(v, name + ".")
        if lines or prefix or tables[0][0] != k:
            lines.append("")
        if any(not isinstance(x, dict) for x in v.values()) or not v:
            lines.append(f"[{name}]")
        lines.append(sub.rstrip("\n"))
    return "\n".join(x for x in lines if x is not None).strip("\n") + "\n"
