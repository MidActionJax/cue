"""HTML for the trainer windows (Qt rich text), built from the ProblemState. Pure functions:
the windows just show what these return."""
from __future__ import annotations

import html
import json
import math
import time

from . import highlight
from .state import ProblemState

TEXT = "#F4F7FB"
MUTED = "rgba(210,220,235,150)"
FAINT = "rgba(210,220,235,90)"
ACCENT = "#8fb4ff"
GREEN, AMBER, RED = "#4cd97b", "#febc2e", "#ff6b6b"
MONO = "'Cascadia Mono','Consolas',monospace"
BG = {"red": "#4a1f26", "yellow": "#463c18", "green": "#173a26"}
FG = {"red": RED, "yellow": AMBER, "green": GREEN}
FLASH_S = 3.0


def esc(s) -> str:
    return html.escape(str(s))


def _label(text: str, fs: int, top: int = 8, color: str = ACCENT) -> str:
    return (f"<div style='color:{color}; font-size:{int(fs * 0.72)}px; font-weight:600; letter-spacing:0.5px;"
            f" margin:{top}px 0 2px 0'>{esc(text.upper())}</div>")


def _short(v, n: int = 70) -> str:
    s = v if isinstance(v, str) else json.dumps(v)
    return s if len(s) <= n else s[: n - 1] + "…"


def clock(seconds: float) -> str:
    neg = seconds < 0
    s = int(abs(seconds))
    return f"{'+' if neg else ''}{s // 60}:{s % 60:02d}"


# ---------------------------------------------------------------------------- spec board (main panel)

def pacing(state: ProblemState, left: float | None) -> tuple[str, str]:
    """(nudge, color) for the interview's rhythm, as a share of the estimate."""
    e = state.estimate
    if left is None or not e.minutes:
        if state.problem.defined:
            return "Problem's clear: say the brute force out loud, then the better idea.", MUTED
        return "Ask the questions in Ask next before you start designing.", MUTED
    total = e.minutes * 60 + e.extra_s
    f = 1 - left / total if total else 1
    phase = state.phase
    if f >= 1:
        return "Over time: say what's left and how you'd finish it.", RED
    if f > 0.8:
        return "Walk a test case through your code out loud, then fix what it finds.", AMBER
    if f > 0.35 and phase in ("listening", "clarifying", "defined", "designing"):
        return "Time to start coding: say your plan in one sentence and go.", AMBER
    if f > 0.3:
        return "Code it block by block, saying what each part does.", MUTED
    if f > 0.15:
        return "Say the brute force and its cost, then the better idea and its complexity.", MUTED
    return "Confirm inputs, edge cases and the output format before designing.", MUTED


def spec_header(state: ProblemState, now: float | None = None) -> str:
    left = state.seconds_left(now)
    p, e = state.problem, state.estimate
    bits = []
    if left is not None:
        bits.append(f"⏱ {clock(left)} {'left' if left >= 0 else 'over'}" + (" (paused)" if e.paused_left >= 0 else ""))
    elif e.minutes:
        bits.append(f"⏱ {e.minutes} min (not started)")
    if p.difficulty:
        bits.append(p.difficulty.title())
    if p.pattern:
        bits.append(", ".join(p.pattern))
    bits.append(state.phase.replace("_", " "))
    return "TRAINER · " + " · ".join(bits)


