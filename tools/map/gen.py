# agentive-unit: gen.map
# design: decisions.defaults[F1] (single source + generator), view, layout
"""Render the framework map page from design/framework.yaml.

Usage: python3 tools/map/gen.py design/framework.yaml tools/map/tpl.html docs/map/index.html
(tools/generate.py runs it into a temporary file; never run it by hand into docs/map/.)

All visible text comes from the YAML (view/spec/decisions/contracts/unverified/
sources). This file holds rendering logic only: no content literals.
"""
import html
import json
import re
import sys

import yaml

warnings = []
Y = yaml.safe_load(open(sys.argv[1], encoding='utf-8'))
V = Y['view']
L = Y['layout']
LB = V['labels']
SRC = {s['id']: s for s in Y['sources']}
SRC_N = {sid: sid[1:] for sid in SRC}


def esc(s):
    return html.escape(str(s), quote=True)


# ---------------------------------------------------------------- text markup
REF_RE = re.compile(r'\{(S\d+|U\d+|D\d+|F\d+|T\d+|UV-\d+|RV\d+b?|ambiguity|unverified|contracts|pipeline|explainer|chain)\}')
TAG_RE = re.compile(r'\[\[(off|des|tbd)(?::([^\]]+))?\]\]')


def _refs_html(t):
    def rep(m):
        k = m.group(1)
        if k in SRC:
            return f'<a class="ref" href="#src-{SRC_N[k]}">[{SRC_N[k]}]</a>'
        if k in V['header']['links']:
            return f'<a class="ref" href="#{k}">{esc(V["header"]["links"][k])}</a>'
        return f'<a class="ref" href="#{k.lower()}">{k}</a>'
    t = REF_RE.sub(rep, t)
    t = TAG_RE.sub(lambda m: f'<span class="tag {m.group(1)}">{esc(m.group(2) or LB["prov"][m.group(1)])}</span>', t)
    t = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', t)
    return t


def rich(text):
    """prose -> HTML: `code`, **bold**, {S5} refs, {U1} anchors, [[off]] tags."""
    parts = re.split(r'(`[^`]+`)', str(text))
    out = []
    for p in parts:
        if p.startswith('`') and p.endswith('`') and len(p) > 1:
            out.append(f'<code>{esc(p[1:-1])}</code>')
        else:
            out.append(_refs_html(esc(p)))
    return ''.join(out)


def plain(text):
    """prose -> SVG text: {S5} -> [5], tags dropped, backticks dropped."""
    t = str(text)
    t = REF_RE.sub(lambda m: f'[{SRC_N[m.group(1)]}]' if m.group(1) in SRC else m.group(1), t)
    t = TAG_RE.sub('', t).replace('`', '')
    return re.sub(r'\*\*(.+?)\*\*', r'\1', t).strip()


def est(text, fs, mono=False):
    w = 0
    for ch in text:
        if '가' <= ch <= '힣' or ch in '·→①②③④⑤≤≥':
            w += fs * 0.96
        elif ch == ' ':
            w += fs * 0.3
        else:
            w += fs * (0.6 if mono else 0.56)
    return w


# ---------------------------------------------------------------- svg primitives
def chips_svg(x_right, y, chips):
    out = []
    cx = x_right
    for label, cls in reversed(chips):
        w = est(label, 12) + 16
        cx -= w
        out.append(f'<rect class="chip {cls}" x="{cx:.0f}" y="{y}" width="{w:.0f}" height="22" rx="11"/>'
                   f'<text class="chip-t" x="{cx + w / 2:.0f}" y="{y + 15}" text-anchor="middle">{esc(label)}</text>')
        cx -= 6
    return ''.join(out)


def norm_line(item):
    if isinstance(item, list):
        return plain(item[0]), item[1]
    return plain(item), 'muted'


def box(item, rect):
    x, y, w, h = rect
    iid = item['id']
    chips = item.get('chips') or []
    first = chips[0][1] if chips else None
    out = [f'<g class="node" data-id="{esc(iid)}">']
    if item.get('kind') == 'actor':
        out.append(f'<rect class="actor" data-id="{esc(iid)}" x="{x}" y="{y}" width="{w}" height="{h}" rx="{min(h / 2, 26):.0f}"/>')
    else:
        scls = {'off': 'b-off', 'des': 'b-des', 'tbd': 'b-tbd'}.get(first, 'b-des')
        out.append(f'<rect class="box {scls}" data-id="{esc(iid)}" x="{x}" y="{y}" width="{w}" height="{h}" rx="8"/>')
    title = plain(item['title'])
    mono = bool(item.get('mono'))
    ty = y + 29
    tw = est(title, 16, mono)
    if tw > w - 26:
        warnings.append(f'TITLE overflow {iid}: {tw:.0f} > {w - 26}')
    out.append(f'<text class="t-title{" mono" if mono else ""}" x="{x + 14}" y="{ty}">{esc(title)}</text>')
    ly = ty + 22
    for raw in item.get('lines', []):
        text, st = norm_line(raw)
        lw = est(text, 13.5, 'mono' in st)
        if lw > w - 26:
            warnings.append(f'LINE overflow {iid}: "{text}" {lw:.0f} > {w - 26}')
        out.append(f'<text class="t-line {st}" x="{x + 14}" y="{ly}">{esc(text)}</text>')
        ly += 19
    if ly - 19 + 8 > y + h:
        warnings.append(f'HEIGHT overflow {iid}: need {ly - 19 + 8 - y} have {h}')
    if chips:
        lab = []
        for c in chips:
            label, prov = plain(c[0]), c[1]
            lab.append((LB['prov'][prov] + (' ' + label if label else ''), 'c-' + prov))
        out.append(chips_svg(x + w - 12, y - 11, lab))
    out.append('</g>')
    return ''.join(out)


def zone(z, rect):
    x, y, w, h = rect
    tag = z['id']
    out = [f'<g class="zone-g" data-id="{tag}"><rect class="zone" data-id="{tag}" x="{x}" y="{y}" width="{w}" height="{h}" rx="16"/>']
    tw = est(tag, 13, True) + 14
    out.append(f'<rect class="ztag" x="{x + 18}" y="{y + 16}" width="{tw:.0f}" height="24" rx="5"/>')
    out.append(f'<text class="ztag-t" x="{x + 18 + tw / 2:.0f}" y="{y + 33}" text-anchor="middle">{esc(tag)}</text>')
    out.append(f'<text class="z-name" x="{x + 18 + tw + 10:.0f}" y="{y + 34}">{esc(z["name"])}</text>')
    if z.get('sub'):
        sub = plain(z['sub'])
        if est(sub, 13.5) > w - 40:
            warnings.append(f'ZONE sub overflow {tag}')
        out.append(f'<text class="z-sub" x="{x + 20}" y="{y + 60}">{esc(sub)}</text>')
    out.append('</g>')
    return ''.join(out)


def arrow_svg(a, geo):
    out = []
    kind = a['kind']
    if geo.get('d'):
        m = '' if a.get('marker') is False else f' marker-end="url(#m-{kind})"'
        out.append(f'<path class="ln ln-{kind}" data-id="{esc(a["id"])}" d="{geo["d"]}"{m}/>')
    if a.get('label') and geo.get('label_at'):
        lx, ly = geo['label_at']
        anchor = geo.get('anchor', 'middle')
        for i, t in enumerate(a['label']):
            rot = f' transform="rotate(90 {lx} {ly})"' if geo.get('vertical') else ''
            out.append(f'<text class="lbl lbl-{kind}" x="{lx}" y="{ly + 19 * i}" text-anchor="{anchor}"{rot}>{esc(plain(t))}</text>')
    return ''.join(out)


def defs(extra=()):
    out = ['<defs>']
    for c in ['run', 'block', 'gen', 'read'] + list(extra):
        out.append(f'<marker id="m-{c}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="9" markerHeight="9" orient="auto-start-reverse">'
                   f'<path class="mk mk-{c}" d="M0,0 L10,5 L0,10 z"/></marker>')
    out.append('</defs>')
    return ''.join(out)


