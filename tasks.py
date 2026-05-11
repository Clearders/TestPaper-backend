from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from celery import shared_task
from sqlalchemy import func

from celery_app import BaseTask
from db import QuestionRow, SessionLocal
from repositories import QUESTIONS
from schemas import QuestionType

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helper: provide a DB session for Celery workers (they run in separate
# processes so they cannot share the FastAPI sessionmaker directly).
# ---------------------------------------------------------------------------
def _worker_session():
    return SessionLocal()


# ---------------------------------------------------------------------------
# Health / ping
# ---------------------------------------------------------------------------
@shared_task(name="ping", bind=True, base=BaseTask)
def ping_task(self: BaseTask) -> str:
    """Simple health-check task."""
    return "pong"


# ---------------------------------------------------------------------------
# Question statistics (expensive aggregate queries)
# ---------------------------------------------------------------------------
@shared_task(name="compute_question_stats", bind=True, base=BaseTask)
def compute_question_stats(self: BaseTask) -> dict[str, Any]:
    """Compute aggregate question statistics (counts, breakdowns)."""
    session = _worker_session()
    try:
        total = session.query(func.count(QuestionRow.id)).scalar() or 0
        type_counts = Counter(dict(session.query(QuestionRow.type, func.count(QuestionRow.id)).group_by(QuestionRow.type).all()))
        difficulty_counts = Counter(dict(session.query(QuestionRow.difficulty, func.count(QuestionRow.id)).group_by(QuestionRow.difficulty).all()))
        subject_counts = Counter(dict(session.query(QuestionRow.subject, func.count(QuestionRow.id)).group_by(QuestionRow.subject).all()))
        latex_count = session.query(func.count(QuestionRow.id)).filter(QuestionRow.has_latex.is_(True)).scalar() or 0
        tag_counter = Counter(
            str(tag).strip().lower()
            for tags in session.query(QuestionRow.tags)
            for tag in (tags[0] or [])
            if tag is not None
        )

        return {
            "total": total,
            "byType": dict(type_counts),
            "byDifficulty": dict(difficulty_counts),
            "bySubject": dict(subject_counts),
            "withLatex": latex_count,
            "topTags": tag_counter.most_common(20),
            "computedAt": datetime.now(timezone.utc).isoformat(),
        }
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Paper export (heavy processing)
# ---------------------------------------------------------------------------
@shared_task(name="export_paper", bind=True, base=BaseTask)
def export_paper_task(
    self: BaseTask,
    paper_id: int,
    question_order: str = "paper",
    include_answer: bool = True,
    format: str = "json",
) -> dict[str, Any]:
    """Asynchronously export a paper to the requested format.

    Worker return value is stored in Redis via the Celery result backend,
    so callers can poll for completion.
    """
    from repositories import PAPERS

    paper = PAPERS.get(paper_id)
    if paper is None:
        raise ValueError(f"Paper {paper_id} not found")

    # Resolve questions
    resolved = []
    for item in sorted(paper.questions, key=lambda x: x.orderNo):
        q = QUESTIONS.get(item.questionId)
        if q is None:
            continue
        entry = q.model_dump(mode="json")
        if not include_answer:
            entry.pop("answer", None)
        entry["orderNo"] = item.orderNo
        entry["marks"] = item.marks
        resolved.append(entry)

    # Reorder if categorized
    if question_order == "categorized":
        grouped: dict[str, list[dict[str, Any]]] = {
            t.value: [] for t in QuestionType
        }
        for entry in resolved:
            grouped[entry["type"]].append(entry)
        resolved = []
        for t in (QuestionType.choice, QuestionType.true_false, QuestionType.blank, QuestionType.short_answer, QuestionType.essay):
            resolved.extend(grouped[t.value])

    result: dict[str, Any] = {
        "paper": paper.model_dump(mode="json"),
        "questions": resolved,
        "exportedAt": datetime.now(timezone.utc).isoformat(),
        "format": format,
    }

    if format == "json":
        return result
    if format == "csv":
        return _to_csv_format(result)
    if format == "txt":
        return _to_txt_format(result)

    raise ValueError(f"Unsupported export format: {format}")


def _to_csv_format(result: dict[str, Any]) -> dict[str, Any]:
    """Convert export result to CSV text."""
    import csv
    import io

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Order", "Type", "Subject", "Difficulty", "Question", "Answer", "Marks"])
    for i, q in enumerate(result["questions"], 1):
        writer.writerow([
            i, q.get("type"), q.get("subject"), q.get("difficulty"),
            q.get("text"), q.get("answer", ""), q.get("marks"),
        ])
    return {"csv": buf.getvalue(), "exportedAt": result["exportedAt"]}


def _to_txt_format(result: dict[str, Any]) -> dict[str, Any]:
    """Convert export result to plain-text."""
    lines = [f"Paper: {result['paper'].get('title', 'Untitled')}"]
    lines.append(f"Subject: {result['paper'].get('subject', '')}  |  Marks: {result['paper'].get('totalMarks', 0)}")
    lines.append("=" * 60)
    for i, q in enumerate(result["questions"], 1):
        lines.append(f"\n{i}. [{q.get('difficulty', '')}] {q.get('text', '')}")
        if q.get("options"):
            for j, opt in enumerate(q["options"]):
                lines.append(f"   ({chr(65 + j)}) {opt}")
        lines.append(f"   Answer: {q.get('answer', 'N/A')}")
    return {"txt": "\n".join(lines), "exportedAt": result["exportedAt"]}


# ---------------------------------------------------------------------------
# Batch question processing via Celery workflow (chain / group)
# ---------------------------------------------------------------------------
@shared_task(name="validate_question", bind=True, base=BaseTask)
def validate_question_task(self: BaseTask, question_id: int) -> dict[str, Any]:
    """Validate a single question's data integrity."""
    return _validate_question(question_id)


def _validate_question(question_id: int) -> dict[str, Any]:
    q = QUESTIONS.get(question_id)
    if q is None:
        return {"questionId": question_id, "status": "missing", "issues": ["Question not found"]}

    issues = []
    if not q.text or len(q.text.strip()) == 0:
        issues.append("Empty question text")
    if not q.answer or len(q.answer.strip()) == 0:
        issues.append("Empty answer text")
    if q.type in (QuestionType.choice, QuestionType.true_false) and not q.options:
        issues.append("Choice/true-false question has no options")
    if q.type == QuestionType.essay and q.essayBlankSpace is None:
        issues.append("Essay question missing blank-space config")

    return {
        "questionId": question_id,
        "status": "invalid" if issues else "valid",
        "issues": issues,
    }


@shared_task(name="validate_all_questions", bind=True, base=BaseTask)
def validate_all_questions_task(self: BaseTask) -> dict[str, Any]:
    """Validate every question in one worker task without blocking on child tasks."""
    ids = QUESTIONS.keys()
    if not ids:
        return {"status": "done", "validated": 0, "results": []}

    results = [_validate_question(qid) for qid in ids]
    summary = Counter(r.get("status") for r in results)
    return {
        "status": "done",
        "total": len(results),
        "valid": summary.get("valid", 0),
        "invalid": summary.get("invalid", 0),
        "missing": summary.get("missing", 0),
        "results": results,
    }


# ---------------------------------------------------------------------------
# LaTeX rendering (placeholder — real rendering needs a LaTeX engine)
# ---------------------------------------------------------------------------
@shared_task(name="detect_latex_questions", bind=True, base=BaseTask)
def detect_latex_questions_task(self: BaseTask) -> list[int]:
    """Return IDs of all questions that contain LaTeX markup."""
    return [qid for qid, q in QUESTIONS.items() if q.hasLatex]


# ---------------------------------------------------------------------------
# Periodic / maintenance
# ---------------------------------------------------------------------------
@shared_task(name="cleanup_expired_sessions", bind=True, base=BaseTask)
def cleanup_expired_sessions_task(self: BaseTask) -> dict[str, Any]:
    """Remove expired auth tokens. Intended for periodic scheduling."""
    from datetime import datetime, timezone

    session = _worker_session()
    try:
        from db import AuthTokenRow

        now = datetime.now(timezone.utc)
        result = session.query(AuthTokenRow).filter(AuthTokenRow.expires_at <= now).delete()
        session.commit()
        return {"deleted": result, "timestamp": now.isoformat()}
    finally:
        session.close()
