from __future__ import annotations

import base64
import binascii
import re
import struct
import zipfile
from io import BytesIO
from typing import Any
from xml.sax.saxutils import escape

from schemas import PaperEntity


DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_DOCX_NS = (
    'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
    'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
    'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"'
)
_EMU_PER_INCH = 914400
_PX_PER_INCH = 96
_MAX_IMAGE_WIDTH_EMU = int(5.8 * _EMU_PER_INCH)


def docx_filename(title: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|]+', " ", title).strip()
    cleaned = re.sub(r"\s+", " ", cleaned) or "examination-paper"
    return f"{cleaned[:80]}.docx"


def build_paper_docx(
    paper: PaperEntity,
    questions: list[dict[str, Any]],
    *,
    include_answer: bool,
) -> bytes:
    images: list[tuple[str, bytes]] = []
    image_relationships: list[str] = []
    paragraphs: list[str] = [
        _paragraph(paper.title, bold=True, size=32, align="center"),
        _paragraph(f"Subject: {paper.subject}", size=22, align="center"),
        _paragraph(f"Duration: {paper.duration} minutes    Total Marks: {paper.totalMarks}", size=22, align="center"),
        _paragraph(""),
    ]

    for index, question in enumerate(questions, start=1):
        marks_text = f" ({question['marks']} marks)" if question.get("marks") else ""
        paragraphs.append(_paragraph(f"{index}. {question.get('text', '')}{marks_text}", bold=True, size=23))

        options = question.get("options") or []
        if options:
            for option_index, option in enumerate(options):
                label = chr(65 + option_index)
                paragraphs.append(_paragraph(f"    {label}. {option}", size=22))

        for image in question.get("images") or []:
            relationship_id = _add_image_relationship(image.get("url", ""), images, image_relationships)
            if relationship_id:
                paragraphs.append(_image_paragraph(relationship_id, len(images), images[-1][1]))
                if image.get("caption"):
                    paragraphs.append(_paragraph(str(image["caption"]), italic=True, size=18, align="center"))

        if question.get("type") == "essay":
            blank_space = question.get("essayBlankSpace") or {}
            line_count = max(1, min(20, int(blank_space.get("lines") or 6)))
            for _ in range(line_count):
                paragraphs.append(_paragraph("_" * 78, size=20))

        if include_answer and "answer" in question:
            paragraphs.append(_paragraph(f"Answer: {question.get('answer', '')}", italic=True, size=21))

        paragraphs.append(_paragraph(""))

    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f"<w:document {_DOCX_NS}>"
        "<w:body>"
        f"{''.join(paragraphs)}"
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


def _paragraph(text: str, *, bold: bool = False, italic: bool = False, size: int | None = None, align: str | None = None) -> str:
    props = []
    if align:
        props.append(f'<w:jc w:val="{align}"/>')
    run_props = []
    if bold:
        run_props.append("<w:b/>")
    if italic:
        run_props.append("<w:i/>")
    if size is not None:
        run_props.append(f'<w:sz w:val="{size}"/>')

    escaped_lines = escape(text).splitlines() or [""]
    text_runs = []
    for index, line in enumerate(escaped_lines):
        if index:
            text_runs.append("<w:br/>")
        text_runs.append(f'<w:t xml:space="preserve">{line}</w:t>')

    paragraph_props = f"<w:pPr>{''.join(props)}</w:pPr>" if props else ""
    run_props_xml = f"<w:rPr>{''.join(run_props)}</w:rPr>" if run_props else ""
    return f"<w:p>{paragraph_props}<w:r>{run_props_xml}{''.join(text_runs)}</w:r></w:p>"


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
    if not url.startswith(prefix):
        return None

    try:
        image_bytes = base64.b64decode(url[len(prefix):], validate=True)
    except (binascii.Error, ValueError):
        return None

    images.append((url, image_bytes))
    relationship_id = f"rIdImage{len(images)}"
    relationships.append(
        f'<Relationship Id="{relationship_id}" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
        f'Target="media/image{len(images)}.png"/>'
    )
    return relationship_id


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
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )


def _package_relationships_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        "</Relationships>"
    )


def _document_relationships_xml(image_relationships: list[str]) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f"{''.join(image_relationships)}"
        "</Relationships>"
    )