def spec_html(state: ProblemState, fs: int, now: float | None = None) -> str:
    now = time.time() if now is None else now
    p, out = state.problem, []
    left = state.seconds_left(now)
    if left is not None:
        total = state.estimate.minutes * 60 + state.estimate.extra_s
        color = RED if left < 0 else AMBER if total and left < total * 0.25 else ACCENT
        out.append(f"<div style='font-size:{int(fs * 1.6)}px; font-weight:700; color:{color}'>{clock(left)}"
                   f"<span style='font-size:{int(fs * 0.75)}px; font-weight:400; color:{MUTED}'>"
                   f"&nbsp; {'left' if left >= 0 else 'over'} of ~{state.estimate.minutes + int(state.estimate.extra_s // 60)} min"
                   f"{' · paused' if state.estimate.paused_left >= 0 else ''}</span></div>")
    if p.title or p.summary:
        out.append(f"<div style='font-size:{int(fs * 1.05)}px; font-weight:600'>{esc(p.title or 'Problem')}</div>")
        if p.summary:
            out.append(f"<div style='color:{MUTED}; font-size:{int(fs * 0.85)}px'>{esc(p.summary)}</div>")
    elif not state.requirements:
        out.append(f"<div style='color:{MUTED}'>Listening for the problem… (or 📋 Paste problem)</div>")
    if p.signature:
        s = p.signature
        sig = f"{s['name']}(" + ", ".join(f"{n}: {t}" for n, t in s["params"]) + f") → {s.get('returns', '')}"
        out.append(f"<div style='font-family:{MONO}; font-size:{int(fs * 0.78)}px; color:#c3e88d; margin-top:3px'>"
                   f"{esc(sig)}</div>")
    groups = [("Task & output", ("functional", "io")), ("Constraints", ("constraint", "performance")), ("Edge cases", ("edge",))]
    for title, kinds in groups:
        reqs = [r for r in state.requirements if r.kind in kinds and
                (r.status != "dropped" or now - r.changed_at < 10)]
        if not reqs:
            continue
        out.append(_label(title, fs))
        for r in reqs:
            out.append(_req_line(r, fs, now))
    if p.examples:
        out.append(_label("Examples", fs))
        for ex in p.examples[:4]:
            out.append(f"<div style='font-family:{MONO}; font-size:{int(fs * 0.78)}px; color:{MUTED}'>"
                       f"{esc(_short(ex['input'], 60))} → {esc(_short(ex.get('output'), 30))}</div>")
    nudge, color = pacing(state, left)
    out.append(f"<div style='color:{color}; font-size:{int(fs * 0.82)}px; margin-top:8px'>➜ {esc(nudge)}</div>")
    return "".join(out)


def _req_line(r, fs: int, now: float) -> str:
    fresh = now - r.changed_at < FLASH_S
    if r.status == "dropped":
        return (f"<div style='color:{FAINT}; margin:1px 0 1px 4px'><s>{esc(r.text)}</s>"
                f" <span style='font-size:{int(fs * 0.7)}px'>(dropped)</span></div>")
    mark = {"yes": f"<span style='color:{GREEN}; font-weight:700'>✓</span>",
            "partial": f"<span style='color:{AMBER}; font-weight:700'>◐</span>"}.get(r.met, f"<span style='color:{FAINT}'>☐</span>")
    text = esc(r.text)
    if r.status == "assumed":
        text = f"<i>{text}</i> <span style='color:{AMBER}; font-size:{int(fs * 0.7)}px'>assumed · ask?</span>"
    if r.history and fresh:
        text += f" <span style='color:{AMBER}; font-size:{int(fs * 0.7)}px'>edited</span>"
    color = AMBER if fresh else TEXT
    tip = esc(r.source)
    return (f"<div style='margin:1px 0 1px 4px; color:{color}' title='{tip}'>{mark}&nbsp; {text}</div>")


# ---------------------------------------------------------------------------- Ask next

def questions_html(state: ProblemState, fs: int, now: float | None = None) -> str:
    now = time.time() if now is None else now
    open_ = sorted(state.open_questions(), key=lambda q: (q.priority, q.id))
    done = [q for q in state.questions if q.status in ("asked", "answered")]
    gone = [q for q in state.questions if q.status == "dropped" and now - q.changed_at < 6]
    out = []
    if not state.questions:
        return f"<div style='color:{MUTED}'>Questions to ask show up once the problem starts.</div>"
    for q in open_[:6]:
        star = f"<span style='color:{AMBER}'>★</span> " if q.priority == 1 else ""
        color = AMBER if now - q.changed_at < FLASH_S else TEXT
        why = f"<div style='color:{FAINT}; font-size:{int(fs * 0.72)}px; margin-left:22px'>{esc(q.why)}</div>" if q.why else ""
        out.append(f"<div style='margin:4px 0 0 0; color:{color}'><span style='color:{FAINT}'>☐</span>&nbsp; {star}{esc(q.text)}</div>{why}")
    if len(open_) > 6:
        out.append(f"<div style='color:{FAINT}; font-size:{int(fs * 0.75)}px'>+{len(open_) - 6} more</div>")
    if done:
        out.append(_label("Asked", fs, top=10, color=MUTED))
        for q in reversed(done):
            icon = f"<span style='color:{GREEN}'>✓</span>" if q.status == "answered" else f"<span style='color:{AMBER}'>…</span>"
            ans = f"<div style='color:{GREEN}; font-size:{int(fs * 0.78)}px; margin-left:22px'>→ {esc(q.answer)}</div>" if q.answer else \
                f"<div style='color:{FAINT}; font-size:{int(fs * 0.72)}px; margin-left:22px'>waiting for the answer</div>"
            out.append(f"<div style='margin:3px 0 0 0; color:{MUTED}'>{icon}&nbsp; {esc(q.text)}</div>{ans}")
    for q in gone:
        out.append(f"<div style='color:{FAINT}'><s>{esc(q.text)}</s></div>")
    return "".join(out)


