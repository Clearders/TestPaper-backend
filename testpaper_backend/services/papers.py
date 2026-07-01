from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status

from testpaper_backend.repositories import PAPERS, QUESTIONS, normalize_question_type
from testpaper_backend.schemas import PaperEntity, PaperUpdate, QuestionOrder, QuestionOrderUpdate, QuestionRef, QuestionType, UserEntity
from testpaper_backend.services.ownership import can_manage_owned_resource
from testpaper_backend.services.questions import get_question_or_404, question_to_dict
from testpaper_backend.time_utils import now_utc


def get_paper_or_404(paper_public_id: str) -> PaperEntity:
    paper = PAPERS.get_by_public_id(paper_public_id)
    if paper is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "PAPER_NOT_FOUND", "message": "Paper not found"},
        )
    return paper


def ensure_paper_owner_access(paper: PaperEntity, current_user: UserEntity) -> None:
    if can_manage_owned_resource(paper.ownerId, current_user):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={"code": "FORBIDDEN", "message": "You can only modify papers you own"},
    )


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


def update_paper_metadata(paper: PaperEntity, payload: PaperUpdate) -> PaperEntity:
    data = paper.model_dump()
    data.update(payload.model_dump(exclude_unset=True))
    data["updatedAt"] = now_utc()
    updated = PaperEntity(**data)
    PAPERS[paper.id] = updated
    return updated


def add_questions_to_paper(paper: PaperEntity, question_refs: list[QuestionRef]) -> PaperEntity:
    validate_unique_question_refs(question_refs, "questions")
    existing_ids = {item.questionPublicId for item in paper.questions}
    existing_orders = {item.orderNo for item in paper.questions}
    additions = []
    for item in question_refs:
        get_question_or_404(item.questionPublicId)
        if item.questionPublicId in existing_ids:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "QUESTION_ALREADY_IN_PAPER", "message": "Question already exists in paper"},
            )
        if item.orderNo in existing_orders:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": "VALIDATION_ERROR", "message": "orderNo already exists in paper"},
            )
        additions.append(QuestionRef(**item.model_dump()))
    paper.questions.extend(additions)
    return _save_ordered_paper(paper)


def remove_question_from_paper(paper: PaperEntity, question_public_id: str) -> PaperEntity:
    before = len(paper.questions)
    paper.questions = [item for item in paper.questions if item.questionPublicId != question_public_id]
    if len(paper.questions) == before:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "QUESTION_NOT_FOUND", "message": "Question not found in paper"},
        )
    return _save_ordered_paper(paper)


def reorder_paper_question_refs(paper: PaperEntity, payload: QuestionOrderUpdate) -> PaperEntity:
    validate_unique_question_refs(
        [QuestionRef(questionPublicId=item.questionPublicId, orderNo=item.orderNo) for item in payload.orders],
        "orders",
    )
    order_map = {item.questionPublicId: item.orderNo for item in payload.orders}
    existing_ids = {item.questionPublicId for item in paper.questions}
    if set(order_map) != existing_ids:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "VALIDATION_ERROR", "message": "orders must include every question in the paper"},
        )
    paper.questions = [
        QuestionRef(questionPublicId=item.questionPublicId, orderNo=order_map[item.questionPublicId], marks=item.marks)
        for item in paper.questions
    ]
    return _save_ordered_paper(paper)


def replace_paper_question_refs(paper: PaperEntity, question_refs: list[QuestionRef]) -> PaperEntity:
    validate_unique_question_refs(question_refs, "questions")
    for item in question_refs:
        get_question_or_404(item.questionPublicId)
    paper.questions = [QuestionRef(**item.model_dump()) for item in question_refs]
    return _save_ordered_paper(paper)


def _save_ordered_paper(paper: PaperEntity) -> PaperEntity:
    paper.questions = sorted(paper.questions, key=lambda item: item.orderNo)
    paper.updatedAt = now_utc()
    PAPERS[paper.id] = paper
    return paper


def paper_with_questions(paper: PaperEntity, include_answer: bool = True) -> dict[str, Any]:
    normalized = paper.model_dump(mode="json")
    normalized["questions"] = sorted(normalized["questions"], key=lambda item: item["orderNo"])
    questions_by_id = QUESTIONS.get_by_public_ids([item["questionPublicId"] for item in normalized["questions"]])
    resolved_questions = []
    for item in normalized["questions"]:
        question = questions_by_id.get(item["questionPublicId"])
        if question is None:
            continue
        resolved_questions.append(
            {
                **question_to_dict(question, include_answer=include_answer),
                "questionPublicId": item["questionPublicId"],
                "orderNo": item["orderNo"],
                "marks": item.get("marks"),
            }
        )
    normalized["questions"] = resolved_questions
    return normalized


def build_export_questions(paper: PaperEntity, question_order: QuestionOrder, include_answer: bool) -> list[dict[str, Any]]:
    ordered_questions = paper_with_questions(paper, include_answer=include_answer)["questions"]
    return order_export_questions(ordered_questions, question_order)


def order_export_questions(ordered_questions: list[dict[str, Any]], question_order: QuestionOrder) -> list[dict[str, Any]]:
    ordered_questions = sorted(ordered_questions, key=lambda item: item.get("orderNo", 0))
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