# ---------------------------------------------------------------- main map
def main_map():
    M = V['map']
    LM = L['map']
    S = [defs()]
    for z in M['zones']:
        S.append(zone(z, LM['zones'][z['id']]))
    for zid, heads in M.get('col_heads', {}).items():
        ch = LM['col_heads'][zid]
        for x, h in zip(ch['xs'], heads):
            S.append(f'<text class="col-h" x="{x}" y="{ch["y"]}">{esc(h)}</text>')
    for it in M['items']:
        S.append(box(it, LM['items'][it['id']]))
    for a in M['arrows']:
        S.append(arrow_svg(a, LM['arrows'][a['id']]))
    w, h = LM['viewBox']
    return (f'<svg class="dia dia-main" viewBox="0 0 {w} {h}" role="img" aria-label="{esc(V["map_aria"])}">'
            + ''.join(S) + '</svg>')


def apply_map():
    A = V['apply']
    LA = L['apply']
    S = [defs()]
    for st in A['steps']:
        geo = LA['items'][st['id']]
        if st.get('kind') == 'heading':
            x, y = geo['text_at']
            S.append(f'<text class="z-name" data-id="{st["id"]}" x="{x}" y="{y}" text-anchor="middle">{esc(st["title"])}</text>')
        else:
            S.append(box(st, geo))
    for a in A['arrows']:
        S.append(arrow_svg(a, LA['arrows'][a['id']]))
    w, h = LA['viewBox']
    return (f'<svg class="dia dia-apply" viewBox="0 0 {w} {h}" role="img" aria-label="{esc(A["aria"])}">'
            + ''.join(S) + '</svg>')


# ---------------------------------------------------------------- legends
SW = {
    'box-off': '<rect x="2" y="3" width="48" height="18" rx="4" fill="var(--sheet)" stroke="var(--off)" stroke-width="2"/>',
    'box-des': '<rect x="2" y="3" width="48" height="18" rx="4" fill="var(--sheet)" stroke="var(--des)" stroke-width="2" stroke-dasharray="7 5"/>',
    'box-tbd': '<rect x="2" y="3" width="48" height="18" rx="4" fill="var(--sheet)" stroke="var(--tbd)" stroke-width="2" stroke-dasharray="3 4"/>',
    'box-actor': '<rect x="2" y="3" width="48" height="18" rx="9" fill="var(--actor-bg)" stroke="var(--muted)" stroke-width="1.2"/>',
    'ln-run': '<line x1="2" y1="12" x2="42" y2="12" stroke="var(--run)" stroke-width="2"/><path d="M40,6 L50,12 L40,18 z" fill="var(--run)"/>',
    'ln-block': '<line x1="2" y1="12" x2="42" y2="12" stroke="var(--block)" stroke-width="2" stroke-dasharray="8 5"/><path d="M40,6 L50,12 L40,18 z" fill="var(--block)"/>',
    'ln-gen': '<line x1="2" y1="12" x2="42" y2="12" stroke="var(--gen)" stroke-width="2"/><path d="M40,6 L50,12 L40,18 z" fill="var(--gen)"/>',
    'ln-read': '<line x1="2" y1="12" x2="42" y2="12" stroke="var(--read)" stroke-width="2" stroke-dasharray="3 5"/><path d="M40,6 L50,12 L40,18 z" fill="var(--read)"/>',
    'zone': '<rect x="2" y="2" width="48" height="20" rx="6" fill="var(--zone)" stroke="var(--rule)" stroke-width="1.5"/><rect x="6" y="6" width="16" height="12" rx="2" fill="var(--ink)"/>',
    'pv-det': '<rect x="2" y="3" width="48" height="18" rx="4" fill="var(--sheet)" stroke="var(--run)" stroke-width="2"/>',
    'pv-llm': '<rect x="2" y="3" width="48" height="18" rx="4" fill="var(--sheet)" stroke="var(--llm)" stroke-width="2"/>',
    'pv-gate': '<rect x="2.5" y="3.5" width="47" height="17" rx="4" fill="var(--sheet)" stroke="var(--run)" stroke-width="3.5"/>',
    'pv-off': '<rect x="2" y="3" width="48" height="18" rx="4" fill="none" stroke="var(--muted)" stroke-width="1.5" stroke-dasharray="2 4"/>',
    'pv-file': '<rect x="2" y="3" width="48" height="18" rx="4" fill="var(--actor-bg)" stroke="var(--muted)" stroke-width="1.5"/>',
    'pv-pass': '<rect x="2" y="3" width="48" height="18" rx="4" fill="var(--sheet)" stroke="var(--gen)" stroke-width="2"/>',
    'pv-human': '<rect x="2.5" y="3.5" width="47" height="17" rx="4" fill="var(--human-bg)" stroke="var(--human)" stroke-width="3"/>',
    'pv-block': '<rect x="2" y="3" width="48" height="18" rx="4" fill="var(--sheet)" stroke="var(--block)" stroke-width="2" stroke-dasharray="8 5"/>',
    'pv-agent': '<rect x="2" y="3" width="48" height="18" rx="4" fill="var(--sheet)" stroke="var(--gen)" stroke-width="2.5"/>',
    'ln-skip': '<line x1="2" y1="12" x2="42" y2="12" stroke="var(--muted)" stroke-width="2" stroke-dasharray="2 4"/><path d="M40,6 L50,12 L40,18 z" fill="var(--muted)"/>',
    'ex-same': '<rect x="2" y="3" width="48" height="18" rx="4" fill="var(--sheet)" stroke="var(--muted)" stroke-width="1.5"/>',
    'ex-add': '<rect x="2.5" y="3.5" width="47" height="17" rx="4" fill="var(--add-bg)" stroke="var(--gen)" stroke-width="2.5"/>',
    'ex-chg': '<rect x="2.5" y="3.5" width="47" height="17" rx="4" fill="var(--chg-bg)" stroke="var(--chg)" stroke-width="2.5"/>',
    'ex-rem': '<rect x="2" y="3" width="48" height="18" rx="4" fill="none" stroke="var(--block)" stroke-width="2" stroke-dasharray="7 5"/>',
}
SW_LOOP = {
    'nd-auto': '<rect x="2" y="3" width="36" height="14" rx="3" fill="var(--sheet)" stroke="var(--gen)" stroke-width="2"/>',
    'nd-human': '<rect x="2" y="3" width="36" height="14" rx="3" fill="var(--human-bg)" stroke="var(--human)" stroke-width="3"/>',
    'nd-input': '<rect x="2" y="3" width="36" height="14" rx="7" fill="var(--actor-bg)" stroke="var(--muted)" stroke-width="1.2"/>',
    'nd-off': '<rect x="2" y="3" width="36" height="14" rx="3" fill="none" stroke="var(--muted)" stroke-width="1.5" stroke-dasharray="2 4"/>',
    'lp-run': '<line x1="2" y1="10" x2="30" y2="10" stroke="var(--run)" stroke-width="2"/><path d="M28,4 L38,10 L28,16 z" fill="var(--run)"/>',
    'lp-block': '<line x1="2" y1="10" x2="30" y2="10" stroke="var(--block)" stroke-width="2" stroke-dasharray="6 4"/><path d="M28,4 L38,10 L28,16 z" fill="var(--block)"/>',
    'lp-skip': '<line x1="2" y1="10" x2="30" y2="10" stroke="var(--muted)" stroke-width="2" stroke-dasharray="2 4"/><path d="M28,4 L38,10 L28,16 z" fill="var(--muted)"/>',
}


def main_legend():
    o = [f'<div class="legend" aria-label="{esc(LB["legend"])}">']
    for g in V['legend']['groups']:
        o.append(f'<div class="lg-group"><h3>{esc(g["h"])}</h3>')
        for it in g['items']:
            o.append(f'<div class="lg-item"><svg viewBox="0 0 52 24" aria-hidden="true">{SW[it["sw"]]}</svg><span>{rich(it["text"])}</span></div>')
        if g.get('note'):
            o.append(f'<p class="note">{rich(g["note"])}</p>')
        o.append('</div>')
    o.append('</div>')
    return ''.join(o)


