from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any
from xml.sax.saxutils import escape

_PX_PER_INCH = 96
_TWIPS_PER_INCH = 1440
_TWIPS_PER_PX = _TWIPS_PER_INCH // _PX_PER_INCH
_DEFAULT_ESSAY_BLANK_LINES = 6
_DEFAULT_ESSAY_BLANK_LINE_HEIGHT = 28
_MIN_ESSAY_BLANK_LINES = 1
_MAX_ESSAY_BLANK_LINES = 20
_MIN_ESSAY_BLANK_LINE_HEIGHT = 20
_MAX_ESSAY_BLANK_LINE_HEIGHT = 48
_LATEX_SEGMENT_RE = re.compile(r"(\$\$(?P<block>.+?)\$\$|\$(?P<inline>.+?)\$)", re.DOTALL)

_MATH_SYMBOLS = {
    "alpha": "\u03b1",
    "beta": "\u03b2",
    "gamma": "\u03b3",
    "delta": "\u03b4",
    "epsilon": "\u03b5",
    "varepsilon": "\u03b5",
    "theta": "\u03b8",
    "lambda": "\u03bb",
    "mu": "\u03bc",
    "pi": "\u03c0",
    "rho": "\u03c1",
    "sigma": "\u03c3",
    "phi": "\u03c6",
    "varphi": "\u03c6",
    "omega": "\u03c9",
    "Delta": "\u0394",
    "Theta": "\u0398",
    "Lambda": "\u039b",
    "Pi": "\u03a0",
    "Sigma": "\u03a3",
    "Phi": "\u03a6",
    "Omega": "\u03a9",
    "times": "\u00d7",
    "cdot": "\u00b7",
    "pm": "\u00b1",
    "le": "\u2264",
    "leq": "\u2264",
    "ge": "\u2265",
    "geq": "\u2265",
    "neq": "\u2260",
    "approx": "\u2248",
    "infty": "\u221e",
    "to": "\u2192",
    "rightarrow": "\u2192",
    "leftarrow": "\u2190",
    "Rightarrow": "\u21d2",
    "sum": "\u2211",
    "prod": "\u220f",
    "int": "\u222b",
    "lim": "lim",
    "log": "log",
    "ln": "ln",
    "sin": "sin",
    "cos": "cos",
    "tan": "tan",
}
_IGNORED_MATH_STYLE_COMMANDS = {
    "displaystyle",
    "textstyle",
    "scriptstyle",
    "scriptscriptstyle",
}
_TEXT_MATH_COMMANDS = {
    "text",
    "textm",
    "textrm",
    "mathrm",
    "mathbf",
    "mathit",
    "mathsf",
    "mathtt",
    "operatorname",
}
_MATH_SPACING_COMMANDS = {
    ",": " ",
    ";": " ",
    ":": " ",
    " ": " ",
    "!": "",
}
_LATEX_TEXT_ESCAPES = {
    "\\\\": "\\",
    "\\{": "{",
    "\\}": "}",
    "\\$": "$",
    "\\%": "%",
    "\\&": "&",
    "\\#": "#",
    "\\_": "_",
}


def paragraph(
    text: str,
    *,
    bold: bool = False,
    italic: bool = False,
    size: int | None = None,
    align: str | None = None,
    spacing_before: int | None = None,
    spacing_after: int | None = None,
    line: int | None = None,
) -> str:
    return _paragraph_from_runs(
        _text_runs(text, _run_props_xml(bold=bold, italic=italic, size=size)),
        align=align,
        spacing_before=spacing_before,
        spacing_after=spacing_after,
        line=line,
    )


def paragraph_with_latex(
    text: str,
    *,
    bold: bool = False,
    italic: bool = False,
    size: int | None = None,
    align: str | None = None,
    spacing_before: int | None = None,
    spacing_after: int | None = None,
    line: int | None = None,
) -> str:
    run_props_xml = _run_props_xml(bold=bold, italic=italic, size=size)
    return _paragraph_from_runs(
        _latex_runs(text, run_props_xml),
        align=align,
        spacing_before=spacing_before,
        spacing_after=spacing_after,
        line=line,
    )


def essay_answer_space(blank_space: Any, *, scale: float = 1.0) -> str:
    minimum_height_twips = _MIN_ESSAY_BLANK_LINES * _MIN_ESSAY_BLANK_LINE_HEIGHT * _TWIPS_PER_PX
    height_twips = max(minimum_height_twips, int(_essay_blank_height_twips(blank_space) * scale))
    return (
        "<w:p>"
        f'<w:pPr><w:spacing w:before="0" w:after="0" w:line="{height_twips}" w:lineRule="exact"/></w:pPr>'
        '<w:r><w:t xml:space="preserve"> </w:t></w:r>'
        "</w:p>"
    )