# ---------------------------------------------------------------------------- How to solve it

def walkthrough_html(state: ProblemState, fs: int) -> str:
    """The recommended approach as verbal pseudocode: what you'd do, in the words you'd say it."""
    w = state.walkthrough
    if not w.get("steps"):
        if state.problem.defined:
            return f"<div style='color:{MUTED}'>Working out the plan…</div>"
        return f"<div style='color:{MUTED}'>Once the problem is clear, the way to solve it shows up here in plain words.</div>"
    rec = state.chosen_approach()
    out = []
    if rec:
        out.append(f"<div style='color:{MUTED}; font-size:{int(fs * 0.75)}px'>{esc(rec.name)} · "
                   f"<span style='font-family:{MONO}; color:#c3e88d'>{esc(rec.time)}</span></div>")
    for i, step in enumerate(w["steps"], 1):
        out.append(f"<table cellspacing='0' cellpadding='0' style='margin-top:5px'><tr>"
                   f"<td valign='top' style='color:{ACCENT}; font-weight:700; padding-right:8px'>{i}</td>"
                   f"<td style='font-size:{int(fs * 1.02)}px'>{esc(step)}</td></tr></table>")
    if w.get("example"):
        out.append(_label("Walk it through an example", fs, top=10))
        for line in w["example"]:
            out.append(f"<div style='color:{TEXT}; font-size:{int(fs * 0.88)}px; margin:2px 0 0 4px'>{esc(line)}</div>")
    if w.get("edge"):
        out.append(_label("Edge cases", fs, top=10))
        for line in w["edge"]:
            out.append(f"<div style='color:{AMBER}; font-size:{int(fs * 0.88)}px; margin:2px 0 0 4px'>• {esc(line)}</div>")
    if w.get("why"):
        out.append(f"<div style='color:{GREEN}; font-size:{int(fs * 0.85)}px; margin-top:10px'>Why it works: {esc(w['why'])}</div>")
    return "".join(out)


# ---------------------------------------------------------------------------- Design tracks

def _ops_bar(ops: float, biggest: float, fs: int) -> str:
    if ops <= 0 or biggest <= 0:
        return ""
    width = max(1, round(24 * math.log10(max(ops, 1)) / max(math.log10(max(biggest, 10)), 1)))
    exp = int(math.floor(math.log10(ops))) if ops >= 1 else 0
    mant = ops / 10 ** exp if exp else ops
    sup = str(exp).translate(str.maketrans("0123456789-", "⁰¹²³⁴⁵⁶⁷⁸⁹⁻"))
    label = f"~{mant:.0f}·10{sup} ops" if exp >= 3 else f"~{ops:.0f} ops"
    return (f"<span style='color:{ACCENT}; font-family:{MONO}; font-size:{int(fs * 0.7)}px'>{'█' * width}</span>"
            f"<span style='color:{MUTED}; font-size:{int(fs * 0.72)}px'> {label}</span>")