def loop_legend():
    o = [f'<div class="lg-mini" aria-label="{esc(LB["legend"])}">']
    for it in V['legend']['loop']:
        o.append(f'<span class="lgm"><svg viewBox="0 0 40 20" aria-hidden="true">{SW_LOOP[it["sw"]]}</svg>{rich(it["text"])}</span>')
    o.append('</div>')
    return ''.join(o)


# ---------------------------------------------------------------- loop diagram (U cards)
def loop_svg(mid, stages, block, auto, human, aria, after=None, after_label=None, after_from=None):
    LL = L['loop']
    W = LL['width']
    n = len(stages)
    gap = LL['node']['gap_many'] if n > 5 else LL['node']['gap_few']
    sw = (W - 60 - (n - 1) * gap) / n
    y, h = LL['node']['y'], LL['node']['h']
    out = [f'<svg class="dia dia-loop" viewBox="0 0 {W} {{H}}" role="img" aria-label="{esc(aria)}">', '<defs>']
    for c in ['run', 'block', 'skip']:
        out.append(f'<marker id="{mid}-{c}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="9" markerHeight="9" orient="auto-start-reverse"><path class="mk-{c}" d="M0,0 L10,5 L0,10 z"/></marker>')
    out.append('</defs>')

    def node(x, yy, w, hh, st, chip=None):
        title, lines, state = st['title'], st.get('lines', []), st['state']
        o = []
        rx = 26 if state == 'input' else 8
        o.append(f'<rect class="nd nd-{state}" x="{x:.0f}" y="{yy}" width="{w:.0f}" height="{hh}" rx="{rx}"/>')
        if est(title, 15) > w - 22:
            warnings.append(f'LOOP title overflow {mid}: {title}')
        o.append(f'<text class="nd-t{" nd-dim" if state == "off" else ""}" x="{x + 12:.0f}" y="{yy + 28}">{esc(title)}</text>')
        ly = yy + 49
        for ln in lines:
            if est(ln, 13) > w - 22:
                warnings.append(f'LOOP line overflow {mid}: {ln}')
            o.append(f'<text class="nd-s" x="{x + 12:.0f}" y="{ly}">{esc(ln)}</text>')
            ly += 19
        if ly - 19 + 10 > yy + hh:
            warnings.append(f'LOOP height overflow {mid}: {title}')
        if chip:
            cw = est(chip, 12) + 16
            o.append(f'<rect class="ndc ndc-{state}" x="{x + w - cw - 10:.0f}" y="{yy - 11}" width="{cw:.0f}" height="22" rx="11"/>')
            o.append(f'<text class="ndc-t ndc-t-{state}" x="{x + w - 10 - cw / 2:.0f}" y="{yy + 4}" text-anchor="middle">{esc(chip)}</text>')
        return ''.join(o)

    chipname = LB['node_chip']
    xs = []
    for i, st in enumerate(stages):
        x = 30 + i * (sw + gap)
        xs.append(x)
        out.append(node(x, y, sw, h, st, chipname.get(st['state']) if st['state'] in ('human', 'off') else None))
        if i > 0:
            cls = 'skip' if st['state'] == 'off' else 'run'
            out.append(f'<path class="lp lp-{cls}" d="M{xs[i - 1] + sw:.0f},{y + h // 2} H{x - 2:.0f}" marker-end="url(#{mid}-{cls})"/>')
    if block:
        fx = xs[block['from']] + sw / 2 + 20
        tx = xs[block['to']] + sw / 2 + 20
        top = y - 30
        out.append(f'<path class="lp lp-block" d="M{fx:.0f},{y - 13} V{top} H{tx:.0f} V{y - 13}" marker-end="url(#{mid}-block)"/>')
        out.append(f'<text class="lp-l lp-l-block" x="{(fx + tx) / 2:.0f}" y="{top - 8}" text-anchor="middle">{esc(block["label"])}</text>')
    tw_, th = LL['branch']['w'], LL['branch']['h']
    ax = W - 30 - 2 * tw_ - 40
    hx = W - 30 - tw_
    acx, hcx = ax + tw_ / 2, hx + tw_ / 2
    ox = xs[-1] + sw / 2
    bar = y + h + 44
    ty = bar + 44
    out.append(f'<path class="lp lp-run" d="M{ox:.0f},{y + h} V{bar} H{hcx:.0f}"/>')
    hcls = 'skip' if human['state'] == 'off' else 'run'
    acls = 'skip' if auto['state'] == 'off' else 'run'
    out.append(f'<path class="lp lp-{hcls}" d="M{hcx:.0f},{bar} V{ty - 2}" marker-end="url(#{mid}-{hcls})"/>')
    out.append(f'<path class="lp lp-{acls}" d="M{hcx:.0f},{bar} H{acx:.0f} V{ty - 2}" marker-end="url(#{mid}-{acls})"/>')
    out.append(f'<circle class="lp-dot" cx="{hcx:.0f}" cy="{bar}" r="4"/>')
    for x, st in ((ax, auto), (hx, human)):
        out.append(node(x, ty, tw_, th, st, chipname.get(st['state'])))
    H = ty + th + 20
    if after:
        bar2 = ty + th + 40
        ty2 = bar2 + 46
        n2 = len(after)
        span = hx + tw_ - ax
        w2 = (span - (n2 - 1) * 40) / n2
        scx = hcx if after_from == 'human' else acx
        out.append(f'<path class="lp lp-run" d="M{scx:.0f},{ty + th} V{bar2}"/>')
        xs2 = [ax + i * (w2 + 40) for i in range(n2)]
        cxs = [x + w2 / 2 for x in xs2]
        out.append(f'<path class="lp lp-run" d="M{min(cxs + [scx]):.0f},{bar2} H{max(cxs + [scx]):.0f}"/>')
        out.append(f'<circle class="lp-dot" cx="{scx:.0f}" cy="{bar2}" r="4"/>')
        if after_label:
            out.append(f'<text class="lp-l" x="{scx + 10:.0f}" y="{ty + th + 26}">{esc(after_label)}</text>')
        for x, cx, st in zip(xs2, cxs, after):
            out.append(f'<path class="lp lp-run" d="M{cx:.0f},{bar2} V{ty2 - 2}" marker-end="url(#{mid}-run)"/>')
            out.append(node(x, ty2, w2, th, st, chipname.get(st['state']) if st['state'] in ('human', 'off') else None))
        H = ty2 + th + 20
    out.append('</svg>')
    return ''.join(out).replace('{H}', str(H))


def meter(vals, note=None):
    rows = list(zip(LB['meter_rows'], ['human', 'speed', 'risk'], vals))
    o = [f'<div class="meter"><div class="meter-h">{esc(LB["meter_head"])} <span>{esc(LB["meter_note"])}</span></div>']
    for k, cls, v in rows:
        segs = ''.join(f'<i class="{"on" if i < v else ""}"></i>' for i in range(5))
        lv = LB['levels'][v - 1]
        o.append(f'<div class="m-row"><span class="m-k">{esc(k)}</span><span class="m-bar m-{cls}" role="img" aria-label="{esc(k)} {esc(lv)}">{segs}</span><span class="m-v">{esc(lv)}</span></div>')
    if note:
        o.append(f'<p class="note">{rich(note)}</p>')
    o.append('</div>')
    return ''.join(o)


# ---------------------------------------------------------------- decision cards
def snippet(u, k):
    rec = u['record']
    path = rec['path']
    lines = [f'# {rec["file"]}']
    ind = ''
    for p in path:
        lines.append(f'{ind}{p}:')
        ind += '  '
    if u.get('status') == 'decided':
        lines += [f'{ind}value: {u["value"]}', f'{ind}decided_by: {u["decided_by"]}', f'{ind}decided_at: "{u["decided_at"]}"']
        if rec.get('conditions'):
            lines.append(f'{ind}conditions: [{", ".join(rec["conditions"])}]')
        if rec.get('confirm_scope'):
            lines.append(f'{ind}confirm_scope: [{", ".join(rec["confirm_scope"])}]')
        if rec.get('non_interactive'):
            lines.append(f'{ind}non_interactive: {rec["non_interactive"]}')
        if rec.get('self_tests'):
            lines.append(f'{ind}self_tests: [{", ".join(rec["self_tests"])}]')
        if rec.get('criteria_file'):
            lines.append(f'{ind}criteria_file: {rec["criteria_file"]}')
        if rec.get('referenced_from'):
            lines.append(f'{ind}referenced_from: {rec["referenced_from"]}')
        if rec.get('managed_code_review_copy_default') is not None:
            lines.append(f'{ind}managed_code_review_copy_default: {"true" if rec["managed_code_review_copy_default"] else "false"}')
        return '\n'.join(lines)
    lines += [f'{ind}value: {k}', f'{ind}decided_by: user', f'{ind}decided_at: <ISO 8601>']
    return '\n'.join(lines)


