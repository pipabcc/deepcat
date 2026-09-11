from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Optional


_MATH_COLOR = "#1f2937"
_MATH_FONT = "'Cambria Math','Times New Roman','Noto Serif',serif"
_DISPLAY_STYLE = (
    f"font-family:{_MATH_FONT}; font-size:26px; color:{_MATH_COLOR};"
    " font-style:italic; line-height:180%;"
)
_INLINE_STYLE = (
    f"font-family:{_MATH_FONT}; font-size:15px; color:{_MATH_COLOR};"
    " font-style:italic;"
)
_TEXT_STYLE = "font-style:normal;"


_SYMBOLS = {
    "alpha": "α",
    "beta": "β",
    "gamma": "γ",
    "delta": "δ",
    "epsilon": "ϵ",
    "varepsilon": "ε",
    "zeta": "ζ",
    "eta": "η",
    "theta": "θ",
    "vartheta": "ϑ",
    "iota": "ι",
    "kappa": "κ",
    "lambda": "λ",
    "mu": "μ",
    "nu": "ν",
    "xi": "ξ",
    "pi": "π",
    "varpi": "ϖ",
    "rho": "ρ",
    "varrho": "ϱ",
    "sigma": "σ",
    "varsigma": "ς",
    "tau": "τ",
    "upsilon": "υ",
    "phi": "ϕ",
    "varphi": "φ",
    "chi": "χ",
    "psi": "ψ",
    "omega": "ω",
    "Gamma": "Γ",
    "Delta": "Δ",
    "Theta": "Θ",
    "Lambda": "Λ",
    "Xi": "Ξ",
    "Pi": "Π",
    "Sigma": "Σ",
    "Upsilon": "Υ",
    "Phi": "Φ",
    "Psi": "Ψ",
    "Omega": "Ω",
    "infty": "∞",
    "partial": "∂",
    "nabla": "∇",
    "ell": "ℓ",
    "hbar": "ℏ",
    "emptyset": "∅",
    "forall": "∀",
    "exists": "∃",
    "neg": "¬",
    "land": "∧",
    "lor": "∨",
    "cap": "∩",
    "cup": "∪",
    "subset": "⊂",
    "subseteq": "⊆",
    "supset": "⊃",
    "supseteq": "⊇",
    "in": "∈",
    "notin": "∉",
    "pm": "±",
    "mp": "∓",
    "times": "×",
    "div": "÷",
    "cdot": "·",
    "circ": "∘",
    "le": "≤",
    "leq": "≤",
    "ge": "≥",
    "geq": "≥",
    "neq": "≠",
    "ne": "≠",
    "approx": "≈",
    "sim": "∼",
    "equiv": "≡",
    "propto": "∝",
    "to": "→",
    "rightarrow": "→",
    "leftarrow": "←",
    "leftrightarrow": "↔",
    "Rightarrow": "⇒",
    "Leftarrow": "⇐",
    "Leftrightarrow": "⇔",
    "mapsto": "↦",
    "sqrt": "√",
    "angle": "∠",
    "degree": "°",
}

_LARGE_OPERATORS = {
    "int": "∫",
    "iint": "∬",
    "iiint": "∭",
    "oint": "∮",
    "sum": "∑",
    "prod": "∏",
    "lim": "lim",
}

_FUNCTIONS = {
    "sin",
    "cos",
    "tan",
    "cot",
    "sec",
    "csc",
    "arcsin",
    "arccos",
    "arctan",
    "sinh",
    "cosh",
    "tanh",
    "log",
    "ln",
    "lg",
    "exp",
    "max",
    "min",
    "arg",
    "det",
    "dim",
    "ker",
    "Pr",
}

_BLACKBOARD = {
    "R": "ℝ",
    "N": "ℕ",
    "Z": "ℤ",
    "Q": "ℚ",
    "C": "ℂ",
    "P": "ℙ",
    "E": "𝔼",
}


@dataclass
class _Node:
    html: str
    text: str = ""
    large_operator: bool = False


