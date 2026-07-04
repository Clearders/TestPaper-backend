# ruff: noqa: RUF001
from __future__ import annotations

import re
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any, NamedTuple
from xml.sax.saxutils import escape

from testpaper_backend.documents.docx_assets import (
    DocxAssetStore,
    append_document_relationships,
    content_types_xml,
    document_relationships_xml,
    ensure_png_content_type,
    image_paragraph,
    package_relationships_xml,
)
from testpaper_backend.documents.docx_runs import essay_answer_space, paragraph, paragraph_with_latex
from testpaper_backend.schemas import PaperEntity

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
_TEMPLATE_TITLE_TEXT = "2020-2021 Academic Year First Semester Final Exam Paper"
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
_TEMPLATE_INSTRUCTIONS = "答题说明：请将答案填写在相应位置，计算与论述题请写出必要步骤。"
_TEMPLATE_EMPTY_COLUMN_TEXT = "本栏暂无题目。"
_QUESTION_SECTION_XML = (
    '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
    '<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" '
    'w:header="720" w:footer="720" w:gutter="0"/>'
    "</w:sectPr>"
)


class _TemplateSection(NamedTuple):
    number: int
    question_type: str
    items: list[tuple[int, dict[str, Any]]]
    density: str


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
    assets = DocxAssetStore()
    if use_template:
        page_cells, compact_layout = _template_question_pages(
            paper,
            questions,
            include_answer=include_answer,
            assets=assets,
            layout_density=density,
        )
        return _build_docx_from_template(
            template,
            paper.title,
            page_cells,
            assets,
            compact_layout=compact_layout,
        )

    paragraphs: list[str] = []
    paragraphs.append(paragraph(paper.title, bold=True, size=32, align="center"))
    paragraphs.extend(
        [
            paragraph(
                f"Subject: {paper.subject}    Duration: {paper.duration} minutes    Total Marks: {paper.totalMarks}",
                size=22,
                align="center",
            ),
            paragraph(""),
        ]
    )

    paragraphs.extend(
        _question_paragraphs(
            questions,
            include_answer=include_answer,
            assets=assets,
            density=density,
        )
    )
    document_body_xml = "".join(paragraphs)
    return _build_standalone_docx(document_body_xml, assets)


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
    assets: DocxAssetStore,
    density: str = _DENSITY_NORMAL,
) -> list[str]:
    return _question_paragraphs_for_items(
        list(enumerate(questions, start=1)),
        include_answer=include_answer,
        assets=assets,
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
    assets: DocxAssetStore,
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
            paragraph_with_latex(
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
            embedded_image = assets.add_image(image.get("url", ""))
            if embedded_image:
                paragraphs.append(image_paragraph(embedded_image))
                if image.get("caption"):
                    paragraphs.append(paragraph(str(image["caption"]), italic=True, size=18, align="center"))

        if question.get("type") == "essay":
            blank_scale = {_DENSITY_NORMAL: 1.0, _DENSITY_COMPACT: 0.75, _DENSITY_DENSE: 0.55}[density]
            paragraphs.append(essay_answer_space(question.get("essayBlankSpace"), scale=blank_scale))

        if include_answer and "answer" in question:
            ans = question.get("answer", "")
            if isinstance(ans, list):
                ans = ", ".join(ans)
            answer_label = "答案：" if localized else "Answer: "
            paragraphs.append(
                paragraph_with_latex(
                    f"{answer_label}{ans}",
                    italic=True,
                    size=answer_size,
                    spacing_after=0 if is_compact else None,
                    line=line_height,
                )
            )

        if not is_compact:
            paragraphs.append(paragraph(""))

    return paragraphs


def _option_paragraphs(options: list[Any], *, density: str, size: int, line: int | None) -> list[str]:
    option_texts = [f"{chr(65 + index)}. {option}" for index, option in enumerate(options)]
    if density == _DENSITY_DENSE:
        return [paragraph_with_latex("    " + "    ".join(option_texts), size=size, spacing_after=0, line=line)]
    if density == _DENSITY_COMPACT:
        return [
            paragraph_with_latex(
                "    " + "    ".join(option_texts[index : index + 2]),
                size=size,
                spacing_after=0,
                line=line,
            )
            for index in range(0, len(option_texts), 2)
        ]
    return [paragraph_with_latex(f"    {option}", size=size) for option in option_texts]


def _template_question_pages(
    paper: PaperEntity,
    questions: list[dict[str, Any]],
    *,
    include_answer: bool,
    assets: DocxAssetStore,
    layout_density: str = _DENSITY_AUTO,
) -> tuple[list[tuple[str, str]], bool]:
    grouped = _group_template_questions(questions)
    overall_density = layout_density if layout_density != _DENSITY_AUTO else _template_overall_density(grouped)
    if overall_density == _DENSITY_NORMAL:
        left_cell_xml, right_cell_xml, compact_layout = _template_question_cells(
            paper,
            questions,
            include_answer=include_answer,
            assets=assets,
            layout_density=overall_density,
        )
        return [(left_cell_xml, right_cell_xml)], compact_layout

    columns: list[list[str]] = [_template_intro_paragraphs(paper, compact=True)]
    column_units = [_template_intro_units()]
    capacity = _template_column_capacity(overall_density)

    def push_block(block_xml: str, block_units: float) -> None:
        if column_units[-1] > 0 and column_units[-1] + block_units > capacity:
            columns.append([])
            column_units.append(0.0)
        columns[-1].append(block_xml)
        column_units[-1] += block_units

    for section in _template_sections(grouped, total_questions=len(questions), overall_density=overall_density):
        heading_xml = paragraph(
            _template_section_heading(section.number, section.question_type, section.items),
            bold=True,
            size=20 if section.density == _DENSITY_DENSE else 22,
            spacing_after=0,
        )
        heading_units = _template_heading_units(section.density)

        for item_index, item in enumerate(section.items):
            question_xml = "".join(
                _question_paragraphs_for_items(
                    [item],
                    include_answer=include_answer,
                    assets=assets,
                    localized=True,
                    density=section.density,
                )
            )
            block_xml = f"{heading_xml}{question_xml}" if item_index == 0 else question_xml
            block_units = _template_question_units(item[1], include_answer=include_answer, density=section.density)
            if item_index == 0:
                block_units += heading_units
            push_block(block_xml, block_units)

    if len(columns) == 1 and len(columns[0]) == 2:
        columns[0].append(_template_empty_column_paragraph())

    page_cells: list[tuple[str, str]] = []
    for index in range(0, len(columns), 2):
        left_xml = "".join(columns[index])
        right_xml = "".join(columns[index + 1]) if index + 1 < len(columns) else _template_empty_column_paragraph()
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


def _template_sections(
    grouped: dict[str, list[dict[str, Any]]],
    *,
    total_questions: int,
    overall_density: str,
) -> list[_TemplateSection]:
    sections: list[_TemplateSection] = []
    section_number = 0
    question_number = 1
    for question_type in _TEMPLATE_TYPE_ORDER:
        grouped_questions = grouped.get(question_type) or []
        if not grouped_questions:
            continue
        items = list(enumerate(grouped_questions, start=question_number))
        question_number += len(items)
        section_number += 1
        sections.append(
            _TemplateSection(
                number=section_number,
                question_type=question_type,
                items=items,
                density=_template_section_density(question_type, len(grouped_questions), total_questions, overall_density),
            )
        )
    return sections


def _template_intro_paragraphs(paper: PaperEntity, *, compact: bool) -> list[str]:
    return [
        paragraph(
            f"科目：{paper.subject}    考试时长：{paper.duration}分钟    满分：{paper.totalMarks}分",
            bold=True,
            size=20 if compact else 22,
            spacing_after=0 if compact else None,
        ),
        paragraph(
            _TEMPLATE_INSTRUCTIONS,
            italic=True,
            size=18 if compact else 20,
            spacing_after=0 if compact else None,
        ),
    ]


def _template_empty_column_paragraph() -> str:
    return paragraph(_TEMPLATE_EMPTY_COLUMN_TEXT, italic=True, size=20)


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
    assets: DocxAssetStore,
    layout_density: str = _DENSITY_AUTO,
) -> tuple[str, str, bool]:
    grouped = _group_template_questions(questions)
    overall_density = layout_density if layout_density != _DENSITY_AUTO else _template_overall_density(grouped)
    is_compact = overall_density in {_DENSITY_COMPACT, _DENSITY_DENSE}
    left_parts = _template_intro_paragraphs(paper, compact=False)
    right_parts: list[str] = []

    for section in _template_sections(grouped, total_questions=len(questions), overall_density=overall_density):
        section_parts = left_parts if section.question_type in _TEMPLATE_LEFT_TYPES else right_parts
        section_parts.append(
            paragraph(
                _template_section_heading(section.number, section.question_type, section.items),
                bold=True,
                size=20 if section.density == _DENSITY_DENSE else 22 if section.density == _DENSITY_COMPACT else 24,
                spacing_after=0 if section.density != _DENSITY_NORMAL else None,
            )
        )
        section_parts.extend(
            _question_paragraphs_for_items(
                section.items,
                include_answer=include_answer,
                assets=assets,
                localized=True,
                density=section.density,
            )
        )

    if len(left_parts) == 2:
        left_parts.append(_template_empty_column_paragraph())
    if not right_parts:
        right_parts.append(_template_empty_column_paragraph())
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
    assets: DocxAssetStore,
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
        archive.writestr("[Content_Types].xml", content_types_xml(assets))
        archive.writestr("_rels/.rels", package_relationships_xml())
        archive.writestr("word/document.xml", document_xml)
        archive.writestr("word/_rels/document.xml.rels", document_relationships_xml(assets))
        assets.write_media(archive)

    return buffer.getvalue()


def _build_docx_from_template(
    template_path: Path,
    paper_title: str,
    page_cells: list[tuple[str, str]],
    assets: DocxAssetStore,
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
        relationships_xml = append_document_relationships(relationships_xml, assets)

        content_types_xml = template_archive.read("[Content_Types].xml").decode("utf-8")
        content_types_xml = ensure_png_content_type(content_types_xml, assets)

        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as output_archive:
            for entry in template_archive.infolist():
                if entry.filename in {"word/document.xml", "word/_rels/document.xml.rels", "[Content_Types].xml"}:
                    continue
                output_archive.writestr(entry, template_archive.read(entry.filename))

            output_archive.writestr("[Content_Types].xml", content_types_xml)
            output_archive.writestr("word/document.xml", document_xml)
            output_archive.writestr("word/_rels/document.xml.rels", relationships_xml)
            assets.write_media(output_archive)

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
    para_opening = para_xml[: gt_pos + 1]
    para_content = para_xml[gt_pos + 1 : -len("</w:p>")]
    title_runs_pattern = re.compile(
        r'<w:r\b[^>]*>(?:(?!</w:r>).)*?<w:sz w:val="36"/>(?:(?!</w:r>).)*?</w:r>',
        re.DOTALL,
    )
    title_runs = title_runs_pattern.findall(para_content)
    if not title_runs:
        return document_xml
    new_run = f'<w:r><w:rPr><w:b/><w:bCs/><w:sz w:val="36"/></w:rPr><w:t>{escape(paper_title)}</w:t></w:r>'
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
        attribute for prefix, attribute in namespace_attrs.items() if re.search(rf"\\s{re.escape(prefix)}\\s*=", document_tag) is None
    ]
    if not additions:
        return document_xml

    updated_tag = document_tag[:-1] + " " + " ".join(additions) + ">"
    return document_xml[: document_tag_match.start()] + updated_tag + document_xml[document_tag_match.end() :]


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
    for left_cell_xml, right_cell_xml in page_cells or [("", "")]:
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
    for match, replacement in reversed(list(zip(cell_matches[:2], replacements, strict=True))):
        cell_xml = match.group(0)
        properties_end = cell_xml.find("</w:tcPr>")
        if properties_end == -1:
            continue
        properties_end += len("</w:tcPr>")
        updated_cell = cell_xml[:properties_end] + replacement + "</w:tc>"
        row_xml = row_xml[: match.start()] + updated_cell + row_xml[match.end() :]

    row_xml = row_xml.replace("<w:cantSplit/>", "", 1)
    if compact_layout:
        row_xml = re.sub(r"<w:trHeight\b[^>]*/>", "", row_xml, count=1)
    return table_xml[:first_row_start] + row_xml + table_xml[first_row_end:]