def u_card(u):
    uid = u['id']
    decided = u['status'] == 'decided'
    sel = u['value'] if decided else u['recommended']
    o = [f'<article class="sheet item{" decided" if decided else ""}" id="{uid.lower()}" data-id="{uid}" data-status="{u["status"]}">']
    bd = f'<span class="bd bd-done">{esc(LB["badge"]["decided"])}</span>' if decided else f'<span class="bd bd-user">{esc(LB["badge"]["user"])}</span>'
    o.append(f'<div class="item-h"><span class="iid">{uid}</span><h3>{esc(u["title"])}</h3>{bd}</div>')
    if decided:
        st = LB['status_decided'].format(value=u['value'], by=u['decided_by'], at=u['decided_at'])
        meta = f'<span class="status status-done">{esc(st)}</span>'
    else:
        meta = f'<span class="status">{esc(LB["status_undecided"])}</span>'
    if u.get('source_finding'):
        meta += f' <span class="src-find">{esc(LB["source_finding"])} {esc(u["source_finding"])}</span>'
    o.append(f'<div class="opt-h">{meta}</div>')
    o.append(f'<div class="why"><b>{esc(LB["why_decided"] if decided else LB["why_user"])}</b><p>{rich(u["why"])}</p></div>')
    if decided:
        o.append(f'<div class="decision"><p>{rich(u["decision_text"])}</p>')
        if u.get('condition_items'):
            o.append(f'<b class="cond-h">{esc(u.get("condition_head") or LB["conditions_head"])}</b><ol class="conds">')
            for c in u['condition_items']:
                o.append(f'<li id="{c["id"].lower().replace(".", "-")}" data-id="{c["id"]}"><b>{esc(c["h"])}</b><span>{rich(c["v"])}</span></li>')
            o.append('</ol>')
        if u.get('non_interactive_items'):
            o.append(f'<b class="cond-h">{esc(u["non_interactive_head"])}</b><ol class="conds">')
            for c in u['non_interactive_items']:
                o.append(f'<li id="{c["id"].lower().replace(".", "-")}" data-id="{c["id"]}"><b>{esc(c["h"])}</b><span>{rich(c["v"])}</span></li>')
            o.append('</ol>')
        for lst in u.get('extra_lists') or []:
            o.append(f'<b class="cond-h">{esc(lst["head"])}</b><ol class="conds">')
            for c in lst['items']:
                o.append(f'<li id="{c["id"].lower().replace(".", "-")}" data-id="{c["id"]}"><b>{esc(c["h"])}</b><span>{rich(c["v"])}</span></li>')
            o.append('</ol>')
        o.append('</div>')
    if u.get('matrix'):
        mx = u['matrix']
        th = ''.join(f'<th>{esc(c)}</th>' for c in [''] + mx['cols'])
        trs = ''.join(f'<tr><td>{esc(k)}{" (" + esc(LB["decided_em"] if decided else LB["rec"]) + ")" if k == sel else ""}</td>' + ''.join(f'<td>{rich(c)}</td>' for c in cells) + '</tr>' for k, cells in mx['rows'].items())
        o.append(f'<div class="matrix"><b>{esc(u.get("matrix_head") or LB["matrix_head"])}</b><div class="tblwrap"><table class="data mx"><thead><tr>{th}</tr></thead><tbody>{trs}</tbody></table></div></div>')
    note = LB['decided_note'] if decided else LB['options_note']
    o.append(f'<div class="opt-h"><b>{esc(LB["options"])}</b> <span class="tag des">{esc(LB["design_tag"])}</span> <span class="note">{esc(note)} {esc(LB["loop_prefix"])} {esc(u["loop"])}</span></div>')
    o.append(f'<div class="opts" role="group" aria-label="{uid} {esc(LB["options"])}" data-group="{uid}">')
    for op in u['options']:
        is_rec = op['k'] == sel
        rec_em = (f' <em class="em-done">{esc(LB["decided_em"])}</em>' if decided else f' <em>{esc(LB["rec"])}</em>') if is_rec else ''
        o.append(f'<button type="button" class="opt" id="{uid}-{op["k"]}-btn" data-k="{op["k"]}" data-value="{op["k"]}" aria-pressed="{"true" if is_rec else "false"}">'
                 f'<span class="opt-k">{op["k"]}{rec_em}</span><span class="opt-n">{esc(op["name"])}</span><span class="opt-s">{esc(op["short"])}</span></button>')
    o.append('</div>')
    for op in u['options']:
        is_rec = op['k'] == sel
        hidden = '' if is_rec else ' hidden'
        o.append(f'<div class="opt-panel" data-panel="{op["k"]}"{hidden}>')
        if u.get('stages'):
            st = [dict(s) for s in u['stages']]
            for i, v in (op.get('stage_override') or {}).items():
                st[int(i)] = v
            mid = f'{uid}{op["k"]}'.lower()
            svg = loop_svg(mid, st, u.get('block'), op['auto'], op['human'], f'{uid} {op["k"]}: {op["name"]}. {plain(op["desc"])}',
                           op.get('after'), op.get('after_label'), op.get('after_from'))
            o.append(f'<figure class="loopfig"><div class="scroll">{svg}</div>{loop_legend()}</figure>')
        tail = ''
        if is_rec:
            tail = ' (' + esc(LB['decided_em']) + ')' if decided else ' ' + esc(LB['rec_paren'])
        desc = f'<div class="opt-desc"><b>{op["k"]} · {esc(op["name"])}{tail}</b><p>{rich(op["desc"])}</p></div>'
        o.append(f'<div class="opt-detail">{desc}{meter(op["meter"], op.get("meter_note")) if op.get("meter") else ""}</div>')
        o.append('</div>')
    scope_note = ''
    if u['record']['file'] != '.agents/decisions.yaml':
        scope_note = ' ' + LB['apply_scope_note']
    fixed = ' data-fixed="1"' if decided else ''
    o.append(f'<div class="apply" data-apply="{uid}"><div class="apply-h"><b>{esc(LB["apply_label"])}</b>'
             f'<button type="button" class="copy" data-copy="{uid}" data-label="{esc(LB["copy"])}" data-done="{esc(LB["copied"])}" data-fail="{esc(LB["copy_fail"])}">{esc(LB["copy"])}</button></div>'
             f'<p class="note">{rich(LB["decided_apply_text"] if decided else LB["apply_text"])} <code>{esc(u["record"]["file"])}</code>{rich(scope_note)}</p>'
             f'<pre><code id="snip-{uid}"{fixed} data-tpl="{esc(json.dumps(u["record"], ensure_ascii=False))}">{esc(snippet(u, sel))}</code></pre></div>')
    o.append('</article>')
    return ''.join(o)