def design_html(state: ProblemState, fs: int, expanded: set[str] | None = None) -> str:
    expanded = expanded or set()
    out = []
    if not state.approaches:
        if state.problem.defined:
            return f"<div style='color:{MUTED}'>Designing approaches…</div>"
        return f"<div style='color:{MUTED}'>Approaches show up once the problem is clear.</div>"
    if state.plan:
        out.append(_label("Plan", fs, top=0))
        arrow = f" <span style='color:{FAINT}'>→</span> "
        steps = arrow.join(f"<span style='color:{TEXT}; font-weight:600'>{esc(s['step'])}</span>" for s in state.plan)
        out.append(f"<div style='font-size:{int(fs * 0.85)}px'>{steps}</div>")
        for i, s in enumerate(state.plan, 1):
            line = f"{i}. {esc(s['detail'])}" if s.get("detail") else ""
            if s.get("branch"):
                line += f" <span style='color:{AMBER}'>↳ {esc(s['branch'])}</span>"
            if line:
                out.append(f"<div style='color:{MUTED}; font-size:{int(fs * 0.75)}px; margin-left:6px'>{line}</div>")
    detected = state.code.get("detected_approach")
    hint_to = " ".join(h.get("points_to", "") + " " + h["text"] for h in state.hints).lower()
    biggest = max((a.ops for a in state.approaches), default=0)
    out.append(_label("Approaches", fs))
    for a in state.approaches:
        badges = []
        if a.recommended:
            badges.append(f"<span style='color:{GREEN}'>★ recommended</span>")
        if a.chosen:
            badges.append(f"<span style='color:{ACCENT}'>📌 pinned</span>")
        if detected == a.id:
            badges.append(f"<span style='color:{ACCENT}'>▶ you're on this one</span>")
        if hint_to and any(w in hint_to for w in a.name.lower().replace(":", " ").split() if len(w) > 4):
            badges.append(f"<span style='color:{AMBER}'>💡 the hint points here</span>")
        pin = f"<a href='pin:{a.id}' style='color:{FAINT}; text-decoration:none'>{'unpin' if a.chosen else 'pin'}</a>"
        out.append(f"<div style='margin-top:9px'><span style='font-weight:700; font-size:{int(fs * 0.95)}px'>{esc(a.name)}</span>"
                   f" &nbsp;<span style='font-family:{MONO}; color:#c3e88d; font-size:{int(fs * 0.78)}px'>"
                   f"{esc(a.time)} time · {esc(a.space)} space</span> &nbsp;{pin}</div>")
        if badges:
            out.append(f"<div style='font-size:{int(fs * 0.72)}px'>{' · '.join(badges)}</div>")
        out.append(f"<div style='font-size:{int(fs * 0.85)}px'>{esc(a.idea)}</div>")
        bar = _ops_bar(a.ops, biggest, fs)
        if bar:
            out.append(f"<div>{bar}</div>")
        if a.why_not:
            out.append(f"<div style='color:{AMBER}; font-size:{int(fs * 0.8)}px'>Why it loses: {esc(a.why_not)}</div>")
        if a.tradeoffs:
            out.append(f"<div style='color:{MUTED}; font-size:{int(fs * 0.78)}px'>Trade-off: {esc(a.tradeoffs)}</div>")
        if a.say_it:
            out.append(f"<div style='color:{MUTED}; font-size:{int(fs * 0.8)}px'><i>🗣 “{esc(a.say_it)}”</i></div>")
        show = a.id in expanded or (a.recommended and f"-{a.id}" not in expanded)
        if a.pseudocode and show:
            code = "<br>".join(esc(l).replace(" ", "&nbsp;") for l in a.pseudocode.strip().splitlines())
            out.append(f"<div style='font-family:{MONO}; font-size:{int(fs * 0.75)}px; color:#d6deeb; margin:3px 0 0 8px'>{code}</div>")
        if a.pseudocode:
            out.append(f"<div style='font-size:{int(fs * 0.7)}px'><a href='toggle:{a.id}' style='color:{FAINT}'>"
                       f"{'hide' if show else 'show'} pseudocode</a></div>")
    m = state.measured
    if m.get("label") and m.get("code_hash") == _code_hash(state.code.get("text", "")):
        claimed = state.chosen_approach()
        out.append(_label("Measured on your code", fs))
        pts = ", ".join(f"n={n:,}: {ms:.0f} ms" for n, ms in m.get("points", []) if ms is not None)
        out.append(f"<div><span style='font-weight:700; color:{GREEN if 'n²' not in m['label'] and 'slow' not in m['label'] else AMBER}'>"
                   f"{esc(m['label'])}</span>" + (f" <span style='color:{MUTED}'>(you're aiming for {esc(claimed.time)})</span>" if claimed else "")
                   + f"</div><div style='color:{FAINT}; font-size:{int(fs * 0.72)}px'>{esc(pts)}</div>")
    return "".join(out)


# ---------------------------------------------------------------------------- Code window