def _escape_text(value: str) -> str:
    return html.escape(str(value or ""), quote=False)


def _strip_wrappers(expr: str) -> str:
    s = str(expr or "").strip()
    pairs = (("$$", "$$"), (r"\[", r"\]"), (r"\(", r"\)"))
    changed = True
    while changed and s:
        changed = False
        for left, right in pairs:
            if s.startswith(left) and s.endswith(right):
                s = s[len(left) : len(s) - len(right)].strip()
                changed = True
                break
    return s


def _normalize_latex(expr: str) -> str:
    s = _strip_wrappers(expr)
    s = s.replace("\u00a0", " ")
    s = re.sub(r"\\(?:display|text|script|scriptscript)style\b", "", s)
    s = s.replace(r"\dfrac", r"\frac").replace(r"\tfrac", r"\frac")
    s = re.sub(r"\\ce\s*\{([^{}]*)\}", r"\\mathrm{\1}", s)
    return s.strip()


def _is_escaped(text: str, idx: int) -> bool:
    slash_count = 0
    pos = int(idx) - 1
    while pos >= 0 and text[pos] == "\\":
        slash_count += 1
        pos -= 1
    return bool(slash_count % 2)


def _split_unescaped(text: str, delimiter: str) -> list[str]:
    out: list[str] = []
    start = 0
    i = 0
    while i < len(text):
        if text.startswith(delimiter, i) and not _is_escaped(text, i):
            out.append(text[start:i])
            i += len(delimiter)
            start = i
            continue
        i += 1
    out.append(text[start:])
    return out


def _script_html(kind: str, node: _Node) -> str:
    tag = "sup" if kind == "^" else "sub"
    return f'<{tag} style="font-size:62%; line-height:0;">{node.html}</{tag}>'


def _large_operator_html(value: str) -> str:
    if value.isalpha():
        return f'<span style="{_TEXT_STYLE}">{_escape_text(value)}</span>'
    return f'<span style="font-size:150%; font-style:normal;">{_escape_text(value)}</span>'