def resolved_card(u):
    uid = u['id']
    by = u['resolved_by']
    o = [f'<article class="sheet item resolved" id="{uid.lower()}" data-id="{uid}" data-status="{u["status"]}" data-resolved-by="{by}">']
    bd = f'<span class="bd bd-res">{esc(LB["badge_resolved"].format(by=by))}</span>'
    o.append(f'<div class="item-h"><span class="iid">{uid}</span><h3>{esc(u["title"])}</h3>{bd}</div>')
    st = LB['status_resolved'].format(by=by, at=u['resolved_at'])
    meta = f'<span class="status status-res">{esc(st)}</span>'
    if u.get('source_finding'):
        meta += f' <span class="src-find">{esc(LB["source_finding"])} {esc(u["source_finding"])}</span>'
    o.append(f'<div class="opt-h">{meta}</div>')
    o.append(f'<div class="decision decision-res"><b class="cond-h">{esc(LB["resolution_head"])}</b><p>{rich(u["resolution_text"])}</p></div>')
    o.append(f'<div class="why"><b>{esc(LB["why_resolved"])}</b><p>{rich(u["why"])}</p></div>')
    o.append(f'<div class="opt-h"><b>{esc(LB["options"])}</b> <span class="note">{esc(LB["resolved_note"])} {esc(LB["loop_prefix"])} {esc(u["loop"])}</span></div>')
    o.append('<ul class="res-opts">')
    for op in u['options']:
        o.append(f'<li><span class="opt-k">{op["k"]}</span><div><b>{esc(op["name"])}</b> <span class="opt-s">{esc(op["short"])}</span><p>{rich(op["desc"])}</p></div></li>')
    o.append('</ul></article>')
    return ''.join(o)


def mf_nodes(nodes, used):
    o = ['<ol class="mf-flow">']
    for nd in nodes:
        if isinstance(nd, dict):
            conn = nd.get('c', 'solid')
            used.add('c-' + conn)
            o.append(f'<li class="mf-li c-{conn}"><div class="mf-split">')
            for text, st in nd['split']:
                used.add(st)
                o.append(f'<div class="mf-n s-{st}">{rich(text)}</div>')
            o.append('</div></li>')
        else:
            text, st = nd[0], nd[1]
            conn = nd[2] if len(nd) > 2 else 'solid'
            used.add(st)
            used.add('c-' + conn)
            o.append(f'<li class="mf-li c-{conn}"><div class="mf-n s-{st}">{rich(text)}</div></li>')
    o.append('</ol>')
    return ''.join(o)


MF_SW = {'auto': 'mf-sw s-auto', 'human': 'mf-sw s-human', 'file': 'mf-sw s-file', 'off': 'mf-sw s-off',
         'fail': 'mf-sw s-fail', 'probe': 'mf-sw s-probe', 'c-solid': 'mf-ln', 'c-dash': 'mf-ln dash'}


def mini_legend(used):
    o = [f'<div class="lg-mini sm" aria-label="{esc(LB["legend"])}">']
    for k in ['auto', 'human', 'file', 'probe', 'off', 'fail', 'c-solid', 'c-dash']:
        if k in used:
            o.append(f'<span class="lgm"><i class="{MF_SW[k]}"></i>{esc(V["legend"]["mini"][k])}</span>')
    o.append('</div>')
    return ''.join(o)


def d_card(d):
    did = d['id']
    o = [f'<article class="sheet item d-item" id="{did.lower()}" data-id="{did}">']
    o.append(f'<div class="item-h"><span class="iid">{did}</span><h3>{esc(d["title"])}</h3><span class="bd bd-def">{esc(LB["badge"]["default"])}</span></div>')
    o.append(f'<p class="keyline">{esc(LB["key_label"])}: <code>{esc(d["key"])}</code></p>')
    o.append(f'<div class="why"><b>{esc(LB["why_default"])}</b><p>{rich(d["why"])}</p></div>')
    o.append('<div class="ba">')
    for label, data, cls in ((LB['default_col'], d['rec'], 'rec'), (LB['alt_col'], d['alt'], 'alt')):
        used = {'c-solid'}
        flow = mf_nodes(data['flow'], used)
        em = f' <em>{esc(LB["rec"])}</em>' if cls == 'rec' else ''
        o.append(f'<div class="ba-col ba-{cls}"><div class="ba-h"><span class="ba-k">{esc(label)}</span>{em} <span class="tag des">{esc(LB["design_tag"])}</span></div>'
                 f'<b class="ba-n">{esc(data["name"])}</b>{flow}{mini_legend(used)}<p class="ba-e">{rich(data["effect"])}</p></div>')
    o.append('</div></article>')
    return ''.join(o)


def t_card(t):
    used = {'probe', 'auto', 'fail', 'file', 'c-solid'}
    pre = ''
    if t.get('pre'):
        pre = f'<li class="mf-li c-solid"><div class="mf-n s-file"><span class="mf-k">{esc(LB["probe_pre"])}</span>{rich(t["pre"])}</div></li>'
    flow = ('<ol class="mf-flow">' + pre +
            f'<li class="mf-li c-solid"><div class="mf-n s-probe"><span class="mf-k">{esc(LB["probe_k"])}</span>{rich(t["probe"])}</div></li>'
            '<li class="mf-li c-solid"><div class="mf-split">'
            f'<div class="mf-n s-auto"><span class="mf-k">{esc(LB["probe_ok"])}</span>{rich(t["ok"])}</div>'
            f'<div class="mf-n s-fail"><span class="mf-k">{esc(LB["probe_ng"])}</span>{rich(t["ng"])}</div>'
            '</div></li>'
            f'<li class="mf-li c-solid"><div class="mf-n s-file">{esc(LB["probe_record"])}</div></li></ol>')
    return (f'<article class="sheet item t-item" id="{t["id"].lower()}" data-id="{t["id"]}">'
            f'<div class="item-h"><span class="iid">{t["id"]}</span><h3>{esc(t["title"])}</h3><span class="bd bd-probe">{esc(LB["badge"]["probe"])}</span></div>'
            f'<div class="why"><b>{esc(LB["why_probe"])}</b><p>{rich(t["why"])}</p></div>'
            f'<div class="t-flow"><div class="ba-h"><span class="ba-k">{esc(LB["probe_flow"])}</span> <span class="tag des">{esc(LB["design_tag"])}</span></div>{flow}{mini_legend(used)}</div>'
            '</article>')


def idx(items, bdcls, label, desc):
    lis = ''.join(f'<li><a href="#{i["id"].lower()}"><span class="iid">{i["id"]}</span>{esc(i["title"])}</a></li>' for i in items)
    return (f'<div class="sheet idx"><div class="idx-h"><span class="bd {bdcls}">{esc(label)}</span><span class="idx-n">{len(items)}</span></div>'
            f'<p class="note">{rich(desc)}</p><ul>{lis}</ul></div>')


def ambiguity():
    D = Y['decisions']
    S = V['sections']['ambiguity']
    o = ['<section id="ambiguity">']
    o.append(f'<div class="sec-head"><h2>{esc(S["h2"])}</h2><p>{rich(S["p"])}</p></div>')
    pr = V['principle']
    o.append(f'<div class="principle sheet"><b>{esc(pr["b"])}</b><p>{rich(pr["p"])}</p></div>')
    o.append('<div class="idx-grid">')
    if D.get('decided'):
        o.append(idx(D['decided'], 'bd-done', LB['badge']['decided'], V['index_desc']['decided']))
    if D.get('user'):
        o.append(idx(D['user'], 'bd-user', LB['badge']['user'], V['index_desc']['user']))
    if D.get('resolved'):
        o.append(idx(D['resolved'], 'bd-res', LB['badge']['resolved'], V['index_desc']['resolved']))
    o.append(idx(D['defaults'], 'bd-def', LB['badge']['default'], V['index_desc']['default']))
    o.append(idx(D['probes'], 'bd-probe', LB['badge']['probe'], V['index_desc']['probe']))
    o.append('</div>')
    rem = [r for r in D.get('removed', []) if r.get('page_note')]
    if rem:
        lis = ''.join(f'<li id="{r["id"].lower()}"><span class="iid">{esc(r["id"])}</span><b>{esc(r["title"])}</b><p class="note">{rich(r["page_note"])}</p></li>' for r in rem)
        o.append(f'<div class="sheet idx idx-removed"><div class="idx-h"><span class="bd bd-res">{esc(LB["badge"]["removed"])}</span><span class="idx-n">{len(rem)}</span></div>'
                 f'<p class="note">{rich(V["index_desc"]["removed"])}</p><ul class="rem">{lis}</ul></div>')
    gh = V['group_heads']
    if D.get('decided'):
        o.append(f'<h3 class="grp-h"><span class="bd bd-done">{esc(LB["badge"]["decided"])}</span> {esc(gh["decided"])}</h3>')
        o.append(''.join(u_card(u) for u in D['decided']))
    if D.get('user'):
        o.append(f'<h3 class="grp-h"><span class="bd bd-user">{esc(LB["badge"]["user"])}</span> {esc(gh["user"])}</h3>')
        o.append(''.join(u_card(u) for u in D['user']))
    if D.get('resolved'):
        o.append(f'<h3 class="grp-h"><span class="bd bd-res">{esc(LB["badge"]["resolved"])}</span> {esc(gh["resolved"])}</h3>')
        o.append(''.join(resolved_card(u) for u in D['resolved']))
    o.append(f'<h3 class="grp-h"><span class="bd bd-def">{esc(LB["badge"]["default"])}</span> {esc(gh["default"])}</h3>')
    o.append('<div class="d-grid">' + ''.join(d_card(d) for d in D['defaults']) + '</div>')
    o.append(f'<h3 class="grp-h"><span class="bd bd-probe">{esc(LB["badge"]["probe"])}</span> {esc(gh["probe"])}</h3>')
    o.append('<div class="t-grid">' + ''.join(t_card(t) for t in D['probes']) + '</div>')
    o.append('</section>')
    return ''.join(o)