def _essay_blank_height_twips(blank_space: Any) -> int:
    source = blank_space if isinstance(blank_space, dict) else {}
    lines = _bounded_int(
        source.get("lines"),
        _DEFAULT_ESSAY_BLANK_LINES,
        _MIN_ESSAY_BLANK_LINES,
        _MAX_ESSAY_BLANK_LINES,
    )
    line_height = _bounded_int(
        source.get("lineHeight"),
        _DEFAULT_ESSAY_BLANK_LINE_HEIGHT,
        _MIN_ESSAY_BLANK_LINE_HEIGHT,
        _MAX_ESSAY_BLANK_LINE_HEIGHT,
    )
    return lines * line_height * _TWIPS_PER_PX


def _bounded_int(value: Any, fallback: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = fallback
    return max(minimum, min(maximum, parsed))


def _run_props_xml(*, bold: bool = False, italic: bool = False, size: int | None = None) -> str:
    run_props = []
    if bold:
        run_props.append("<w:b/>")
    if italic:
        run_props.append("<w:i/>")
    if size is not None:
        run_props.append(f'<w:sz w:val="{size}"/>')
    return f"<w:rPr>{''.join(run_props)}</w:rPr>" if run_props else ""


def _paragraph_from_runs(
    runs: str,
    *,
    align: str | None = None,
    spacing_before: int | None = None,
    spacing_after: int | None = None,
    line: int | None = None,
) -> str:
    props = []
    if align:
        props.append(f'<w:jc w:val="{align}"/>')
    spacing_attrs = []
    if spacing_before is not None:
        spacing_attrs.append(f'w:before="{spacing_before}"')
    if spacing_after is not None:
        spacing_attrs.append(f'w:after="{spacing_after}"')
    if line is not None:
        spacing_attrs.append(f'w:line="{line}"')
        spacing_attrs.append('w:lineRule="auto"')
    if spacing_attrs:
        props.append(f"<w:spacing {' '.join(spacing_attrs)}/>")
    paragraph_props = f"<w:pPr>{''.join(props)}</w:pPr>" if props else ""
    return f"<w:p>{paragraph_props}{runs}</w:p>"


def _text_runs(text: str, run_props_xml: str = "") -> str:
    escaped_lines = escape(text).splitlines() or [""]
    text_runs = []
    for index, line in enumerate(escaped_lines):
        if index:
            text_runs.append(f"<w:r>{run_props_xml}<w:br/></w:r>")
        text_runs.append(f'<w:r>{run_props_xml}<w:t xml:space="preserve">{line}</w:t></w:r>')
    return "".join(text_runs)


def _latex_runs(text: str, run_props_xml: str) -> str:
    runs: list[str] = []
    position = 0
    for match in _LATEX_SEGMENT_RE.finditer(text):
        if match.start() > position:
            runs.append(_text_runs(text[position : match.start()], run_props_xml))
        latex = match.group("block") or match.group("inline") or ""
        runs.append(_math_run(latex))
        position = match.end()
    if position < len(text):
        runs.append(_text_runs(text[position:], run_props_xml))
    if not runs:
        runs.append(_text_runs("", run_props_xml))
    return "".join(runs)


def _math_run(latex: str) -> str:
    math_xml = _latex_to_omml(latex)
    return f"<m:oMath>{math_xml}</m:oMath>"


def _latex_to_omml(latex: str) -> str:
    normalized = latex.strip()
    if not normalized:
        return _math_text("")
    parser = _LatexMathParser(normalized)
    return parser.parse()


def _math_text(text: str) -> str:
    return f"<m:r><m:t>{escape(text)}</m:t></m:r>"


def _math_arg(xml: str) -> str:
    return f"<m:e>{xml}</m:e>"


class _LatexMathParser:
    def __init__(self, source: str) -> None:
        self.source = source
        self.length = len(source)

    def parse(self) -> str:
        xml, _ = self._parse_sequence(0, None)
        return xml or _math_text(self.source)

    def _parse_sequence(self, position: int, stop: str | None) -> tuple[str, int]:
        parts: list[str] = []
        while position < self.length:
            char = self.source[position]
            if stop and char == stop:
                return "".join(parts), position + 1
            if char == "}":
                return "".join(parts), position + 1
            atom, position = self._parse_atom(position)
            atom, position = self._parse_scripts(atom, position)
            parts.append(atom)
        return "".join(parts), position

    def _parse_atom(self, position: int) -> tuple[str, int]:
        char = self.source[position]
        if char == "{":
            return self._parse_sequence(position + 1, "}")
        if char == "\\":
            return self._parse_command(position)
        if char.isspace():
            end = position + 1
            while end < self.length and self.source[end].isspace():
                end += 1
            return _math_text(" "), end

        end = position
        while end < self.length and self.source[end] not in "\\{}^_":
            if self.source[end].isspace():
                break
            end += 1
        return _math_text(self.source[position:end]), end

    def _parse_command(self, position: int) -> tuple[str, int]:
        command_start = position + 1
        command_end = command_start
        while command_end < self.length and self.source[command_end].isalpha():
            command_end += 1
        command = self.source[command_start:command_end]
        if not command and command_end < self.length:
            escaped_command = self.source[command_end]
            if escaped_command in _MATH_SPACING_COMMANDS:
                return _math_text(_MATH_SPACING_COMMANDS[escaped_command]), command_end + 1
            return _math_text(self.source[command_end]), command_end + 1

        handlers: dict[str, Callable[[int], tuple[str, int]]] = {
            "frac": self._parse_fraction,
            "dfrac": self._parse_fraction,
            "tfrac": self._parse_fraction,
            "sqrt": self._parse_sqrt,
        }
        if command in handlers:
            return handlers[command](command_end)
        if command in _IGNORED_MATH_STYLE_COMMANDS:
            return "", self._skip_spaces(command_end)
        if command in _TEXT_MATH_COMMANDS:
            return self._parse_text_command(command_end)
        if command in ("left", "right"):
            return "", command_end
        if command in ("quad", "qquad"):
            return _math_text(" "), command_end
        if command in _MATH_SYMBOLS:
            return _math_text(_MATH_SYMBOLS[command]), command_end
        return _math_text(f"\\{command}"), command_end

    def _parse_fraction(self, position: int) -> tuple[str, int]:
        numerator, position = self._parse_required_group(position)
        denominator, position = self._parse_required_group(position)
        return f"<m:f><m:num>{numerator}</m:num><m:den>{denominator}</m:den></m:f>", position

    def _parse_sqrt(self, position: int) -> tuple[str, int]:
        radicand, position = self._parse_required_group(position)
        return f"<m:rad><m:deg/><m:e>{radicand}</m:e></m:rad>", position

    def _parse_text_command(self, position: int) -> tuple[str, int]:
        position = self._skip_spaces(position)
        if position >= self.length:
            return _math_text(""), position
        if self.source[position] == "{":
            text, position = self._read_balanced_text(position)
            return _math_text(_plain_latex_text(text)), position

        end = position
        while end < self.length and self.source[end] not in "\\{}^_":
            if self.source[end].isspace():
                break
            end += 1
        if end == position:
            return self._parse_atom(position)
        return _math_text(_plain_latex_text(self.source[position:end])), end

    def _read_balanced_text(self, position: int) -> tuple[str, int]:
        depth = 0
        start = position + 1
        index = position
        while index < self.length:
            char = self.source[index]
            if char == "\\":
                index += 2
                continue
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return self.source[start:index], index + 1
            index += 1
        return self.source[start:], self.length

    def _parse_required_group(self, position: int) -> tuple[str, int]:
        position = self._skip_spaces(position)
        if position >= self.length:
            return _math_text(""), position
        if self.source[position] == "{":
            return self._parse_sequence(position + 1, "}")
        atom, position = self._parse_atom(position)
        return atom, position

    def _parse_scripts(self, base: str, position: int) -> tuple[str, int]:
        subscript: str | None = None
        superscript: str | None = None
        while position < self.length and self.source[position] in ("_", "^"):
            marker = self.source[position]
            script, position = self._parse_required_group(position + 1)
            if marker == "_":
                subscript = script
            else:
                superscript = script

        if subscript is not None and superscript is not None:
            return f"<m:sSubSup>{_math_arg(base)}<m:sub>{subscript}</m:sub><m:sup>{superscript}</m:sup></m:sSubSup>", position
        if subscript is not None:
            return f"<m:sSub>{_math_arg(base)}<m:sub>{subscript}</m:sub></m:sSub>", position
        if superscript is not None:
            return f"<m:sSup>{_math_arg(base)}<m:sup>{superscript}</m:sup></m:sSup>", position
        return base, position

    def _skip_spaces(self, position: int) -> int:
        while position < self.length and self.source[position].isspace():
            position += 1
        return position


def _plain_latex_text(text: str) -> str:
    plain = text
    for latex_escape, replacement in _LATEX_TEXT_ESCAPES.items():
        plain = plain.replace(latex_escape, replacement)
    plain = plain.replace("~", " ")
    return re.sub(r"\\([A-Za-z]+)", r"\1", plain)