def blocks_html(state: ProblemState, fs: int, lexer: str, show_code: bool) -> str:
    if not state.blocks:
        return f"<div style='color:{MUTED}'>The solution's building blocks show up once the design is ready.</div>"
    out = []
    icons = {"todo": (FAINT, "☐"), "partial": (AMBER, "◐"), "done": (GREEN, "☑"), "issue": (RED, "✗")}
    for b in state.blocks:
        color, icon = icons.get(b.status, icons["todo"])
        where = f" <span style='color:{FAINT}; font-size:{int(fs * 0.7)}px'>lines {b.lines[0]}–{b.lines[1]}</span>" if b.lines else ""
        out.append(f"<div style='margin-top:8px'><span style='color:{color}; font-weight:700'>{icon}</span>&nbsp; "
                   f"<span style='font-weight:700'>{esc(b.name)}</span>{where}</div>")
        if b.purpose:
            out.append(f"<div style='color:{MUTED}; font-size:{int(fs * 0.78)}px; margin-left:20px'>{esc(b.purpose)}</div>")
        body = b.code if show_code else b.pseudo
        if body.strip():
            if show_code:
                out.append(f"<div style='margin:2px 0 0 20px'>{highlight.block_html(body, lexer, int(fs * 0.8))}</div>")
            else:
                text = "<br>".join(esc(l).replace(" ", "&nbsp;") for l in body.strip("\n").splitlines())
                out.append(f"<div style='font-family:{MONO}; font-size:{int(fs * 0.8)}px; color:#d6deeb; margin:2px 0 0 20px'>{text}</div>")
    return "".join(out)


def mycode_html(state: ProblemState, fs: int, lexer: str) -> str:
    code = state.code.get("text", "")
    if not code.strip():
        path = state.code.get("path") or "your file"
        return f"<div style='color:{MUTED}'>Waiting for code in {esc(path)}…</div>"
    out = []
    live = state.live_requirements()
    if live:
        chips = []
        for r in live:
            c = {"yes": GREEN, "partial": AMBER}.get(r.met, FAINT)
            sym = {"yes": "✓", "partial": "◐"}.get(r.met, "☐")
            chips.append(f"<span style='color:{c}'>{sym} {esc(_short(r.text, 34))}</span>")
        out.append(f"<div style='font-size:{int(fs * 0.72)}px'>{' &nbsp;·&nbsp; '.join(chips)}</div>")
    if state.code.get("summary"):
        out.append(f"<div style='color:{ACCENT}; font-size:{int(fs * 0.8)}px; margin:4px 0'>{esc(state.code['summary'])}</div>")
    marks = relocate(state.code.get("lines") or {}, code)
    last = state.runs[-1] if state.runs else None
    for e in (last or {}).get("errors", []) if last and last.get("code_hash") == _code_hash(code) else []:
        if e.get("line"):
            marks[int(e["line"])] = {"mark": "red", "why": e.get("message", ""), "evidence": "error when run"}
    for e in state.code.get("syntax") or []:
        if e.get("line"):
            marks[int(e["line"])] = {"mark": "red", "why": e.get("message", ""), "evidence": "syntax"}
    rows = []
    hl = highlight.lines_html(code, lexer)
    for i, line in enumerate(hl, 1):
        m = marks.get(i)
        bg = f" bgcolor='{BG[m['mark']]}'" if m and m.get("mark") in BG else ""
        rows.append(f"<tr><td align='right' style='color:{FAINT}; padding-right:6px'>{i}</td>"
                    f"<td{bg}>{line or '&nbsp;'}</td></tr>")
        if m and m.get("why"):
            ev = f" · {esc(m['evidence'])}" if m.get("evidence") else ""
            rows.append(f"<tr><td></td><td style='color:{FG.get(m.get('mark'), MUTED)}; font-family:Segoe UI;"
                        f" font-size:{int(fs * 0.72)}px'>↑ {esc(m['why'])}{ev}</td></tr>")
    out.append(f"<table cellspacing='0' cellpadding='0' width='100%' style=\"font-family:{MONO}; font-size:{int(fs * 0.8)}px;"
               f" color:#e6edf3\">{''.join(rows)}</table>")
    syn = state.code.get("syntax") or []
    if syn and not any(e.get("line") for e in syn):
        out.append(f"<div style='color:{RED}'>{esc(syn[0].get('message', ''))}</div>")
    return "".join(out)