# ---------------------------------------------------------------- other sections
def header():
    H = V['header']
    o = ['<header>', f'<div class="eyebrow">{esc(H["eyebrow"])}</div>', f'<h1>{esc(H["h1"])}</h1>']
    o += [f'<p class="lede">{rich(p)}</p>' for p in H['lede']]
    o.append('</header>')
    return ''.join(o)


def sec_head(key, sid=None):
    s = V['sections'][key]
    p = f'<p>{rich(s["p"])}</p>' if s.get('p') else ''
    return f'<div class="sec-head"><h2>{esc(s["h2"])}</h2>{p}</div>'


def map_section():
    return ('<section id="map">' + sec_head('map') +
            f'<figure class="sheet"><div class="scroll">{main_map()}</div>{main_legend()}'
            f'<figcaption>{rich(V["map_caption"])}</figcaption></figure></section>')


def roles_section():
    o = ['<section id="roles">', sec_head('roles'), '<div class="roles">']
    for r in V['role_cards']:
        tag = f' <span class="tag {r["tag"]}">{esc(LB["prov"][r["tag"]])}</span>'
        ps = ''.join(f'<p>{rich(p)}</p>' for p in r['p'])
        note = f'<p class="note">{rich(r["note"])}</p>' if r.get('note') else ''
        o.append(f'<div class="sheet role" id="{r["id"]}" data-id="{r["id"]}"><span class="k">{esc(r["k"])}</span><h3>{esc(r["h"])}{tag}</h3>{ps}{note}</div>')
    o.append('</div></section>')
    return ''.join(o)


def loop_section():
    o = ['<section id="loop-contract">', sec_head('loop'), '<dl class="sheet lc-list">']
    for it in V['loop_contract']:
        o.append(f'<div class="lc" data-id="{it["id"]}"><dt>{esc(it["k"])} <span class="tag {it["tag"]}">{esc(LB["prov"][it["tag"]])}</span></dt><dd>{rich(it["v"])}</dd></div>')
    o.append('</dl>')
    if V.get('explainer'):
        o.append(explainer_block())
    o.append('</section>')
    return ''.join(o)


# ---------------------------------------------------------------- tier-2 explainer example (Before / After)
EX_STATE = {'same': 'ex-same', 'added': 'ex-add', 'changed': 'ex-chg', 'removed': 'ex-rem'}


def ex_box(nd, rect, chip_names):
    x, y, w, h = rect
    st = nd.get('state', 'same')
    rx = min(h / 2, 26) if nd.get('shape') == 'actor' else 8
    cls = EX_STATE[st] + (' ex-actor' if nd.get('shape') == 'actor' and st == 'same' else '')
    out = [f'<g class="node" data-id="{esc(nd["id"])}" data-state="{st}"><rect class="exn {cls}" x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx:.0f}"/>']
    title = plain(nd['title'])
    ty = y + 29
    if est(title, 16) > w - 26:
        warnings.append(f'EX title overflow {nd["id"]}')
    out.append(f'<text class="t-title{" ex-dim" if st == "removed" else ""}" x="{x + 14}" y="{ty}">{esc(title)}</text>')
    ly = ty + 22
    for raw in nd.get('lines', []):
        text, s = norm_line(raw)
        if est(text, 13.5, 'mono' in s) > w - 26:
            warnings.append(f'EX line overflow {nd["id"]}: "{text}"')
        out.append(f'<text class="t-line {s}" x="{x + 14}" y="{ly}">{esc(text)}</text>')
        ly += 19
    if ly - 19 + 8 > y + h:
        warnings.append(f'EX height overflow {nd["id"]}')
    if st in chip_names:
        out.append(chips_svg(x + w - 12, y - 11, [(chip_names[st], 'c-ex' + st)]))
    out.append('</g>')
    return ''.join(out)


def ex_svg(part):
    E = V['explainer']
    LE = L['explainer']
    S = [defs()]
    for a in part['arrows']:
        S.append(arrow_svg(a, LE['arrows'][a['id']]))
    for nd in part['nodes']:
        S.append(ex_box(nd, LE['items'][nd['id']], E['state_chip']))
    for a in part['arrows']:
        geo = LE['arrows'][a['id']]
        lx, ly = geo.get('label_at', (0, 0))
        for t_ in a.get('label', []):
            if lx + (est(plain(t_), 13) if geo.get('anchor') == 'start' else est(plain(t_), 13) / 2) > LE['viewBox'][0]:
                warnings.append(f'EX label overflow {a["id"]}')
    w, h = LE['viewBox']
    return (f'<svg class="dia dia-ex" viewBox="0 0 {w} {h}" role="img" aria-label="{esc(part["aria"])}">'
            + ''.join(S) + '</svg>')


def explainer_block():
    E = V['explainer']
    o = [f'<div class="sheet ex" id="{E["id"]}" data-id="{E["id"]}">']
    o.append(f'<div class="ex-h"><h3>{rich(E["h3"])} <span class="tag {E["tag"]}">{esc(E["tag_label"])}</span></h3><p class="note">{rich(E["intro"])}</p></div>')
    items = ''.join(f'<li class="{"ex-mark" if it.get("mark") else ""}"><code>{esc(it["text"])}</code>'
                    + (f'<span>{esc(it["note"])}</span>' if it.get('note') else '') + '</li>' for it in E['order'])
    o.append(f'<div class="ex-order"><b class="cond-h">{esc(E["order_h"])}</b><ol>{items}</ol></div>')
    lis = ''.join(f'<li>{rich(x)}</li>' for x in E['text'])
    o.append(f'<div class="ex-text"><b class="cond-h">{esc(E["text_h"])}</b><ul class="tight">{lis}</ul></div>')
    o.append('<div class="ex-ba">')
    for key in ('before', 'after'):
        part = E[key]
        hint = f'<p class="note ex-hint">{rich(E["scroll_hint"])}</p>' if E.get('scroll_hint') else ''
        o.append(f'<figure class="ex-col ex-{key}"><h4>{esc(part["h"])}</h4>{hint}<div class="scroll">{ex_svg(part)}</div></figure>')
    o.append('</div>')
    o.append(f'<div class="legend ex-legend" aria-label="{esc(LB["legend"])}">')
    for g in E['legend']:
        o.append(f'<div class="lg-group"><h3>{esc(g["h"])}</h3>')
        for it in g['items']:
            o.append(f'<div class="lg-item"><svg viewBox="0 0 52 24" aria-hidden="true">{SW[it["sw"]]}</svg><span>{rich(it["text"])}</span></div>')
        o.append('</div>')
    o.append('</div>')
    for g in E['legend']:
        if g.get('note'):
            o.append(f'<p class="note ex-note">{rich(g["note"])}</p>')
    lis = ''.join(f'<li>{rich(x)}</li>' for x in E['checks'])
    o.append(f'<div class="ex-checks"><b class="cond-h">{esc(E["checks_h"])}</b><ul class="tight">{lis}</ul></div>')
    src = '\n'.join(['## Explainer', ''] + ['- ' + x for x in E['text']]
                     + ['', '### Before', '```mermaid', E['mermaid_before'].rstrip('\n'), '```', '', '### After', '```mermaid', E['mermaid_after'].rstrip('\n'), '```', '', E['pr_legend_line']])
    o.append(f'<details class="ex-src"><summary>{esc(E["source_h"])}</summary><pre><code>{esc(src)}</code></pre></details>')
    o.append('</div>')
    return ''.join(o)


