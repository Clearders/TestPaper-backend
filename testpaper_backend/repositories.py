from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from testpaper_backend.db import PaperQuestionRow, PaperRow, QuestionRevisionRow, QuestionRow, SessionLocal
from testpaper_backend.question_images import normalize_question_image_url
from testpaper_backend.schemas import (
    Difficulty,
    EssayBlankSpace,
    PaperEntity,
    PaperStatus,
    QuestionBase,
    QuestionEntity,
    QuestionImage,
    QuestionRef,
    QuestionType,
)


def _normalize_enum_token(value: Any) -> str:
    return str(value).strip().lower()


_QUESTION_TYPE_ALIASES = {
    "choice": QuestionType.single_choice,
    "choices": QuestionType.single_choice,
    "choice_question": QuestionType.single_choice,
    "single_choice": QuestionType.single_choice,
    "single_choice_question": QuestionType.single_choice,
    "mcq": QuestionType.single_choice,
    "选择": QuestionType.single_choice,
    "选择题": QuestionType.single_choice,
    "单选": QuestionType.single_choice,
    "单选题": QuestionType.single_choice,
    "单项选择题": QuestionType.single_choice,
    "multiple_choice": QuestionType.multiple_choice,
    "multiple_choice_question": QuestionType.multiple_choice,
    "多选": QuestionType.multiple_choice,
    "多选题": QuestionType.multiple_choice,
    "多项选择题": QuestionType.multiple_choice,
    "mcqa": QuestionType.multiple_choice,
    "true_false": QuestionType.true_false,
    "truefalse": QuestionType.true_false,
    "true_false_question": QuestionType.true_false,
    "judgment": QuestionType.true_false,
    "judgement": QuestionType.true_false,
    "判断": QuestionType.true_false,
    "判断题": QuestionType.true_false,
    "blank": QuestionType.blank,
    "blanks": QuestionType.blank,
    "blank_question": QuestionType.blank,
    "fill_blank": QuestionType.blank,
    "fill_in_blank": QuestionType.blank,
    "fill_in_the_blank": QuestionType.blank,
    "填空": QuestionType.blank,
    "填空题": QuestionType.blank,
    "short_answer": QuestionType.short_answer,
    "shortanswer": QuestionType.short_answer,
    "short_answer_question": QuestionType.short_answer,
    "brief_answer": QuestionType.short_answer,
    "简答": QuestionType.short_answer,
    "简答题": QuestionType.short_answer,
    "essay": QuestionType.essay,
    "essay_question": QuestionType.essay,
    "long_answer": QuestionType.essay,
    "long_answer_question": QuestionType.essay,
    "解答": QuestionType.essay,
    "解答题": QuestionType.essay,
    "问答": QuestionType.essay,
    "问答题": QuestionType.essay,
    "论述": QuestionType.essay,
    "论述题": QuestionType.essay,
}


def _normalize_question_type_token(value: Any) -> str:
    token = _normalize_enum_token(value)
    if "." in token:
        token = token.rsplit(".", 1)[-1]
    token = token.replace("-", "_").replace(" ", "_").replace("　", "_")
    return "_".join(part for part in token.split("_") if part)


def normalize_question_type(value: Any) -> QuestionType:
    token = _normalize_question_type_token(value)
    return _QUESTION_TYPE_ALIASES.get(token) or QuestionType(token)


def has_latex(value: QuestionBase | dict[str, Any]) -> bool:
    text = value.text if isinstance(value, QuestionBase) else value.get("text", "")
    answer = value.answer if isinstance(value, QuestionBase) else value.get("answer", "")
    options = value.options if isinstance(value, QuestionBase) else value.get("options")
    options_text = "".join(options or [])
    return bool(re.search(r"(\$\$[^$]+\$\$|\$[^$]+\$)", f"{text}{answer}{options_text}"))


