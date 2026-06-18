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
from testpaper_backend.services.images import IMAGE_UPLOAD_DIR

DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
DEFAULT_TEMPLATE_PATH = Path(__file__).with_name("试卷模板.docx")
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
_TEMPLATE_TYPE_ORDER = (
    "single_choice",
    "multiple_choice",
    "true_false",
    "blank",
    "short_answer",
    "essay",
)
_TEMPLATE_LEFT_TYPES = frozenset(_TEMPLATE_TYPE_ORDER[:4])
_TEMPLATE_OBJECTIVE_TYPES = frozenset(("single_choice", "multiple_choice", "true_false"))
_DENSITY_NORMAL = "normal"
_DENSITY_COMPACT = "compact"
_DENSITY_DENSE = "dense"
_DENSITY_AUTO = "auto"
_TEMPLATE_COMPACT_TOTAL_THRESHOLD = 14
_TEMPLATE_DENSE_TOTAL_THRESHOLD = 24
_TEMPLATE_COMPACT_OBJECTIVE_THRESHOLD = 8
_TEMPLATE_DENSE_OBJECTIVE_THRESHOLD = 14
_TEMPLATE_COMPACT_COLUMN_CAPACITY = 45.0
_TEMPLATE_DENSE_COLUMN_CAPACITY = 42.0
_TEMPLATE_TYPE_LABELS = {
    "single_choice": "单项选择题",
    "multiple_choice": "多项选择题",
    "true_false": "判断题",
    "blank": "填空题",
    "short_answer": "简答题",
    "essay": "计算与论述题",
}
_QUESTION_SECTION_XML = (
    '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
    '<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" '
    'w:header="720" w:footer="720" w:gutter="0"/>'
    "</w:sectPr>"
)
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
    layout_density: str = _DENSITY_AUTO,
    template_path: Path | str | None = None,
) -> bytes:
    density = _normalize_layout_density(layout_density)
    template = Path(template_path) if template_path is not None else DEFAULT_TEMPLATE_PATH
    use_template = template.is_file()
    images: list[tuple[str, bytes]] = []
    image_relationships: list[str] = []
    if use_template:
        page_cells, compact_layout = _template_question_pages(
            paper,
            questions,
            include_answer=include_answer,
            images=images,
            image_relationships=image_relationships,
            layout_density=density,
        )
        return _build_docx_from_template(
            template,
            paper.title,
            page_cells,
            images,
            image_relationships,
            compact_layout=compact_layout,
        )

    paragraphs: list[str] = []
    paragraphs.append(_paragraph(paper.title, bold=True, size=32, align="center"))
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
            density=density,
        )
    )
    document_body_xml = "".join(paragraphs)
    return _build_standalone_docx(document_body_xml, images, image_relationships)


def resolve_layout_density(
    questions: list[dict[str, Any]],
    layout_density: str = _DENSITY_AUTO,
    *,
    template_path: Path | str | None = None,
) -> str:
    density = _normalize_layout_density(layout_density)
    if density != _DENSITY_AUTO:
        return density

    template = Path(template_path) if template_path is not None else DEFAULT_TEMPLATE_PATH
    if template.is_file():
        return _template_overall_density(_group_template_questions(questions))
    return _DENSITY_NORMAL


def _question_paragraphs(
    questions: list[dict[str, Any]],
    *,
    include_answer: bool,
    images: list[tuple[str, bytes]],
    image_relationships: list[str],
    density: str = _DENSITY_NORMAL,
) -> list[str]:
    return _question_paragraphs_for_items(
        list(enumerate(questions, start=1)),
        include_answer=include_answer,
        images=images,
        image_relationships=image_relationships,
        localized=False,
        density=_DENSITY_NORMAL if density == _DENSITY_AUTO else density,
    )


def _normalize_layout_density(layout_density: str) -> str:
    value = layout_density.value if hasattr(layout_density, "value") else str(layout_density)
    return value if value in {_DENSITY_AUTO, _DENSITY_NORMAL, _DENSITY_COMPACT, _DENSITY_DENSE} else _DENSITY_AUTO