def panels():
    P = V['panels']
    e = P['enforcement']
    o = ['<div class="panels">']
    ir = P.get('init_rules')
    if ir:
        o.append(f'<div class="sheet panel" data-id="panel-init-rules"><h3>{esc(ir["h"])} <span class="tag {ir["tag"]}">{esc(LB["prov"][ir["tag"]])}</span></h3><p class="note">{rich(ir["note"])}</p><div class="stop-grid">')
        for it in ir['items']:
            o.append(f'<div class="stop" data-id="init-{esc(it["k"].split(" ")[0])}"><b>{esc(it["k"])}</b><span>{rich(it["v"])}</span></div>')
        o.append('</div></div>')
    o.append(f'<div class="sheet panel" data-id="panel-enforcement"><h3>{esc(e["h"])}</h3><div class="band">')
    for b in e['bands']:
        lis = ''.join(f'<li>{rich(i)}</li>' for i in b['items'])
        o.append(f'<div class="band-row"><div class="band-k">{esc(b["k"])}<small>{esc(b["small"])}</small></div><div><ul class="tight">{lis}</ul></div></div>')
    o.append('</div></div>')
    s = P['stop']
    o.append(f'<div class="sheet panel" data-id="panel-stop"><h3>{esc(s["h"])} <span class="tag {s["tag"]}">{esc(LB["prov"][s["tag"]])}</span></h3><p class="note">{rich(s["note"])}</p><div class="stop-grid">')
    for it in s['items']:
        o.append(f'<div class="stop" data-id="stop-{it["k"]}"><b>{esc(it["k"])}</b><span>{rich(it["v"])}</span></div>')
    o.append('</div></div>')
    c = P['context']
    lis = ''.join(f'<li>{rich(i)}</li>' for i in c['items'])
    o.append(f'<div class="sheet panel" data-id="panel-context"><h3>{esc(c["h"])}</h3><ul class="tight">{lis}</ul></div>')
    o.append('</div>')
    return ''.join(o)


# ---------------------------------------------------------------- review pipeline (RV1-RV8)
def pbox(item, rect, chips_text):
    x, y, w, h = rect
    iid = item['id']
    kind = item.get('kind', 'det')
    out = [f'<g class="node" data-id="{esc(iid)}">']
    if kind == 'actor':
        out.append(f'<rect class="actor" x="{x}" y="{y}" width="{w}" height="{h}" rx="{min(h / 2, 26):.0f}"/>')
    else:
        out.append(f'<rect class="pv pv-{kind}" x="{x}" y="{y}" width="{w}" height="{h}" rx="8"/>')
    title = plain(item['title'])
    mono = bool(item.get('mono'))
    ty = y + 29
    if est(title, 16, mono) > w - 26:
        warnings.append(f'PIPE title overflow {iid}')
    dim = ' nd-dim' if kind == 'off' else ''
    out.append(f'<text class="t-title{" mono" if mono else ""}{dim}" x="{x + 14}" y="{ty}">{esc(title)}</text>')
    ly = ty + 22
    for raw in item.get('lines', []):
        text, st = norm_line(raw)
        if text:
            if est(text, 13.5, 'mono' in st) > w - 26:
                warnings.append(f'PIPE line overflow {iid}: "{text}"')
            out.append(f'<text class="t-line {st}" x="{x + 14}" y="{ly}">{esc(text)}</text>')
        ly += 19 if text else 10
    if ly - 19 + 8 > y + h:
        warnings.append(f'PIPE height overflow {iid}: need {ly - 19 + 8 - y} have {h}')
    if kind in chips_text:
        out.append(chips_svg(x + w - 12, y - 11, [(chips_text[kind], 'c-p' + kind)]))
    out.append('</g>')
    return ''.join(out)


def pzone(z, rect):
    x, y, w, h = rect
    tag = z['tag']
    out = [f'<g class="zone-g" data-id="{esc(z["id"])}"><rect class="zone" x="{x}" y="{y}" width="{w}" height="{h}" rx="16"/>']
    tw = est(tag, 13, True) + 14
    out.append(f'<rect class="ztag" x="{x + 18}" y="{y + 16}" width="{tw:.0f}" height="24" rx="5"/>')
    out.append(f'<text class="ztag-t" x="{x + 18 + tw / 2:.0f}" y="{y + 33}" text-anchor="middle">{esc(tag)}</text>')
    out.append(f'<text class="z-name" x="{x + 18 + tw + 10:.0f}" y="{y + 34}">{esc(z["name"])}</text>')
    sub = plain(z.get('sub', ''))
    if sub:
        if est(sub, 13.5) > w - 40:
            warnings.append(f'PIPE zone sub overflow {z["id"]}')
        out.append(f'<text class="z-sub" x="{x + 20}" y="{y + 60}">{esc(sub)}</text>')
    if est(z['name'], 18) + tw + 28 > w:
        warnings.append(f'PIPE zone name overflow {z["id"]}')
    out.append('</g>')
    return ''.join(out)


def pipeline_svg():
    P = V['pipeline']
    LP = L['pipeline']
    S = [defs()]
    zmap = {z['id']: z for z in P['zones']}
    for zid, rect in LP['zones'].items():
        S.append(pzone(zmap[zid], rect))
    chips_text = P['stage_kind_chip']
    for n in P['nodes']:
        S.append(pbox(n, LP['items'][n['id']], chips_text))
    for a in P['arrows']:
        S.append(arrow_svg(a, LP['arrows'][a['id']]))
    w, h = LP['viewBox']
    return (f'<svg class="dia dia-pipe" viewBox="0 0 {w} {h}" role="img" aria-label="{esc(P["aria"])}">'
            + ''.join(S) + '</svg>')


def pipeline_legend():
    o = [f'<div class="legend" aria-label="{esc(LB["legend"])}">']
    for g in V['pipeline']['legend']:
        o.append(f'<div class="lg-group"><h3>{esc(g["h"])}</h3>')
        for it in g['items']:
            o.append(f'<div class="lg-item"><svg viewBox="0 0 52 24" aria-hidden="true">{SW[it["sw"]]}</svg><span>{rich(it["text"])}</span></div>')
        if g.get('note'):
            o.append(f'<p class="note">{rich(g["note"])}</p>')
        o.append('</div>')
    o.append('</div>')
    return ''.join(o)


def pipeline_section():
    P = V['pipeline']
    o = ['<section id="pipeline">', sec_head('pipeline')]
    o.append(f'<figure class="sheet"><div class="scroll">{pipeline_svg()}</div>{pipeline_legend()}'
             f'<figcaption>{rich(P["caption"])}</figcaption></figure>')
    th = ''.join(f'<th>{esc(c)}</th>' for c in P['table_cols'])
    rows = []
    for s in P['stages']:
        key = s.get('key') or (P['key_prefix'] + s['id'] + P['key_suffix'])
        rows.append(f'<tr id="{s["id"].lower()}" data-id="{s["id"]}"><td>{s["id"]}</td><td class="nm">{esc(s["name"])}</td><td>{rich(s["does"])}</td>'
                    f'<td>{esc(s["local"])}</td><td>{esc(s["ci"])}</td><td>{esc(s["by"])}</td><td><code>{esc(key)}</code></td></tr>')
    o.append(f'<div class="sheet tblwrap"><table class="data pipe-t"><thead><tr>{th}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>')
    o.append(f'<h3 class="sub-h">{esc(P["mapping_h"])}</h3><dl class="sheet lc-list">')
    for it in P['mapping']:
        o.append(f'<div class="lc" data-id="{it["id"]}"><dt>{esc(it["k"])}</dt><dd>{rich(it["v"])}</dd></div>')
    o.append('</dl></section>')
    return ''.join(o)


