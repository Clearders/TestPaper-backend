from __future__ import annotations

import base64
import binascii
import re
import struct
import zipfile
from collections.abc import Callable
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit
from xml.sax.saxutils import escape

from testpaper_backend.schemas import PaperEntity

DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
DEFAULT_TEMPLATE_PATH = Path(__file__).with_name("ExamPaperTemplate.docx")
DEFAULT_IMAGE_UPLOAD_DIR = Path(__file__).resolve().parents[1] / "uploaded-images"
_DOCX_NS = (
    'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
    'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
    'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture" '
    'xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math"'
)
_EMU_PER_INCH = 914400
_PX_PER_INCH = 96
_TWIPS_PER_INCH = 1440
_TWIPS_PER_PX = _TWIPS_PER_INCH // _PX_PER_INCH
_MAX_IMAGE_WIDTH_EMU = int(5.8 * _EMU_PER_INCH)
_DEFAULT_ESSAY_BLANK_LINES = 6
_DEFAULT_ESSAY_BLANK_LINE_HEIGHT = 28
_MIN_ESSAY_BLANK_LINES = 1
_MAX_ESSAY_BLANK_LINES = 20
_MIN_ESSAY_BLANK_LINE_HEIGHT = 20
_MAX_ESSAY_BLANK_LINE_HEIGHT = 48
_LATEX_SEGMENT_RE = re.compile(r"(\$\$(?P<block>.+?)\$\$|\$(?P<inline>.+?)\$)", re.DOTALL)
_TEMPLATE_TITLE_TEXT = "2020-2021 Academic Year First Semester Final Exam Paper"
_PNG_CONTENT_TYPE = '<Default Extension="png" ContentType="image/png"/>'
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


def docx_filename(title: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|]+', " ", title).strip()
    cleaned = re.sub(r"\s+", " ", cleaned) or "examination-paper"
    return f"{cleaned[:80]}.docx"


def build_paper_docx(
    paper: PaperEntity,
    questions: list[dict[str, Any]],
    *,
    include_answer: bool,
    template_path: Path | str | None = None,
) -> bytes:
    template = Path(template_path) if template_path is not None else DEFAULT_TEMPLATE_PATH
    use_template = template.is_file()
    images: list[tuple[str, bytes]] = []
    image_relationships: list[str] = []
    paragraphs: list[str] = []
    if not use_template:
        paragraphs.extend(
            [
                _paragraph(paper.title, bold=True, size=32, align="center"),
            ]
        )
    paragraphs.extend(
        [
            _paragraph(
                f"Subject: {paper.subject}    Duration: {paper.duration} minutes    Total Marks: {paper.totalMarks}",
                size=22,
                align="center",
            ),
            _paragraph(""),
        ]
    )

    paragraphs.extend(
        _question_paragraphs(
            questions,
            include_answer=include_answer,
            images=images,
            image_relationships=image_relationships,
        )
    )
    document_body_xml = "".join(paragraphs)
    if use_template:
        return _build_docx_from_template(template, paper.title, document_body_xml, images, image_relationships)
    return _build_standalone_docx(document_body_xml, images, image_relationships)


def _question_paragraphs(
    questions: list[dict[str, Any]],
    *,
    include_answer: bool,
    images: list[tuple[str, bytes]],
    image_relationships: list[str],
) -> list[str]:
    paragraphs: list[str] = []

    for index, question in enumerate(questions, start=1):
        marks_text = f" ({question['marks']} marks)" if question.get("marks") else ""
        paragraphs.append(_paragraph_with_latex(f"{index}. {question.get('text', '')}{marks_text}", bold=True, size=23))

        options = question.get("options") or []
        if options:
            for option_index, option in enumerate(options):
                label = chr(65 + option_index)
                paragraphs.append(_paragraph_with_latex(f"    {label}. {option}", size=22))

        for image in question.get("images") or []:
            relationship_id = _add_image_relationship(image.get("url", ""), images, image_relationships)
            if relationship_id:
                paragraphs.append(_image_paragraph(relationship_id, len(images), images[-1][1]))
                if image.get("caption"):
                    paragraphs.append(_paragraph(str(image["caption"]), italic=True, size=18, align="center"))

        if question.get("type") == "essay":
            paragraphs.append(_essay_answer_space(question.get("essayBlankSpace")))

        if include_answer and "answer" in question:
            ans = question.get('answer', '')
            if isinstance(ans, list):
                ans = ', '.join(ans)
            paragraphs.append(_paragraph_with_latex(f"Answer: {ans}", italic=True, size=21))

        paragraphs.append(_paragraph(""))

    return paragraphs


