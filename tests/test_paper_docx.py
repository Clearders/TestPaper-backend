from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tarfile
import zipfile
import xml.etree.ElementTree as ET
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

from testpaper_backend.documents.paper_docx import build_paper_docx


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


def test_default_template_with_image_has_bound_drawing_namespaces() -> None:
    png = (
        "data:image/png;base64,"
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
    )
    docx = build_paper_docx(
        _paper(),
        [
            {
                "type": "short_answer",
                "text": "Inspect the image.",
                "marks": 10,
                "images": [{"url": png, "caption": "Figure 1"}],
            }
        ],
        include_answer=False,
    )

    document_xml = _document_xml(docx)

    root = ET.fromstring(document_xml)
    namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    body = root.find("w:body", namespace)
    assert body is not None
    body_children = list(body)
    assert body_children[-1].tag == f"{{{namespace['w']}}}sectPr"
    assert body_children[-1].find("w:pgSz", namespace).get(f"{{{namespace['w']}}}orient") is None
    assert body_children[-1].find("w:cols", namespace) is None
    direct_paragraph_text = ["".join(paragraph.itertext()) for paragraph in body.findall("w:p", namespace)]
    assert any(text.endswith("Essay Export") for text in direct_paragraph_text)
    assert any("Inspect the image." in text for text in direct_paragraph_text)
    assert 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"' in document_xml
    assert 'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"' in document_xml


def test_built_distributions_include_chinese_template_and_packaged_export(tmp_path: Path) -> None:
    project_root = Path(__file__).resolve().parents[1]
    build_root = tmp_path / "project"
    build_root.mkdir()
    shutil.copy2(project_root / "pyproject.toml", build_root / "pyproject.toml")
    shutil.copytree(project_root / "testpaper_backend", build_root / "testpaper_backend")

    dist_dir = tmp_path / "dist"
    build_script = (
        "from setuptools.build_meta import build_sdist, build_wheel; "
        f"build_sdist({str(dist_dir)!r}); build_wheel({str(dist_dir)!r})"
    )
    subprocess.run([sys.executable, "-c", build_script], cwd=build_root, check=True)

    template_member = "testpaper_backend/documents/试卷模板.docx"
    wheel_path = next(dist_dir.glob("*.whl"))
    with zipfile.ZipFile(wheel_path) as wheel:
        assert template_member in wheel.namelist()
        install_root = tmp_path / "install"
        wheel.extractall(install_root)

    sdist_path = next(dist_dir.glob("*.tar.gz"))
    with tarfile.open(sdist_path, "r:gz") as sdist:
        assert any(member.name.endswith(f"/{template_member}") for member in sdist.getmembers())

    export_script = """
from io import BytesIO
import os
from pathlib import Path
from types import SimpleNamespace
import zipfile

import testpaper_backend.documents.paper_docx as paper_docx

assert paper_docx.DEFAULT_TEMPLATE_PATH.is_file(), paper_docx.DEFAULT_TEMPLATE_PATH
assert Path(paper_docx.__file__).resolve().is_relative_to(Path(os.environ["PACKAGED_INSTALL_ROOT"]).resolve())
paper = SimpleNamespace(
    id=1,
    title="Packaged Export",
    subject="Writing",
    duration=60,
    totalMarks=100,
    questions=[],
)
output = paper_docx.build_paper_docx(paper, [], include_answer=False)
with zipfile.ZipFile(BytesIO(output)) as archive:
    document_xml = archive.read("word/document.xml").decode("utf-8")
assert "Packaged Export" in document_xml
"""
    environment = os.environ.copy()
    environment["PACKAGED_INSTALL_ROOT"] = str(install_root)
    environment["PYTHONPATH"] = str(install_root)
    environment["PYTHONUTF8"] = "1"
    subprocess.run(
        [sys.executable, "-c", export_script],
        cwd=tmp_path,
        env=environment,
        check=True,
    )
