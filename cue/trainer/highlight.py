"""Syntax highlighting for the trainer windows: Pygments tokens -> Qt rich text, one HTML string
per source line (so lines can get their own background: red / yellow / green marks)."""
from __future__ import annotations

import html

from pygments.lexers import get_lexer_by_name
from pygments.token import Token
from pygments.util import ClassNotFound

# a palette that reads on dark frosted glass
PALETTE = [
    (Token.Comment, "#7f8ea3", "italic"),
    (Token.Keyword, "#c792ea", ""),
    (Token.Name.Builtin, "#82aaff", ""),
    (Token.Name.Function, "#82aaff", ""),
    (Token.Name.Class, "#ffcb6b", ""),
    (Token.Name.Decorator, "#ffcb6b", ""),
    (Token.Literal.String, "#c3e88d", ""),
    (Token.Literal.Number, "#f78c6c", ""),
    (Token.Operator, "#89ddff", ""),
    (Token.Name.Tag, "#f07178", ""),
]
_cache: dict = {}


def _style(ttype) -> tuple[str, str]:
    if ttype in _cache:
        return _cache[ttype]
    found = ("", "")
    t = ttype
    while t is not None and found == ("", ""):
        for tok, color, extra in PALETTE:
            if t == tok:
                found = (color, extra)
                break
        t = t.parent
    _cache[ttype] = found
    return found


def lexer_for(lexer_name: str):
    try:
        return get_lexer_by_name(lexer_name, stripnl=False, ensurenl=False)
    except ClassNotFound:
        return get_lexer_by_name("text", stripnl=False, ensurenl=False)


def lines_html(code: str, lexer_name: str) -> list[str]:
    """Highlighted HTML for each line of `code` (spaces kept as &nbsp;)."""
    out, cur = [], []
    for ttype, value in lexer_for(lexer_name).get_tokens(code):
        parts = value.split("\n")
        for i, part in enumerate(parts):
            if i:
                out.append("".join(cur))
                cur = []
            if not part:
                continue
            esc = html.escape(part).replace(" ", "&nbsp;").replace("\t", "&nbsp;" * 4)
            color, extra = _style(ttype)
            if color:
                it = "font-style:italic;" if extra == "italic" else ""
                cur.append(f"<span style='color:{color};{it}'>{esc}</span>")
            else:
                cur.append(esc)
    if cur:
        out.append("".join(cur))
    n = len(code.split("\n"))
    while len(out) < n:
        out.append("")
    return out[:n]


def block_html(code: str, lexer_name: str, fs: int) -> str:
    """A highlighted code block (no line numbers)."""
    rows = "<br>".join(l or "&nbsp;" for l in lines_html(code.rstrip("\n"), lexer_name))
    return (f"<div style=\"font-family:'Cascadia Mono','Consolas',monospace; font-size:{fs}px;"
            f" color:#e6edf3\">{rows}</div>")