# ---------------------------------------------------------------- spec-stage artifact chain (CHAIN)
def chain_svg():
    C = V['chain']
    LC_ = L['chain']
    S = [defs(extra=['skip'])]
    zmap = {z['id']: z for z in C['zones']}
    for zid, rect in LC_['zones'].items():
        S.append(pzone(zmap[zid], rect))
    for n in C['nodes']:
        S.append(pbox(n, LC_['items'][n['id']], C['kind_chip']))
    for a in C['arrows']:
        S.append(arrow_svg(a, LC_['arrows'][a['id']]))
        geo = LC_['arrows'][a['id']]
        lx, _ = geo.get('label_at', (0, 0))
        for t_ in a.get('label', []):
            w_ = est(plain(t_), 13)
            right = lx + (w_ if geo.get('anchor') == 'start' else w_ / 2)
            if right > LC_['viewBox'][0]:
                warnings.append(f'CHAIN label overflow {a["id"]}')
    w, h = LC_['viewBox']
    return (f'<svg class="dia dia-chain" viewBox="0 0 {w} {h}" role="img" aria-label="{esc(C["aria"])}">'
            + ''.join(S) + '</svg>')


def chain_section():
    C = V['chain']
    o = [f'<section id="{C["id"]}" data-id="{C["id"]}">', sec_head('chain')]
    hint = f'<p class="note ch-hint">{rich(C["scroll_hint"])}</p>' if C.get('scroll_hint') else ''
    o.append(f'<figure class="sheet ch-fig">{hint}<div class="scroll">{chain_svg()}</div><div class="legend" aria-label="{esc(LB["legend"])}">')
    for g in C['legend']:
        o.append(f'<div class="lg-group"><h3>{esc(g["h"])}</h3>')
        for it in g['items']:
            o.append(f'<div class="lg-item"><svg viewBox="0 0 52 24" aria-hidden="true">{SW[it["sw"]]}</svg><span>{rich(it["text"])}</span></div>')
        if g.get('note'):
            o.append(f'<p class="note">{rich(g["note"])}</p>')
        o.append('</div>')
    o.append(f'</div><figcaption>{rich(C["caption"])} <span class="tag {C["tag"]}">{esc(C["tag_label"])}</span></figcaption></figure>')
    th = ''.join(f'<th>{esc(c)}</th>' for c in C['gates_cols'])
    rows = []
    for g in C['gates']:
        rows.append(f'<tr data-id="chain-{g["stage"]}"><td class="nm">{esc(g["k"])}</td><td>{rich(g["artifact"])}</td><td>{rich(g["start"])}</td><td>{rich(g["writer"])}</td>'
                    f'<td>{rich(g["approval"])}</td><td>{rich(g["ci"])}</td><td>{rich(g["src"])}</td></tr>')
    o.append(f'<h3 class="sub-h">{esc(C["gates_h"])}</h3><div class="sheet tblwrap"><table class="data chain-t"><thead><tr>{th}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>')
    lis = ''.join(f'<li>{rich(x)}</li>' for x in C['notes'])
    o.append(f'<div class="sheet panel ch-notes"><h3>{esc(C["notes_h"])}</h3><ul class="tight">{lis}</ul></div>')
    o.append('</section>')
    return ''.join(o)


def apply_section():
    return ('<section id="apply">' + sec_head('apply') +
            f'<div class="split"><figure class="sheet"><div class="scroll">{apply_map()}</div>'
            f'<figcaption>{rich(V["apply"]["caption"])}</figcaption></figure>{panels()}</div></section>')


def contracts_section():
    cols = V['contracts_table']['cols']
    th = ''.join(f'<th>{esc(c)}</th>' for c in cols)
    rows = []
    for c in Y['contracts']:
        rows.append(f'<tr data-id="contract-{c["id"]}" id="contract-{c["id"]}"><td>{esc(c["id"])}</td><td><code>{esc(c["file"])}</code></td>'
                    f'<td><a href="{esc(c["schema"])}">{esc(c["schema"].split("/")[-1])}</a></td><td>{esc(c["writer"])}</td><td>{esc(c["reader"])}</td></tr>')
    return ('<section id="contracts">' + sec_head('contracts') +
            f'<div class="sheet tblwrap"><table class="data"><thead><tr>{th}</tr></thead><tbody>{"".join(rows)}</tbody></table></div></section>')


def unverified_section():
    cols = V['unverified_table']['cols']
    th = ''.join(f'<th>{esc(c)}</th>' for c in cols)
    rows = []
    for u in Y['unverified']:
        rows.append(f'<tr data-id="{u["id"]}" id="{u["id"].lower()}"><td>{u["id"]}</td><td>{rich(u["claim"])}</td><td>{rich(u["why"])}</td><td>{rich(u["how"])}</td></tr>')
    return ('<section id="unverified">' + sec_head('unverified') +
            f'<div class="sheet tblwrap"><table class="data"><thead><tr>{th}</tr></thead><tbody>{"".join(rows)}</tbody></table></div></section>')


def sources_section():
    lis = ''.join(f'<li id="src-{SRC_N[s["id"]]}" data-id="{s["id"]}"><span class="n">[{SRC_N[s["id"]]}]</span><span><a href="{esc(s["url"])}">{esc(s["url"])}</a> <span class="d">{esc(s["title"])}</span></span></li>'
                  for s in Y['sources'])
    return f'<section id="sources">{sec_head("sources")}<div class="sheet panel"><ol class="src">{lis}</ol></div></section>'


SCRIPT = r'''(function(){
  function snippetFor(rec, k){
    var lines = ['# ' + rec.file], ind = '';
    rec.path.forEach(function(p){ lines.push(ind + p + ':'); ind += '  '; });
    lines.push(ind + 'value: ' + k, ind + 'decided_by: user', ind + 'decided_at: <ISO 8601>');
    return lines.join('\n');
  }
  document.querySelectorAll('.opts[data-group]').forEach(function(group){
    var card = group.closest('.item');
    var uid = group.getAttribute('data-group');
    var btns = group.querySelectorAll('.opt');
    var panels = card.querySelectorAll('.opt-panel');
    var snip = document.getElementById('snip-' + uid);
    btns.forEach(function(b){
      b.addEventListener('click', function(){
        var k = b.getAttribute('data-k');
        btns.forEach(function(x){ x.setAttribute('aria-pressed', x === b ? 'true' : 'false'); });
        panels.forEach(function(p){ p.hidden = p.getAttribute('data-panel') !== k; });
        if (snip && !snip.hasAttribute('data-fixed')) { try { snip.textContent = snippetFor(JSON.parse(snip.getAttribute('data-tpl')), k); } catch (e) {} }
      });
    });
  });
  document.querySelectorAll('.copy[data-copy]').forEach(function(btn){
    btn.addEventListener('click', function(){
      var code = document.getElementById('snip-' + btn.getAttribute('data-copy'));
      if (!code) return;
      function selectIt(){
        var r = document.createRange(); r.selectNodeContents(code);
        var s = window.getSelection(); s.removeAllRanges(); s.addRange(r);
        btn.textContent = btn.getAttribute('data-fail');
      }
      try {
        navigator.clipboard.writeText(code.textContent).then(function(){
          btn.textContent = btn.getAttribute('data-done');
          setTimeout(function(){ btn.textContent = btn.getAttribute('data-label'); }, 1600);
        }, selectIt);
      } catch (e) { selectIt(); }
    });
  });
})();'''


def build():
    body = ''.join([header(), map_section(), roles_section(), chain_section(), loop_section(), pipeline_section(), apply_section(),
                    ambiguity(), contracts_section(), unverified_section(), sources_section()])
    model = json.dumps(Y, ensure_ascii=False, separators=(',', ':')).replace('</', '<\\/')
    tpl = open(sys.argv[2], encoding='utf-8').read()
    out = (tpl.replace('<!--TITLE-->', esc(Y['meta']['title']))
              .replace('<!--BODY-->', body)
              .replace('<!--MODEL-->', model)
              .replace('<!--SCRIPT-->', SCRIPT))
    open(sys.argv[3], 'w', encoding='utf-8').write(out)
    for w in warnings:
        print(w, file=sys.stderr)
    print('ok', len(out), 'warnings', len(warnings))


build()
