from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status

from testpaper_backend.repositories import PAPERS, QUESTIONS, normalize_question_type
from testpaper_backend.schemas import PaperEntity, QuestionOrder, QuestionRef, QuestionType
from testpaper_backend.services.questions import question_to_dict


def get_paper_or_404(paper_public_id: str) -> PaperEntity:
    paper = PAPERS.get_by_public_id(paper_public_id)
    if paper is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "PAPER_NOT_FOUND", "message": "Paper not found"},
        )
    return paper


def validate_unique_question_refs(items: list[QuestionRef], message_prefix: str) -> None:
    question_ids = [item.questionPublicId for item in items]
    order_nos = [item.orderNo for item in items]
    if len(question_ids) != len(set(question_ids)):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "VALIDATION_ERROR", "message": f"{message_prefix} must not contain duplicate question IDs"},
        )
    if len(order_nos) != len(set(order_nos)):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "VALIDATION_ERROR", "message": f"{message_prefix} must not contain duplicate order numbers"},
        )


def paper_with_questions(paper: PaperEntity, include_answer: bool = True) -> dict[str, Any]:
    normalized = paper.model_dump(mode="json")
    normalized["questions"] = sorted(normalized["questions"], key=lambda item: item["orderNo"])
    resolved_questions = []
    for item in normalized["questions"]:
        question = QUESTIONS.get_by_public_id(item["questionPublicId"])
        if question is None:
            continue
        resolved_questions.append({
            **question_to_dict(question, include_answer=include_answer),
            "orderNo": item["orderNo"],
            "marks": item.get("marks"),
        })
    normalized["questions"] = resolved_questions
    return normalized


def build_export_questions(paper: PaperEntity, question_order: QuestionOrder, include_answer: bool) -> list[dict[str, Any]]:
    ordered_questions = paper_with_questions(paper, include_answer=include_answer)["questions"]
    if question_order == QuestionOrder.paper:
        return ordered_questions

    grouped: dict[QuestionType, list[dict[str, Any]]] = {
        QuestionType.single_choice: [],
        QuestionType.multiple_choice: [],
        QuestionType.true_false: [],
        QuestionType.blank: [],
        QuestionType.short_answer: [],
        QuestionType.essay: [],
    }
    for question in ordered_questions:
        grouped[normalize_question_type(question["type"])].append(question)
    flattened: list[dict[str, Any]] = []
    question_types = (
        QuestionType.single_choice,
        QuestionType.multiple_choice,
        QuestionType.true_false,
        QuestionType.blank,
        QuestionType.short_answer,
        QuestionType.essay,
    )
    for qtype in question_types:
        flattened.extend(grouped[qtype])
    return flattened