class _LatexRichTextParser:
    def __init__(self, source: str) -> None:
        self.source = str(source or "")
        self.pos = 0

    def parse(self, stop: str = "") -> _Node:
        parts: list[str] = []
        texts: list[str] = []
        while self.pos < len(self.source):
            if stop and self.source.startswith(stop, self.pos):
                break
            node = self._parse_atom()
            node = self._attach_scripts(node)
            parts.append(node.html)
            texts.append(node.text or self._plain_from_html(node.html))
        return _Node("".join(parts), "".join(texts))

    def _plain_from_html(self, value: str) -> str:
        text = re.sub(r"<[^>]+>", "", value)
        return html.unescape(text)

    def _skip_spaces(self) -> None:
        while self.pos < len(self.source) and self.source[self.pos].isspace():
            self.pos += 1

    def _parse_required_group(self) -> _Node:
        self._skip_spaces()
        if self.pos < len(self.source) and self.source[self.pos] == "{":
            self.pos += 1
            node = self.parse("}")
            if self.pos < len(self.source) and self.source[self.pos] == "}":
                self.pos += 1
            return node
        return self._parse_atom()

    def _parse_optional_bracket_raw(self) -> str:
        self._skip_spaces()
        if self.pos >= len(self.source) or self.source[self.pos] != "[":
            return ""
        self.pos += 1
        start = self.pos
        depth = 1
        while self.pos < len(self.source) and depth > 0:
            ch = self.source[self.pos]
            if ch == "[" and not _is_escaped(self.source, self.pos):
                depth += 1
            elif ch == "]" and not _is_escaped(self.source, self.pos):
                depth -= 1
                if depth == 0:
                    raw = self.source[start : self.pos]
                    self.pos += 1
                    return raw
            self.pos += 1
        return self.source[start : self.pos]

    def _attach_scripts(self, base: _Node) -> _Node:
        lower: Optional[_Node] = None
        upper: Optional[_Node] = None
        changed = True
        while changed and self.pos < len(self.source):
            changed = False
            if self.source[self.pos] == "_":
                self.pos += 1
                lower = self._parse_script_arg()
                changed = True
            if self.pos < len(self.source) and self.source[self.pos] == "^":
                self.pos += 1
                upper = self._parse_script_arg()
                changed = True

        if lower is None and upper is None:
            return base

        if base.large_operator and lower is not None and upper is not None:
            html_value = (
                f"{base.html}"
                f'<span style="display:inline-block; line-height:95%; vertical-align:middle;">'
                f'{_script_html("^", upper)}<br/>{_script_html("_", lower)}</span>'
            )
        else:
            html_value = base.html
            if lower is not None:
                html_value += _script_html("_", lower)
            if upper is not None:
                html_value += _script_html("^", upper)

        text_value = base.text
        if lower is not None:
            text_value += f"_({lower.text})"
        if upper is not None:
            text_value += f"^({upper.text})"
        return _Node(html_value, text_value)

    def _parse_script_arg(self) -> _Node:
        self._skip_spaces()
        if self.pos < len(self.source) and self.source[self.pos] == "{":
            return self._parse_required_group()
        node = self._parse_atom()
        return self._attach_scripts(node)

    def _parse_atom(self) -> _Node:
        if self.pos >= len(self.source):
            return _Node("")

        ch = self.source[self.pos]
        if ch == "{":
            return self._parse_required_group()
        if ch == "\\":
            return self._parse_command()
        if ch == "}":
            # 顶层未配对的 `}`：必须消费掉，否则 parse() 的循环永不前进导致死循环
            self.pos += 1
            return _Node("")
        if ch.isspace():
            self.pos += 1
            while self.pos < len(self.source) and self.source[self.pos].isspace():
                self.pos += 1
            return _Node("&nbsp;", " ")
        if ch in "=+<>":
            self.pos += 1
            return _Node(f"&nbsp;{_escape_text(ch)}&nbsp;", f" {ch} ")
        if ch == "-":
            self.pos += 1
            return _Node("&nbsp;-&nbsp;", " - ")
        if ch in "()[]|,.;:":
            self.pos += 1
            return _Node(_escape_text(ch), ch)

        self.pos += 1
        return _Node(_escape_text(ch), ch)

    def _parse_command_name(self) -> str:
        self.pos += 1
        start = self.pos
        while self.pos < len(self.source) and self.source[self.pos].isalpha():
            self.pos += 1
        if self.pos == start and self.pos < len(self.source):
            self.pos += 1
        return self.source[start : self.pos]

    def _parse_command(self) -> _Node:
        name = self._parse_command_name()
        if not name:
            return _Node("\\", "\\")

        if name in {",", ":", ";"}:
            return _Node("&nbsp;", " ")
        if name in {"!", " "}:
            return _Node("", "")
        if name in {"quad", "qquad"}:
            return _Node("&nbsp;&nbsp;&nbsp;", "   ")
        if name == "\\":
            return _Node("<br/>", "\n")

        if name == "frac":
            numerator = self._parse_required_group()
            denominator = self._parse_required_group()
            value = (
                f'<span style="white-space:nowrap;">'
                f'<sup style="font-size:68%; line-height:0;">{numerator.html}</sup>'
                f'<span style="font-style:normal;">/</span>'
                f'<sub style="font-size:68%; line-height:0;">{denominator.html}</sub>'
                f"</span>"
            )
            return _Node(value, f"({numerator.text})/({denominator.text})")

        if name == "sqrt":
            degree_raw = self._parse_optional_bracket_raw()
            degree = _LatexRichTextParser(degree_raw).parse() if degree_raw else None
            body = self._parse_required_group()
            degree_html = _script_html("^", degree) if degree is not None else ""
            value = (
                f'<span style="white-space:nowrap;">{degree_html}'
                f'<span style="font-style:normal;">√</span>'
                f'<span style="text-decoration:overline;">{body.html}</span></span>'
            )
            text = f"root[{degree.text}]({body.text})" if degree is not None else f"sqrt({body.text})"
            return _Node(value, text)

        if name == "begin":
            env = self._parse_required_group().text
            return self._parse_environment(env)

        if name == "end":
            self._parse_required_group()
            return _Node("")

        if name in {"left", "right", "big", "Big", "bigg", "Bigg"}:
            self._skip_spaces()
            if self.pos < len(self.source):
                if self.source[self.pos] == "\\":
                    return self._parse_command()
                ch = self.source[self.pos]
                self.pos += 1
                return _Node("" if ch == "." else _escape_text(ch), "" if ch == "." else ch)
            return _Node("")

        if name in {"text", "mathrm", "operatorname"}:
            group = self._parse_required_group()
            value = f'<span style="{_TEXT_STYLE}">{group.html}</span>'
            return _Node(value, group.text)

        if name == "mathbf":
            group = self._parse_required_group()
            return _Node(f"<b>{group.html}</b>", group.text)

        if name == "mathit":
            group = self._parse_required_group()
            return _Node(f"<i>{group.html}</i>", group.text)

        if name == "mathbb":
            group = self._parse_required_group()
            mapped = "".join(_BLACKBOARD.get(ch, ch) for ch in group.text)
            return _Node(_escape_text(mapped), mapped)

        if name in _LARGE_OPERATORS:
            symbol = _LARGE_OPERATORS[name]
            return _Node(_large_operator_html(symbol), symbol, large_operator=True)

        if name in _FUNCTIONS:
            value = f'<span style="{_TEXT_STYLE}">{_escape_text(name)}</span>'
            return _Node(value, name)

        if name in _SYMBOLS:
            symbol = _SYMBOLS[name]
            return _Node(_escape_text(symbol), symbol)

        return _Node(_escape_text(name), name)

    def _parse_environment(self, env: str) -> _Node:
        end_token = r"\\end\{" + re.escape(env) + r"\}"
        match = re.search(end_token, self.source[self.pos :])
        if not match:
            return _Node(_escape_text(env), env)
        body = self.source[self.pos : self.pos + match.start()]
        self.pos += match.end()
        return _render_environment(env, body)