def _question_paragraphs_for_items(
    numbered_questions: list[tuple[int, dict[str, Any]]],
    *,
    include_answer: bool,
    images: list[tuple[str, bytes]],
    image_relationships: list[str],
    localized: bool,
    density: str = _DENSITY_NORMAL,
) -> list[str]:
    paragraphs: list[str] = []
    is_compact = density in {_DENSITY_COMPACT, _DENSITY_DENSE}
    question_size = {_DENSITY_NORMAL: 23, _DENSITY_COMPACT: 21, _DENSITY_DENSE: 19}[density]
    option_size = {_DENSITY_NORMAL: 22, _DENSITY_COMPACT: 19, _DENSITY_DENSE: 17}[density]
    answer_size = {_DENSITY_NORMAL: 21, _DENSITY_COMPACT: 18, _DENSITY_DENSE: 16}[density]
    line_height = None if density == _DENSITY_NORMAL else 220

    for index, question in numbered_questions:
        marks_text = ""
        if question.get("marks"):
            marks_text = f"（{question['marks']}分）" if localized else f" ({question['marks']} marks)"
        paragraphs.append(
            _paragraph_with_latex(
                f"{index}. {question.get('text', '')}{marks_text}",
                bold=True,
                size=question_size,
                spacing_after=0 if is_compact else None,
                line=line_height,
            )
        )

        options = question.get("options") or []
        if options:
            paragraphs.extend(_option_paragraphs(options, density=density, size=option_size, line=line_height))

        for image in question.get("images") or []:
            relationship_id = _add_image_relationship(image.get("url", ""), images, image_relationships)
            if relationship_id:
                paragraphs.append(_image_paragraph(relationship_id, len(images), images[-1][1]))
                if image.get("caption"):
                    paragraphs.append(_paragraph(str(image["caption"]), italic=True, size=18, align="center"))

        if question.get("type") == "essay":
            blank_scale = {_DENSITY_NORMAL: 1.0, _DENSITY_COMPACT: 0.75, _DENSITY_DENSE: 0.55}[density]
            paragraphs.append(_essay_answer_space(question.get("essayBlankSpace"), scale=blank_scale))

        if include_answer and "answer" in question:
            ans = question.get('answer', '')
            if isinstance(ans, list):
                ans = ', '.join(ans)
            answer_label = "答案：" if localized else "Answer: "
            paragraphs.append(
                _paragraph_with_latex(
                    f"{answer_label}{ans}",
                    italic=True,
                    size=answer_size,
                    spacing_after=0 if is_compact else None,
                    line=line_height,
                )
            )

        if not is_compact:
            paragraphs.append(_paragraph(""))

    return paragraphs


def _option_paragraphs(options: list[Any], *, density: str, size: int, line: int | None) -> list[str]:
    option_texts = [f"{chr(65 + index)}. {option}" for index, option in enumerate(options)]
    if density == _DENSITY_DENSE:
        return [_paragraph_with_latex("    " + "    ".join(option_texts), size=size, spacing_after=0, line=line)]
    if density == _DENSITY_COMPACT:
        return [
            _paragraph_with_latex(
                "    " + "    ".join(option_texts[index : index + 2]),
                size=size,
                spacing_after=0,
                line=line,
            )
            for index in range(0, len(option_texts), 2)
        ]
    return [_paragraph_with_latex(f"    {option}", size=size) for option in option_texts]