def _question_images_from_row(images: Any) -> list[QuestionImage]:
    normalized_images: list[QuestionImage] = []
    for image in images or []:
        if not isinstance(image, dict):
            continue
        normalized_url = normalize_question_image_url(str(image.get("url") or ""))
        if normalized_url is None:
            continue
        try:
            normalized_images.append(QuestionImage(**{**image, "url": normalized_url}))
        except ValueError:
            continue
    return normalized_images


def question_row_to_entity(row: QuestionRow) -> QuestionEntity:
    return QuestionEntity(
        id=row.id,
        publicId=row.public_id,
        type=normalize_question_type(row.type),
        subjects=list(row.subjects or []),
        difficulty=Difficulty(_normalize_enum_token(row.difficulty)),
        tags=list(row.tags or []),
        text=row.text,
        options=list(row.options) if row.options is not None else None,
        answer=row.answer,
        hasLatex=row.has_latex,
        source=row.source,
        essayBlankSpace=EssayBlankSpace(**row.essay_blank_space) if row.essay_blank_space is not None else None,
        images=_question_images_from_row(row.images),
        scoreWeight=row.score_weight,
        ownerId=row.owner_id,
        createdAt=row.created_at,
        updatedAt=row.updated_at,
    )


def question_entity_to_row_kwargs(question: QuestionEntity) -> dict[str, Any]:
    return {
        "id": question.id,
        "public_id": question.publicId,
        "type": question.type.value,
        "subjects": list(question.subjects),
        "difficulty": question.difficulty.value,
        "tags": list(question.tags),
        "text": question.text,
        "options": list(question.options) if question.options is not None else None,
        "answer": question.answer,
        "has_latex": question.hasLatex if question.hasLatex is not None else has_latex(question),
        "source": question.source,
        "essay_blank_space": question.essayBlankSpace.model_dump(mode="json") if question.essayBlankSpace is not None else None,
        "images": [img.model_dump(mode="json") for img in (question.images or [])],
        "score_weight": question.scoreWeight,
        "owner_id": question.ownerId,
        "created_at": question.createdAt,
        "updated_at": question.updatedAt,
    }


def paper_row_to_entity(row: PaperRow, public_id_map: dict[int, str] | None = None) -> PaperEntity:
    question_ids = [item.question_id for item in row.questions]
    if public_id_map is None:
        if question_ids:
            with SessionLocal() as session:
                result = session.execute(
                    select(QuestionRow.id, QuestionRow.public_id).where(QuestionRow.id.in_(question_ids))
                ).all()
                public_id_map = {r.id: r.public_id for r in result}
        else:
            public_id_map = {}
    return PaperEntity(
        id=row.id,
        publicId=row.public_id,
        title=row.title,
        subject=row.subject,
        duration=row.duration,
        totalMarks=row.total_marks,
        questions=[
            QuestionRef(questionPublicId=public_id_map.get(item.question_id, ""), orderNo=item.order_no, marks=item.marks)
            for item in sorted(row.questions, key=lambda item: item.order_no)
        ],
        status=PaperStatus(row.status),
        ownerId=row.owner_id,
        createdAt=row.created_at,
        updatedAt=row.updated_at,
    )


def paper_entity_to_row_kwargs(paper: PaperEntity) -> dict[str, Any]:
    return {
        "id": paper.id,
        "public_id": paper.publicId,
        "title": paper.title,
        "subject": paper.subject,
        "duration": paper.duration,
        "total_marks": paper.totalMarks,
        "owner_id": paper.ownerId,
        "status": paper.status.value,
        "created_at": paper.createdAt,
        "updated_at": paper.updatedAt,
    }


class StoreMixin(ABC):
    """Mixin providing dict-like iteration/counting/lookup interface."""
    
    def items(self) -> list[tuple[int, Any]]:
        return [(e.id, e) for e in self.values()]

    def __iter__(self):
        return iter(self.keys())

    def __len__(self) -> int:
        with SessionLocal() as session:
            return int(session.scalar(select(func.count()).select_from(self._table())) or 0)

    def __contains__(self, item_id: object) -> bool:
        return isinstance(item_id, int) and self.get(item_id) is not None

    def __getitem__(self, item_id: int) -> Any:
        item = self.get(item_id)
        if item is None:
            raise KeyError(item_id)
        return item

    @abstractmethod
    def _table(self) -> Any: ...
    @abstractmethod
    def values(self) -> list[Any]: ...
    @abstractmethod
    def keys(self) -> list[int]: ...
    @abstractmethod
    def get(self, item_id: int) -> Any: ...