def _build_standalone_docx(
    document_body_xml: str,
    images: list[tuple[str, bytes]],
    image_relationships: list[str],
) -> bytes:
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f"<w:document {_DOCX_NS}>"
        "<w:body>"
        f"{document_body_xml}"
        '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
        '<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" w:header="720" w:footer="720" w:gutter="0"/>'
        "</w:sectPr>"
        "</w:body>"
        "</w:document>"
    )

    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _content_types_xml(images))
        archive.writestr("_rels/.rels", _package_relationships_xml())
        archive.writestr("word/document.xml", document_xml)
        archive.writestr("word/_rels/document.xml.rels", _document_relationships_xml(image_relationships))
        for index, (_, image_bytes) in enumerate(images, start=1):
            archive.writestr(f"word/media/image{index}.png", image_bytes)

    return buffer.getvalue()


def _build_docx_from_template(
    template_path: Path,
    paper_title: str,
    document_body_xml: str,
    images: list[tuple[str, bytes]],
    image_relationships: list[str],
) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(template_path, "r") as template_archive:
        document_xml = template_archive.read("word/document.xml").decode("utf-8")
        document_xml = _replace_template_title(document_xml, paper_title)
        document_xml = _ensure_document_namespaces(document_xml)
        document_xml = _insert_before_final_section(document_xml, document_body_xml)

        relationships_xml = template_archive.read("word/_rels/document.xml.rels").decode("utf-8")
        relationships_xml = _append_document_relationships(relationships_xml, image_relationships)

        content_types_xml = template_archive.read("[Content_Types].xml").decode("utf-8")
        content_types_xml = _ensure_png_content_type(content_types_xml, images)

        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as output_archive:
            for entry in template_archive.infolist():
                if entry.filename in {"word/document.xml", "word/_rels/document.xml.rels", "[Content_Types].xml"}:
                    continue
                output_archive.writestr(entry, template_archive.read(entry.filename))

            output_archive.writestr("[Content_Types].xml", content_types_xml)
            output_archive.writestr("word/document.xml", document_xml)
            output_archive.writestr("word/_rels/document.xml.rels", relationships_xml)
            for index, (_, image_bytes) in enumerate(images, start=1):
                output_archive.writestr(f"word/media/image{index}.png", image_bytes)

    return buffer.getvalue()


def _replace_template_title(document_xml: str, paper_title: str) -> str:
    return document_xml.replace(f"<w:t>{escape(_TEMPLATE_TITLE_TEXT)}</w:t>", f"<w:t>{escape(paper_title)}</w:t>", 1)


def _ensure_document_namespaces(document_xml: str) -> str:
    namespace_attrs = {
        "xmlns:a": 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"',
        "xmlns:pic": 'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"',
    }
    document_tag_match = re.search(r"<w:document\b[^>]*>", document_xml)
    if document_tag_match is None:
        return document_xml

    document_tag = document_tag_match.group(0)
    additions = [attribute for prefix, attribute in namespace_attrs.items() if prefix not in document_tag]
    if not additions:
        return document_xml

    updated_tag = document_tag[:-1] + " " + " ".join(additions) + ">"
    return document_xml[:document_tag_match.start()] + updated_tag + document_xml[document_tag_match.end():]


def _insert_before_final_section(document_xml: str, document_body_xml: str) -> str:
    final_section_match = re.search(r"<w:sectPr\b.*?</w:sectPr>\s*</w:body>", document_xml, re.DOTALL)
    if final_section_match is not None:
        return document_xml[:final_section_match.start()] + document_body_xml + document_xml[final_section_match.start():]
    return document_xml.replace("</w:body>", f"{document_body_xml}</w:body>", 1)


def _append_document_relationships(relationships_xml: str, image_relationships: list[str]) -> str:
    if not image_relationships:
        return relationships_xml
    return relationships_xml.replace("</Relationships>", f"{''.join(image_relationships)}</Relationships>", 1)


def _ensure_png_content_type(content_types_xml: str, images: list[tuple[str, bytes]]) -> str:
    if not images or 'Extension="png"' in content_types_xml:
        return content_types_xml
    return content_types_xml.replace("</Types>", f"{_PNG_CONTENT_TYPE}</Types>", 1)


def _paragraph(text: str, *, bold: bool = False, italic: bool = False, size: int | None = None, align: str | None = None) -> str:
    return _paragraph_from_runs(_text_runs(text, _run_props_xml(bold=bold, italic=italic, size=size)), align=align)


def _essay_answer_space(blank_space: Any) -> str:
    height_twips = _essay_blank_height_twips(blank_space)
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