def _template_question_pages(
    paper: PaperEntity,
    questions: list[dict[str, Any]],
    *,
    include_answer: bool,
    images: list[tuple[str, bytes]],
    image_relationships: list[str],
    layout_density: str = _DENSITY_AUTO,
) -> tuple[list[tuple[str, str]], bool]:
    grouped = _group_template_questions(questions)
    overall_density = layout_density if layout_density != _DENSITY_AUTO else _template_overall_density(grouped)
    if overall_density == _DENSITY_NORMAL:
        left_cell_xml, right_cell_xml, compact_layout = _template_question_cells(
            paper,
            questions,
            include_answer=include_answer,
            images=images,
            image_relationships=image_relationships,
            layout_density=overall_density,
        )
        return [(left_cell_xml, right_cell_xml)], compact_layout

    columns: list[list[str]] = [
        [
            _paragraph(
                f"科目：{paper.subject}    考试时长：{paper.duration}分钟    满分：{paper.totalMarks}分",
                bold=True,
                size=20,
                spacing_after=0,
            ),
            _paragraph(
                "答题说明：请将答案填写在相应位置，计算与论述题请写出必要步骤。",
                italic=True,
                size=18,
                spacing_after=0,
            ),
        ]
    ]
    column_units = [_template_intro_units()]
    capacity = _template_column_capacity(overall_density)
    section_number = 0
    question_number = 1

    def push_block(block_xml: str, block_units: float) -> None:
        if column_units[-1] > 0 and column_units[-1] + block_units > capacity:
            columns.append([])
            column_units.append(0.0)
        columns[-1].append(block_xml)
        column_units[-1] += block_units

    for question_type in _TEMPLATE_TYPE_ORDER:
        grouped_questions = grouped.get(question_type) or []
        if not grouped_questions:
            continue

        items = list(enumerate(grouped_questions, start=question_number))
        question_number += len(items)
        section_number += 1
        section_density = _template_section_density(question_type, len(grouped_questions), len(questions), overall_density)
        heading_xml = _paragraph(
            _template_section_heading(section_number, question_type, items),
            bold=True,
            size=20 if section_density == _DENSITY_DENSE else 22,
            spacing_after=0,
        )
        heading_units = _template_heading_units(section_density)

        for item_index, item in enumerate(items):
            question_xml = "".join(
                _question_paragraphs_for_items(
                    [item],
                    include_answer=include_answer,
                    images=images,
                    image_relationships=image_relationships,
                    localized=True,
                    density=section_density,
                )
            )
            block_xml = f"{heading_xml}{question_xml}" if item_index == 0 else question_xml
            block_units = _template_question_units(item[1], include_answer=include_answer, density=section_density)
            if item_index == 0:
                block_units += heading_units
            push_block(block_xml, block_units)

    if len(columns) == 1 and len(columns[0]) == 2:
        columns[0].append(_paragraph("本栏暂无题目。", italic=True, size=20))

    page_cells: list[tuple[str, str]] = []
    for index in range(0, len(columns), 2):
        left_xml = "".join(columns[index])
        right_xml = "".join(columns[index + 1]) if index + 1 < len(columns) else _paragraph("本栏暂无题目。", italic=True, size=20)
        page_cells.append((left_xml, right_xml))
    return page_cells, True