class QuestionStore(StoreMixin):
    def _table(self):
        return QuestionRow

    def values(self) -> list[QuestionEntity]:
        with SessionLocal() as session:
            rows = session.scalars(select(QuestionRow).order_by(QuestionRow.id)).all()
            return [question_row_to_entity(row) for row in rows]

    def keys(self) -> list[int]:
        with SessionLocal() as session:
            return list(session.scalars(select(QuestionRow.id).order_by(QuestionRow.id)).all())

    def get(self, question_id: int) -> QuestionEntity | None:
        with SessionLocal() as session:
            row = session.get(QuestionRow, question_id)
            return None if row is None else question_row_to_entity(row)

    def get_by_public_id(self, public_id: str) -> QuestionEntity | None:
        with SessionLocal() as session:
            row = session.scalars(
                select(QuestionRow).where(QuestionRow.public_id == public_id)
            ).first()
            if row is None:
                return None
            return question_row_to_entity(row)

    def get_by_public_ids(self, public_ids: list[str]) -> dict[str, QuestionEntity]:
        if not public_ids:
            return {}
        with SessionLocal() as session:
            rows = session.scalars(select(QuestionRow).where(QuestionRow.public_id.in_(set(public_ids)))).all()
            return {row.public_id: question_row_to_entity(row) for row in rows}

    def __setitem__(self, question_id: int, question: QuestionEntity) -> None:
        self.update_with_revision(question_id, question)

    def update_with_revision(
        self,
        question_id: int,
        question: QuestionEntity,
        revision: QuestionRevisionRow | None = None,
    ) -> None:
        payload = question.model_copy(update={"id": question_id}) if question.id != question_id else question
        row_kwargs = question_entity_to_row_kwargs(payload)
        with SessionLocal() as session:
            row = session.get(QuestionRow, question_id)
            if row is None:
                session.add(QuestionRow(**row_kwargs))
            else:
                for key, value in row_kwargs.items():
                    if key == "id":
                        continue
                    setattr(row, key, value)
            if revision is not None:
                session.add(revision)
            session.commit()

    def create(self, question: QuestionEntity) -> QuestionEntity:
        row_kwargs = question_entity_to_row_kwargs(question)
        row_kwargs.pop("id", None)
        with SessionLocal() as session:
            row = QuestionRow(**row_kwargs)
            session.add(row)
            session.commit()
            session.refresh(row)
            return question_row_to_entity(row)

    def __delitem__(self, question_id: int) -> None:
        with SessionLocal() as session:
            row = session.get(QuestionRow, question_id)
            if row is None:
                raise KeyError(question_id)
            session.delete(row)
            session.commit()