def _paragraph_with_latex(text: str, *, bold: bool = False, italic: bool = False, size: int | None = None, align: str | None = None) -> str:
    run_props_xml = _run_props_xml(bold=bold, italic=italic, size=size)
    return _paragraph_from_runs(_latex_runs(text, run_props_xml), align=align)


def _run_props_xml(*, bold: bool = False, italic: bool = False, size: int | None = None) -> str:
    run_props = []
    if bold:
        run_props.append("<w:b/>")
    if italic:
        run_props.append("<w:i/>")
    if size is not None:
        run_props.append(f'<w:sz w:val="{size}"/>')
    return f"<w:rPr>{''.join(run_props)}</w:rPr>" if run_props else ""


def _paragraph_from_runs(runs: str, *, align: str | None = None) -> str:
    props = []
    if align:
        props.append(f'<w:jc w:val="{align}"/>')
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
            runs.append(_text_runs(text[position:match.start()], run_props_xml))
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
    return f'<m:r><m:t>{escape(text)}</m:t></m:r>'


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


def _image_paragraph(relationship_id: str, image_index: int, image_bytes: bytes) -> str:
    cx, cy = _image_dimensions_emu(image_bytes)
    return (
        "<w:p><w:pPr><w:jc w:val=\"center\"/></w:pPr><w:r><w:drawing>"
        '<wp:inline distT="0" distB="0" distL="0" distR="0">'
        f'<wp:extent cx="{cx}" cy="{cy}"/>'
        f'<wp:docPr id="{image_index}" name="Question Image {image_index}"/>'
        '<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
        "<pic:pic><pic:nvPicPr>"
        f'<pic:cNvPr id="{image_index}" name="image{image_index}.png"/>'
        "<pic:cNvPicPr/>"
        "</pic:nvPicPr>"
        "<pic:blipFill>"
        f'<a:blip r:embed="{relationship_id}"/>'
        "<a:stretch><a:fillRect/></a:stretch>"
        "</pic:blipFill>"
        "<pic:spPr>"
        f'<a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom>'
        "</pic:spPr>"
        "</pic:pic>"
        "</a:graphicData></a:graphic>"
        "</wp:inline>"
        "</w:drawing></w:r></w:p>"
    )


def _add_image_relationship(url: str, images: list[tuple[str, bytes]], relationships: list[str]) -> str | None:
    prefix = "data:image/png;base64,"
    if url.startswith(prefix):
        try:
            image_bytes = base64.b64decode(url[len(prefix):], validate=True)
        except (binascii.Error, ValueError):
            return None
    else:
        image_bytes = _read_uploaded_image(url)
        if image_bytes is None:
            return None

    images.append((url, image_bytes))
    relationship_id = f"rIdImage{len(images)}"
    relationships.append(
        f'<Relationship Id="{relationship_id}" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
        f'Target="media/image{len(images)}.png"/>'
    )
    return relationship_id


def _read_uploaded_image(url: str) -> bytes | None:
    parsed = urlsplit(url)
    path = unquote(parsed.path or url)
    prefix = "/api/v1/images/files/"
    if not path.startswith(prefix):
        return None

    filename = Path(path[len(prefix):]).name
    if not filename.lower().endswith(".png"):
        return None

    image_path = DEFAULT_IMAGE_UPLOAD_DIR / filename
    if not image_path.is_file():
        return None
    return image_path.read_bytes()


def _image_dimensions_emu(image_bytes: bytes) -> tuple[int, int]:
    image_size = _png_size(image_bytes)
    if image_size is None:
        return _MAX_IMAGE_WIDTH_EMU, int(_MAX_IMAGE_WIDTH_EMU * 0.55)

    width, height = image_size
    cx = int(width / _PX_PER_INCH * _EMU_PER_INCH)
    cy = int(height / _PX_PER_INCH * _EMU_PER_INCH)
    if cx > _MAX_IMAGE_WIDTH_EMU:
        scale = _MAX_IMAGE_WIDTH_EMU / cx
        cx = _MAX_IMAGE_WIDTH_EMU
        cy = int(cy * scale)
    return cx, cy


def _png_size(image_bytes: bytes) -> tuple[int, int] | None:
    if len(image_bytes) < 24 or image_bytes[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    return struct.unpack(">II", image_bytes[16:24])


def _content_types_xml(images: list[tuple[str, bytes]]) -> str:
    image_default = '<Default Extension="png" ContentType="image/png"/>' if images else ""
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        f"{image_default}"
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )


def _package_relationships_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/>'
        "</Relationships>"
    )


def _document_relationships_xml(image_relationships: list[str]) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f"{''.join(image_relationships)}"
        "</Relationships>"
    )