def tests_html(state: ProblemState, fs: int, open_ids: set[str] | None = None) -> str:
    open_ids = open_ids or set()
    if not state.tests:
        return f"<div style='color:{MUTED}'>Tests are written and cross-checked once the problem is defined.</div>"
    out = []
    o = state.oracle
    if o:
        ok = o.get("verified")
        out.append(f"<div style='font-size:{int(fs * 0.75)}px; color:{GREEN if ok else AMBER}'>"
                   + (f"Expected outputs cross-checked: reference and brute force agree on {esc(o.get('agreement'))} inputs"
                      if ok else "⚠ Expected outputs couldn't be cross-checked: treat them with care") + "</div>")
    last = state.runs[-1] if state.runs else None
    results = {r["id"]: r for r in (last or {}).get("results", [])}
    if last:
        stale = last.get("code_hash") != _code_hash(state.code.get("text", ""))
        when = time.strftime("%H:%M:%S", time.localtime(last["at"]))
        if last.get("build_error"):
            out.append(f"<div style='color:{RED}; font-weight:700; margin-top:4px'>Doesn't build / can't run ({when})</div>"
                       f"<div style='font-family:{MONO}; font-size:{int(fs * 0.72)}px; color:#ffb4b4'>"
                       f"{esc(last['build_error'][-1500:]).replace(chr(10), '<br>')}</div>")
        else:
            color = GREEN if last["passed"] == last["total"] else RED
            out.append(f"<div style='margin-top:4px'><span style='color:{color}; font-weight:700'>{last['passed']}/{last['total']} passed</span>"
                       f" <span style='color:{FAINT}; font-size:{int(fs * 0.72)}px'>{when}{' · code changed since' if stale else ''}</span></div>")
    rows = [f"<tr style='color:{FAINT}'><td></td><td>test</td><td>input</td><td>expected</td><td>got</td><td align='right'>ms</td></tr>"]
    for t in state.tests:
        r = results.get(t.id)
        if r is None:
            icon = f"<span style='color:{FAINT}'>·</span>"
        elif r.get("ok"):
            icon = f"<span style='color:{GREEN}'>✓</span>"
        else:
            icon = f"<span style='color:{RED}'>✗</span>"
        origin = {"interviewer": "theirs", "edge": "edge", "generated": "random", "stress": "speed", "custom": "yours"}.get(t.origin, t.origin)
        warn = f" <span style='color:{AMBER}'>⚠</span>" if not t.verified and t.origin != "custom" else ""
        inp = f"n = {state.problem.n_max:,}" if t.origin == "stress" else _short(t.input, 34)
        exp = "(speed)" if t.origin == "stress" and t.expected is None else _short(t.expected, 22)
        got = "" if r is None else (r.get("error", "").splitlines()[-1][:30] if r.get("error") and "actual" not in r
                                    else _short(r.get("actual"), 22))
        rows.append(f"<tr><td>{icon}</td><td><a href='test:{t.id}' style='color:{TEXT}; text-decoration:none'>{esc(t.name[:28])}</a>"
                    f" <span style='color:{FAINT}; font-size:{int(fs * 0.68)}px'>{origin}</span>{warn}</td>"
                    f"<td style='font-family:{MONO}'>{esc(inp)}</td><td style='font-family:{MONO}'>{esc(exp)}</td>"
                    f"<td style='font-family:{MONO}; color:{RED if r is not None and not r.get('ok') else TEXT}'>{esc(got)}</td>"
                    f"<td align='right' style='color:{FAINT}'>{'' if r is None else r.get('ms', '')}</td></tr>")
        if t.id in open_ids or (r is not None and not r.get("ok") and len(open_ids) == 0 and t.origin != "stress"
                                and sum(1 for x in results.values() if not x.get("ok")) <= 3):
            detail = [f"input: {_short(t.input, 400)}" if t.origin != "stress" else "input: the big random stress input",
                      f"expected: {_short(t.expected, 300)}"]
            if r is not None:
                if "actual" in r:
                    detail.append(f"got: {_short(r.get('actual'), 300)}")
                if r.get("error"):
                    detail.append("error:\n" + r["error"][-900:])
                if r.get("printed"):
                    detail.append("your prints:\n" + r["printed"][-500:])
            if t.note:
                detail.append(f"note: {t.note}")
            body = esc("\n".join(detail)).replace("\n", "<br>").replace("  ", "&nbsp;&nbsp;")
            rows.append(f"<tr><td></td><td colspan='5' style='font-family:{MONO}; font-size:{int(fs * 0.7)}px; color:{MUTED}'>{body}</td></tr>")
    out.append(f"<table cellspacing='3' cellpadding='0' width='100%' style='font-size:{int(fs * 0.78)}px'>{''.join(rows)}</table>")
    return "".join(out)


