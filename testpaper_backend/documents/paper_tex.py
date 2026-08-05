from __future__ import annotations

import re
from typing import Any
from urllib.parse import unquote, urlparse

TEX_MEDIA_TYPE = "application/x-tex"
_MATH_SEGMENT_RE = re.compile(r"(\$\$.+?\$\$|\$.+?\$)", re.DOTALL)
_TYPE_LABELS = {
    "single_choice": "Single Choice",
    "multiple_choice": "Multiple Choice",
    "true_false": "True or False",
    "blank": "Fill in the Blank",
    "short_answer": "Short Answer",
    "essay": "Essay",
}
_LATEX_ESCAPES = {
    "\\": r"\textbackslash{}",
    "{": r"\{",
    "}": r"\}",
    "#": r"\#",
    "$": r"\$",
    "%": r"\%",
    "&": r"\&",
    "_": r"\_",
    "^": r"\textasciicircum{}",
    "~": r"\textasciitilde{}",
}


def tex_filename(title: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|]+', " ", title).strip()
    cleaned = re.sub(r"\s+", " ", cleaned) or "examination-paper"
    return f"{cleaned[:80]}.tex"


def build_paper_tex(
    paper: Any,
    questions: list[dict[str, Any]],
    *,
    include_answer: bool,
) -> bytes:
    lines = [
        r"\documentclass[UTF8,12pt]{ctexart}",
        r"\usepackage[a4paper,margin=2cm]{geometry}",
        r"\usepackage{amsmath,amssymb}",
        r"\usepackage{enumitem}",
        r"\usepackage{graphicx}",
        r"\usepackage{xcolor}",
        r"\setlength{\parindent}{0pt}",
        r"\setlist[enumerate]{leftmargin=*,itemsep=0.8em}",
        r"\begin{document}",
        r"\begin{center}",
        rf"{{\LARGE\bfseries {_tex_text(str(paper.title))}}}\\[0.6em]",
        (
            rf"{_tex_text(str(paper.subject))} \quad "
            rf"Duration: {_tex_text(str(paper.duration))} minutes \quad "
            rf"Total Marks: {_tex_text(str(paper.totalMarks))}"
        ),
        r"\end{center}",
        r"\vspace{0.8em}",
    ]

    current_type: str | None = None
    enumerate_open = False
    for question in questions:
        question_type = str(question.get("type") or "")
        if question_type != current_type:
            if enumerate_open:
                lines.append(r"\end{enumerate}")
            lines.extend(
                [
                    rf"\section*{{{_tex_text(_TYPE_LABELS.get(question_type, 'Questions'))}}}",
                    r"\begin{enumerate}",
                ]
            )
            enumerate_open = True
            current_type = question_type

        marks = question.get("marks")
        mark_text = rf" \hfill \textit{{[{_tex_text(str(marks))} marks]}}" if marks else ""
        lines.append(rf"\item {_tex_text(str(question.get('text') or ''))}{mark_text}")
        options = question.get("options") or []
        if options:
            lines.append(r"\begin{enumerate}[label=\Alph*.,itemsep=0.2em]")
            lines.extend(rf"\item {_tex_text(str(option))}" for option in options)
            lines.append(r"\end{enumerate}")

        for image in question.get("images") or []:
            if not isinstance(image, dict) or not image.get("url"):
                continue
            image_name = _image_filename(str(image["url"]))
            caption = str(image.get("caption") or image_name)
            lines.extend(
                [
                    rf"% Original image URL: {str(image['url']).replace(chr(10), '')}",
                    r"\begin{center}",
                    rf"\IfFileExists{{\detokenize{{{image_name}}}}}"
                    rf"{{\includegraphics[width=0.75\linewidth]{{\detokenize{{{image_name}}}}}}}"
                    rf"{{\fbox{{\parbox{{0.7\linewidth}}{{Image: {_tex_text(caption)}}}}}}}",
                    r"\end{center}",
                ]
            )

        if include_answer:
            answer = question.get("answer", "")
            if isinstance(answer, list):
                answer = ", ".join(str(item) for item in answer)
            lines.append(rf"\par\textcolor{{blue}}{{\textbf{{Answer:}} {_tex_text(str(answer))}}}")
        elif question_type == "essay":
            blank_space = question.get("essayBlankSpace") if isinstance(question.get("essayBlankSpace"), dict) else {}
            blank_lines = _bounded_int(blank_space.get("lines"), 6, 1, 20)
            lines.append(rf"\vspace{{{blank_lines * 0.55:.2f}cm}}")

    if enumerate_open:
        lines.append(r"\end{enumerate}")
    lines.extend([r"\end{document}", ""])
    return "\n".join(lines).encode("utf-8")


def _tex_text(value: str) -> str:
    parts = _MATH_SEGMENT_RE.split(value)
    rendered = []
    for part in parts:
        if part.startswith("$") and part.endswith("$"):
            rendered.append(part)
        else:
            rendered.append("".join(_LATEX_ESCAPES.get(char, char) for char in part).replace("\n", r"\\" + "\n"))
    return "".join(rendered)


def _image_filename(url: str) -> str:
    filename = unquote(urlparse(url).path.rsplit("/", 1)[-1]).strip()
    return filename or "question-image.png"


def _bounded_int(value: Any, fallback: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = fallback
    return max(minimum, min(maximum, parsed))