class PaperStore(StoreMixin):
    def _table(self):
        return PaperRow

    @staticmethod
    def _resolve_question_refs(questions: list[QuestionRef], session) -> list[PaperQuestionRow]:
        public_ids = [item.questionPublicId for item in questions]
        id_map: dict[str, int] = {}
        if public_ids:
            result = session.execute(
                select(QuestionRow.id, QuestionRow.public_id).where(QuestionRow.public_id.in_(public_ids))
            ).all()
            id_map = {r.public_id: r.id for r in result}
        rows = []
        for item in sorted(questions, key=lambda item: item.orderNo):
            question_id = id_map.get(item.questionPublicId)
            if question_id is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={"code": "QUESTION_NOT_FOUND", "message": f"Question '{item.questionPublicId}' not found"},
                )
            rows.append(PaperQuestionRow(question_id=question_id, order_no=item.orderNo, marks=item.marks))
        return rows

    def values(self) -> list[PaperEntity]:
        with SessionLocal() as session:
            rows = session.scalars(select(PaperRow).options(selectinload(PaperRow.questions)).order_by(PaperRow.id)).all()
            all_question_ids = {item.question_id for row in rows for item in row.questions}
            if all_question_ids:
                result = session.execute(
                    select(QuestionRow.id, QuestionRow.public_id).where(QuestionRow.id.in_(all_question_ids))
                ).all()
                public_id_map = {r.id: r.public_id for r in result}
            else:
                public_id_map = {}
            return [paper_row_to_entity(row, public_id_map) for row in rows]

    def keys(self) -> list[int]:
        with SessionLocal() as session:
            return list(session.scalars(select(PaperRow.id).order_by(PaperRow.id)).all())

    def get(self, paper_id: int) -> PaperEntity | None:
        with SessionLocal() as session:
            row = session.scalars(
                select(PaperRow).options(selectinload(PaperRow.questions)).where(PaperRow.id == paper_id)
            ).first()
            if row is None:
                return None
            question_ids = [item.question_id for item in row.questions]
            result = session.execute(select(QuestionRow.id, QuestionRow.public_id).where(QuestionRow.id.in_(question_ids))).all()
            return paper_row_to_entity(row, {item.id: item.public_id for item in result})

    def get_by_public_id(self, public_id: str) -> PaperEntity | None:
        with SessionLocal() as session:
            row = session.scalars(
                select(PaperRow).options(selectinload(PaperRow.questions)).where(PaperRow.public_id == public_id)
            ).first()
            if row is None:
                return None
            question_ids = [item.question_id for item in row.questions]
            result = session.execute(select(QuestionRow.id, QuestionRow.public_id).where(QuestionRow.id.in_(question_ids))).all()
            return paper_row_to_entity(row, {item.id: item.public_id for item in result})

    def __setitem__(self, paper_id: int, paper: PaperEntity) -> None:
        payload = paper.model_copy(update={"id": paper_id}) if paper.id != paper_id else paper
        with SessionLocal() as session:
            question_rows = self._resolve_question_refs(payload.questions, session)
            seen_question_ids: set[int] = set()
            for item in question_rows:
                if item.question_id in seen_question_ids:
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail={"code": "VALIDATION_ERROR", "message": "Paper must not contain duplicate questions"},
                    )
                seen_question_ids.add(item.question_id)
            row_kwargs = paper_entity_to_row_kwargs(payload)
            row = session.get(PaperRow, paper_id)
            if row is None:
                row = PaperRow(**row_kwargs)
                row.questions = question_rows
                session.add(row)
            else:
                for key, value in row_kwargs.items():
                    if key == "id":
                        continue
                    setattr(row, key, value)
                row.questions = question_rows
            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={"code": "INTEGRITY_ERROR", "message": "Paper references a question that no longer exists"},
                ) from exc

    def create(self, paper: PaperEntity) -> PaperEntity:
        with SessionLocal() as session:
            question_rows = self._resolve_question_refs(paper.questions, session)
            seen_question_ids: set[int] = set()
            for item in question_rows:
                if item.question_id in seen_question_ids:
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail={"code": "VALIDATION_ERROR", "message": "Paper must not contain duplicate questions"},
                    )
                seen_question_ids.add(item.question_id)
            row_kwargs = paper_entity_to_row_kwargs(paper)
            row_kwargs.pop("id", None)
            row = PaperRow(**row_kwargs)
            row.questions = question_rows
            session.add(row)
            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={"code": "INTEGRITY_ERROR", "message": "Paper references a question that no longer exists"},
                ) from exc
            session.refresh(row)
            return paper_row_to_entity(row)

    def __delitem__(self, paper_id: int) -> None:
        with SessionLocal() as session:
            row = session.get(PaperRow, paper_id)
            if row is None:
                raise KeyError(paper_id)
            session.delete(row)
            session.commit()


QUESTIONS = QuestionStore()
PAPERS = PaperStore()