def debug_html(state: ProblemState, fs: int, lexer: str, show_trace: bool = False) -> str:
    d = state.debug
    if not d:
        return (f"<div style='color:{MUTED}'>Press <b>Ctrl+Alt+D</b> (or ⚡ Debug) when something fails: Cue runs every test, "
                "shrinks the failing input to the smallest one that still fails, traces it, and points at the exact lines.</div>")
    out = [f"<div style='font-size:{int(fs * 0.95)}px; font-weight:600'>{esc(d.get('summary', ''))}</div>"]
    if d.get("input") is not None:
        out.append(_label("Smallest failing input", fs))
        out.append(f"<div style='font-family:{MONO}; font-size:{int(fs * 0.8)}px'>{esc(_short(d['input'], 300))}</div>"
                   f"<div style='font-family:{MONO}; font-size:{int(fs * 0.8)}px; color:{GREEN}'>expected {esc(_short(d.get('expected'), 200))}</div>"
                   f"<div style='font-family:{MONO}; font-size:{int(fs * 0.8)}px; color:{RED}'>got&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; {esc(_short(d.get('actual'), 200))}</div>")
    src = state.code.get("text", "").splitlines()
    if d.get("lines"):
        out.append(_label("Broken lines", fs))
        for m in d["lines"]:
            try:
                ln = int(m.get("line"))
            except (TypeError, ValueError):
                continue
            text = src[ln - 1] if 0 < ln <= len(src) else ""
            out.append(f"<div style='margin-top:3px'><span style='color:{RED}; font-weight:700'>line {ln}</span> "
                       f"<span style='font-family:{MONO}; font-size:{int(fs * 0.8)}px'>{esc(text.strip())}</span></div>"
                       f"<div style='font-size:{int(fs * 0.82)}px; margin-left:10px'>{esc(m.get('why', ''))}</div>")
            if m.get("evidence"):
                out.append(f"<div style='color:{FAINT}; font-size:{int(fs * 0.72)}px; margin-left:10px'>evidence: {esc(m['evidence'])}</div>")
    if d.get("fix"):
        out.append(_label("Fix", fs))
        for f in d["fix"]:
            out.append(f"<div style='font-family:{MONO}; font-size:{int(fs * 0.8)}px; color:{RED}'>- {esc(f.get('before', ''))}</div>"
                       f"<div style='font-family:{MONO}; font-size:{int(fs * 0.8)}px; color:{GREEN}'>+ {esc(f.get('after', ''))}</div>")
    if d.get("say_it"):
        out.append(f"<div style='color:{MUTED}; margin-top:6px'><i>🗣 “{esc(d['say_it'])}”</i></div>")
    if d.get("trace"):
        out.append(f"<div style='margin-top:6px; font-size:{int(fs * 0.75)}px'><a href='trace' style='color:{FAINT}'>"
                   f"{'hide' if show_trace else 'show'} the execution trace</a></div>")
        if show_trace:   # each step: the line that ran, then (indented, fainter) the variables at that moment
            rows = []
            for line in d["trace"].splitlines():
                color = FAINT if line.startswith("       ") else "#d6deeb"
                rows.append(f"<span style='color:{color}'>{esc(line).replace(' ', '&nbsp;')}</span>")
            out.append(f"<div style='font-family:{MONO}; font-size:{int(fs * 0.72)}px'>{'<br>'.join(rows)}</div>")
    return "".join(out)


def relocate(marks: dict, code: str) -> dict[int, dict]:
    """Marks were made for an older version of the code: keep each on the line whose text it was
    made for (it may have moved a few lines), and drop marks whose line is gone."""
    src = [l.strip() for l in code.splitlines()]
    out: dict[int, dict] = {}
    for k, m in marks.items():
        try:
            ln = int(k)
        except (TypeError, ValueError):
            continue
        want = m.get("text")
        if not want or (0 < ln <= len(src) and src[ln - 1] == want):
            if 0 < ln <= len(src):
                out[ln] = m
            continue
        near = [i + 1 for i, t in enumerate(src) if t == want and abs(i + 1 - ln) <= 8]
        if near:
            out[min(near, key=lambda i: abs(i - ln))] = m
    return out


def _code_hash(code: str) -> str:
    import hashlib
    return hashlib.sha1(code.encode("utf-8")).hexdigest()[:12]