def _render_environment(env: str, body: str) -> _Node:
    rows = _split_unescaped(str(body or "").strip(), r"\\")
    parsed_rows: list[list[_Node]] = []
    plain_rows: list[str] = []
    for row in rows:
        cells = _split_unescaped(row, "&")
        parsed = [_LatexRichTextParser(cell.strip()).parse() for cell in cells]
        if parsed:
            parsed_rows.append(parsed)
            plain_rows.append(" ".join(cell.text for cell in parsed))
    if not parsed_rows:
        return _Node("")

    if env.startswith("align") or env in {"aligned", "gathered", "split", "matrix", "pmatrix", "bmatrix", "cases"}:
        out = [
            '<table border="0" cellspacing="0" cellpadding="2" align="center" '
            'style="margin:4px auto; border-collapse:collapse;">'
        ]
        for row in parsed_rows:
            out.append("<tr>")
            if env == "cases":
                out.append('<td style="font-style:normal; font-size:130%;">{</td>')
            for idx, cell in enumerate(row):
                align = "right" if idx % 2 == 0 else "left"
                out.append(f'<td align="{align}" style="padding:1px 4px;">{cell.html}</td>')
            out.append("</tr>")
        out.append("</table>")
        return _Node("".join(out), "\n".join(plain_rows))

    return _Node("<br/>".join(row[0].html for row in parsed_rows if row), "\n".join(plain_rows))


class LatexRenderer:
    @staticmethod
    def to_html(expr: str, *, display: bool = False) -> str:
        latex = _normalize_latex(expr)
        if not latex:
            return ""

        node = _LatexRichTextParser(latex).parse()
        if display:
            return (
                '<p align="center" style="margin:10px 0 14px 0;">'
                f'<span style="{_DISPLAY_STYLE}">{node.html}</span></p>'
            )
        return f'<span style="{_INLINE_STYLE}">{node.html}</span>'