def _group_template_questions(questions: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {qtype: [] for qtype in _TEMPLATE_TYPE_ORDER}
    for question in questions:
        question_type = question.get("type", "")
        type_value = question_type.value if hasattr(question_type, "value") else str(question_type)
        target_type = type_value if type_value in grouped else "short_answer"
        grouped[target_type].append(question)
    return grouped


def _template_intro_units() -> float:
    return 3.0


def _template_column_capacity(density: str) -> float:
    return _TEMPLATE_DENSE_COLUMN_CAPACITY if density == _DENSITY_DENSE else _TEMPLATE_COMPACT_COLUMN_CAPACITY


def _template_heading_units(density: str) -> float:
    return 1.2 if density == _DENSITY_DENSE else 1.5


def _template_question_units(question: dict[str, Any], *, include_answer: bool, density: str) -> float:
    options = question.get("options") or []
    if density == _DENSITY_DENSE:
        units = 1.7 + (0.9 if options else 0.0)
    elif density == _DENSITY_COMPACT:
        units = 2.0 + ((len(options) + 1) // 2 if options else 0.0)
    else:
        units = 2.8 + len(options)

    if include_answer and "answer" in question:
        units += 0.8 if density != _DENSITY_NORMAL else 1.1
    if question.get("type") == "essay":
        units += 4.0 if density == _DENSITY_DENSE else 5.0
    if question.get("images"):
        units += 6.0 * len(question.get("images") or [])
    return units


def _template_question_cells(
    paper: PaperEntity,
    questions: list[dict[str, Any]],
    *,
    include_answer: bool,
    images: list[tuple[str, bytes]],
    image_relationships: list[str],
    layout_density: str = _DENSITY_AUTO,
) -> tuple[str, str, bool]:
    grouped: dict[str, list[dict[str, Any]]] = {qtype: [] for qtype in _TEMPLATE_TYPE_ORDER}
    for question in questions:
        question_type = question.get("type", "")
        type_value = question_type.value if hasattr(question_type, "value") else str(question_type)
        target_type = type_value if type_value in grouped else "short_answer"
        grouped[target_type].append(question)

    overall_density = layout_density if layout_density != _DENSITY_AUTO else _template_overall_density(grouped)
    is_compact = overall_density in {_DENSITY_COMPACT, _DENSITY_DENSE}
    left_parts = [
        _paragraph(
            f"科目：{paper.subject}    考试时长：{paper.duration}分钟    满分：{paper.totalMarks}分",
            bold=True,
            size=22,
        ),
        _paragraph("答题说明：请将答案填写在相应位置，计算与论述题请写出必要步骤。", italic=True, size=20),
    ]
    right_parts: list[str] = []
    section_number = 0
    question_number = 1

    for question_type in _TEMPLATE_TYPE_ORDER:
        grouped_questions = grouped.get(question_type) or []
        if not grouped_questions:
            continue
        items = list(enumerate(grouped_questions, start=question_number))
        question_number += len(items)
        section_number += 1
        section_parts = left_parts if question_type in _TEMPLATE_LEFT_TYPES else right_parts
        section_density = _template_section_density(question_type, len(grouped_questions), len(questions), overall_density)
        section_parts.append(
            _paragraph(
                _template_section_heading(section_number, question_type, items),
                bold=True,
                size=20 if section_density == _DENSITY_DENSE else 22 if section_density == _DENSITY_COMPACT else 24,
                spacing_after=0 if section_density != _DENSITY_NORMAL else None,
            )
        )
        section_parts.extend(
            _question_paragraphs_for_items(
                items,
                include_answer=include_answer,
                images=images,
                image_relationships=image_relationships,
                localized=True,
                density=section_density,
            )
        )

    if len(left_parts) == 2:
        left_parts.append(_paragraph("本栏暂无题目。", italic=True, size=20))
    if not right_parts:
        right_parts.append(_paragraph("本栏暂无题目。", italic=True, size=20))
    return "".join(left_parts), "".join(right_parts), is_compact


def _template_overall_density(grouped: dict[str, list[dict[str, Any]]]) -> str:
    total_questions = sum(len(items) for items in grouped.values())
    objective_questions = sum(len(grouped.get(question_type, [])) for question_type in _TEMPLATE_OBJECTIVE_TYPES)
    if total_questions >= _TEMPLATE_DENSE_TOTAL_THRESHOLD or objective_questions >= _TEMPLATE_DENSE_OBJECTIVE_THRESHOLD:
        return _DENSITY_DENSE
    if total_questions >= _TEMPLATE_COMPACT_TOTAL_THRESHOLD or objective_questions >= _TEMPLATE_COMPACT_OBJECTIVE_THRESHOLD:
        return _DENSITY_COMPACT
    return _DENSITY_NORMAL


def _template_section_density(question_type: str, section_count: int, total_questions: int, overall_density: str) -> str:
    if overall_density == _DENSITY_NORMAL:
        return _DENSITY_NORMAL
    if question_type in _TEMPLATE_OBJECTIVE_TYPES:
        if section_count >= _TEMPLATE_DENSE_OBJECTIVE_THRESHOLD or total_questions >= _TEMPLATE_DENSE_TOTAL_THRESHOLD:
            return _DENSITY_DENSE
        return _DENSITY_COMPACT
    if question_type == "blank" and (section_count >= 5 or overall_density == _DENSITY_DENSE):
        return _DENSITY_COMPACT
    if question_type in {"short_answer", "essay"} and section_count >= 6:
        return _DENSITY_COMPACT
    return _DENSITY_NORMAL


def _template_section_heading(
    section_number: int,
    question_type: str,
    items: list[tuple[int, dict[str, Any]]],
) -> str:
    chinese_numbers = "一二三四五六七八九十"
    number_text = chinese_numbers[section_number - 1] if section_number <= len(chinese_numbers) else str(section_number)
    details = [f"共{len(items)}题"]
    marks = [item.get("marks") for _, item in items]
    if marks and all(isinstance(mark, int) and mark > 0 for mark in marks):
        details.append(f"共{sum(marks)}分")
    return f"{number_text}、{_TEMPLATE_TYPE_LABELS.get(question_type, '其他题')}（{'，'.join(details)}）"


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
        f"{_QUESTION_SECTION_XML}"
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
    page_cells: list[tuple[str, str]],
    images: list[tuple[str, bytes]],
    image_relationships: list[str],
    compact_layout: bool = False,
) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(template_path, "r") as template_archive:
        document_xml = template_archive.read("word/document.xml").decode("utf-8")
        document_xml = _replace_template_title(document_xml, paper_title)
        document_xml = _ensure_document_namespaces(document_xml)
        document_xml = _populate_template_question_pages(
            document_xml,
            page_cells,
            compact_layout=compact_layout,
        )

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
    if _TEMPLATE_TITLE_TEXT in document_xml:
        return document_xml.replace(f"<w:t>{escape(_TEMPLATE_TITLE_TEXT)}</w:t>", f"<w:t>{escape(paper_title)}</w:t>", 1)
    return _replace_chinese_template_title(document_xml, paper_title)


def _replace_chinese_template_title(document_xml: str, paper_title: str) -> str:
    marker = "学年第"
    marker_pos = document_xml.find(marker)
    if marker_pos == -1:
        return document_xml
    para_start = document_xml.rfind("<w:p", 0, marker_pos)
    if para_start == -1:
        return document_xml
    pict_end_pos = document_xml.find("</w:pict>", para_start)
    if pict_end_pos == -1:
        return document_xml
    search_start = pict_end_pos + len("</w:pict>")
    para_end_pos = document_xml.find("</w:p>", search_start)
    if para_end_pos == -1:
        return document_xml
    para_end_pos += len("</w:p>")
    para_xml = document_xml[para_start:para_end_pos]
    gt_pos = para_xml.find(">")
    para_opening = para_xml[:gt_pos + 1]
    para_content = para_xml[gt_pos + 1:-len("</w:p>")]
    title_runs_pattern = re.compile(
        r'<w:r\b[^>]*>(?:(?!</w:r>).)*?<w:sz w:val="36"/>(?:(?!</w:r>).)*?</w:r>',
        re.DOTALL,
    )
    title_runs = title_runs_pattern.findall(para_content)
    if not title_runs:
        return document_xml
    new_run = (
        "<w:r>"
        "<w:rPr><w:b/><w:bCs/><w:sz w:val=\"36\"/></w:rPr>"
        f"<w:t>{escape(paper_title)}</w:t>"
        "</w:r>"
    )
    new_content = para_content.replace(title_runs[0], new_run)
    for run in title_runs[1:]:
        new_content = new_content.replace(run, "", 1)
    new_paragraph = f"{para_opening}{new_content}</w:p>"
    return document_xml[:para_start] + new_paragraph + document_xml[para_end_pos:]


def _ensure_document_namespaces(document_xml: str) -> str:
    namespace_attrs = {
        "xmlns:a": 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"',
        "xmlns:pic": 'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"',
    }
    document_tag_match = re.search(r"<w:document\b[^>]*>", document_xml)
    if document_tag_match is None:
        return document_xml

    document_tag = document_tag_match.group(0)
    additions = [
        attribute
        for prefix, attribute in namespace_attrs.items()
        if re.search(rf"\\s{re.escape(prefix)}\\s*=", document_tag) is None
    ]
    if not additions:
        return document_xml

    updated_tag = document_tag[:-1] + " " + " ".join(additions) + ">"
    return document_xml[:document_tag_match.start()] + updated_tag + document_xml[document_tag_match.end():]


def _populate_template_question_pages(
    document_xml: str,
    page_cells: list[tuple[str, str]],
    *,
    compact_layout: bool = False,
) -> str:
    body_start = document_xml.find("<w:body>")
    body_end = document_xml.find("</w:body>", body_start)
    if min(body_start, body_end) == -1:
        return document_xml
    body_content_start = body_start + len("<w:body>")
    section_start = document_xml.rfind("<w:sectPr", body_content_start, body_end)
    if section_start == -1:
        section_start = body_end

    page_template_xml = document_xml[body_content_start:section_start]
    table_start = page_template_xml.find("<w:tbl")
    table_end = page_template_xml.find("</w:tbl>", table_start)
    if min(table_start, table_end) == -1:
        return document_xml
    table_end += len("</w:tbl>")
    table_xml = page_template_xml[table_start:table_end]

    populated_pages = []
    for index, (left_cell_xml, right_cell_xml) in enumerate(page_cells or [("", "")]):
        populated_table = _populate_template_table(
            table_xml,
            left_cell_xml,
            right_cell_xml,
            compact_layout=compact_layout,
        )
        populated_pages.append(page_template_xml[:table_start] + populated_table + page_template_xml[table_end:])
    return document_xml[:body_content_start] + "".join(populated_pages) + document_xml[section_start:]


def _populate_template_table(
    table_xml: str,
    left_cell_xml: str,
    right_cell_xml: str,
    *,
    compact_layout: bool = False,
) -> str:
    first_row_start = table_xml.find("<w:tr")
    first_row_end = table_xml.find("</w:tr>", first_row_start)
    if min(first_row_start, first_row_end) == -1:
        return table_xml
    first_row_end += len("</w:tr>")

    row_xml = table_xml[first_row_start:first_row_end]
    cell_matches = list(re.finditer(r"<w:tc\b[^>]*>.*?</w:tc>", row_xml, re.DOTALL))
    if len(cell_matches) < 2:
        return table_xml

    replacements = (left_cell_xml, right_cell_xml)
    for match, replacement in reversed(list(zip(cell_matches[:2], replacements))):
        cell_xml = match.group(0)
        properties_end = cell_xml.find("</w:tcPr>")
        if properties_end == -1:
            continue
        properties_end += len("</w:tcPr>")
        updated_cell = cell_xml[:properties_end] + replacement + "</w:tc>"
        row_xml = row_xml[:match.start()] + updated_cell + row_xml[match.end():]

    row_xml = row_xml.replace("<w:cantSplit/>", "", 1)
    if compact_layout:
        row_xml = re.sub(r"<w:trHeight\b[^>]*/>", "", row_xml, count=1)
    return table_xml[:first_row_start] + row_xml + table_xml[first_row_end:]


def _append_document_relationships(relationships_xml: str, image_relationships: list[str]) -> str:
    if not image_relationships:
        return relationships_xml
    return relationships_xml.replace("</Relationships>", f"{''.join(image_relationships)}</Relationships>", 1)


def _ensure_png_content_type(content_types_xml: str, images: list[tuple[str, bytes]]) -> str:
    if not images or 'Extension="png"' in content_types_xml:
        return content_types_xml
    return content_types_xml.replace("</Types>", f"{_PNG_CONTENT_TYPE}</Types>", 1)


def _paragraph(
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


def _essay_answer_space(blank_space: Any, *, scale: float = 1.0) -> str:
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


def _paragraph_with_latex(
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

    image_path = IMAGE_UPLOAD_DIR / filename
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
