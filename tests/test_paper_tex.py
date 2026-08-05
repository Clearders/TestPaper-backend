from __future__ import annotations

from types import SimpleNamespace

from testpaper_backend.documents.paper_tex import build_paper_tex, tex_filename


def _paper() -> SimpleNamespace:
    return SimpleNamespace(title="Math & Logic", subject="Math_101", duration=60, totalMarks=100)


def test_tex_export_preserves_math_and_escapes_plain_text() -> None:
    output = build_paper_tex(
        _paper(),
        [
            {
                "type": "single_choice",
                "text": r"If $x_1 < 5$, choose 50% & explain.",
                "marks": 5,
                "options": [r"$x_1=1$", "A_B"],
                "answer": "A",
            }
        ],
        include_answer=True,
    ).decode("utf-8")

    assert r"\documentclass[UTF8,12pt]{ctexart}" in output
    assert r"Math \& Logic" in output
    assert r"Math\_101" in output
    assert r"$x_1 < 5$" in output
    assert r"50\% \& explain" in output
    assert r"A\_B" in output
    assert r"\textbf{Answer:} A" in output


def test_tex_export_hides_answers_and_adds_essay_space() -> None:
    output = build_paper_tex(
        _paper(),
        [{"type": "essay", "text": "Explain.", "answer": "Secret", "essayBlankSpace": {"lines": 4}}],
        include_answer=False,
    ).decode("utf-8")

    assert "Secret" not in output
    assert r"\vspace{2.20cm}" in output


def test_tex_filename_is_sanitized() -> None:
    assert tex_filename("A/B: Test?") == "A B Test.tex"
