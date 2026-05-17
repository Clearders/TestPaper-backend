from __future__ import annotations

import importlib
import sys
import zipfile
from io import BytesIO
from pathlib import Path
from types import ModuleType, SimpleNamespace

schemas_stub = ModuleType("schemas")
schemas_stub.PaperEntity = object
sys.modules.setdefault("schemas", schemas_stub)

build_paper_docx = importlib.import_module("paper_docx").build_paper_docx


def _document_xml(docx_bytes: bytes) -> str:
    with zipfile.ZipFile(BytesIO(docx_bytes), "r") as archive:
        return archive.read("word/document.xml").decode("utf-8")


def _paper() -> SimpleNamespace:
    return SimpleNamespace(
        id=1,
        title="Essay Export",
        subject="Writing",
        duration=60,
        totalMarks=100,
        questions=[],
    )


def test_essay_blank_space_uses_configured_exact_height() -> None:
    docx = build_paper_docx(
        _paper(),
        [
            {
                "type": "essay",
                "text": "Explain the result.",
                "marks": 10,
                "essayBlankSpace": {"lines": 3, "lineHeight": 40},
            }
        ],
        include_answer=False,
        template_path=Path("missing-template.docx"),
    )

    document_xml = _document_xml(docx)

    assert 'w:line="1800"' in document_xml
    assert 'w:lineRule="exact"' in document_xml
    assert "_" * 78 not in document_xml


def test_essay_blank_space_defaults_when_missing() -> None:
    docx = build_paper_docx(
        _paper(),
        [
            {
                "type": "essay",
                "text": "Explain the default.",
                "marks": 10,
            }
        ],
        include_answer=False,
        template_path=Path("missing-template.docx"),
    )

    document_xml = _document_xml(docx)

    assert 'w:line="2520"' in document_xml
