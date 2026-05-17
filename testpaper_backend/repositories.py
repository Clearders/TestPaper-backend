from __future__ import annotations

from typing import Any, cast

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from testpaper_backend.db import PaperQuestionRow, PaperRow, QuestionRow, SessionLocal
from testpaper_backend.schemas import Difficulty, EssayBlankSpace, PaperEntity, PaperQuestion, PaperStatus, QuestionBase, QuestionEntity, QuestionImage, QuestionType


def has_latex(value: QuestionBase | dict[str, Any]) -> bool:
    text = value.text if isinstance(value, QuestionBase) else value.get("text", "")
    answer = value.answer if isinstance(value, QuestionBase) else value.get("answer", "")
    options = value.options if isinstance(value, QuestionBase) else value.get("options")
    options_text = "".join(options or [])
    return bool(__import__("re").search(r"(\$\$[^$]+\$\$|\$[^$]+\$)", f"{text}{answer}{options_text}"))


def question_row_to_entity(row: QuestionRow) -> QuestionEntity:
    return QuestionEntity(
        id=row.id,
        type=QuestionType(row.type),
        subject=row.subject,
        difficulty=Difficulty(row.difficulty),
        tags=list(row.tags or []),
        text=row.text,
        options=list(row.options) if row.options is not None else None,
        answer=row.answer,
        hasLatex=row.has_latex,
        source=row.source,
        essayBlankSpace=EssayBlankSpace(**row.essay_blank_space) if row.essay_blank_space is not None else None,
        images=[QuestionImage(**img) for img in (row.images or [])],
        scoreWeight=row.score_weight,
        ownerId=row.owner_id,
        createdAt=row.created_at,
        updatedAt=row.updated_at,
    )


def question_entity_to_row_kwargs(question: QuestionEntity) -> dict[str, Any]:
    return {
        "id": question.id,
        "type": question.type.value,
        "subject": question.subject,
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


def paper_row_to_entity(row: PaperRow) -> PaperEntity:
    return PaperEntity(
        id=row.id,
        title=row.title,
        subject=row.subject,
        duration=row.duration,
        totalMarks=row.total_marks,
        questions=[
            PaperQuestion(questionId=item.question_id, orderNo=item.order_no, marks=item.marks)
            for item in sorted(row.questions, key=lambda item: item.order_no)
        ],
        status=PaperStatus(row.status),
        createdAt=row.created_at,
        updatedAt=row.updated_at,
    )


def paper_entity_to_row_kwargs(paper: PaperEntity) -> dict[str, Any]:
    return {
        "id": paper.id,
        "title": paper.title,
        "subject": paper.subject,
        "duration": paper.duration,
        "total_marks": paper.totalMarks,
        "status": paper.status.value,
        "created_at": paper.createdAt,
        "updated_at": paper.updatedAt,
    }


class QuestionStore:
    def values(self) -> list[QuestionEntity]:
        with SessionLocal() as session:
            rows = session.scalars(select(QuestionRow).order_by(QuestionRow.id)).all()
            return [question_row_to_entity(row) for row in rows]

    def keys(self) -> list[int]:
        return [question.id for question in self.values()]

    def items(self) -> list[tuple[int, QuestionEntity]]:
        return [(question.id, question) for question in self.values()]

    def __iter__(self):
        return iter(self.keys())

    def __len__(self) -> int:
        with SessionLocal() as session:
            return int(session.scalar(select(func.count()).select_from(QuestionRow)) or 0)

    def __contains__(self, question_id: object) -> bool:
        return isinstance(question_id, int) and self.get(question_id) is not None

    def get(self, question_id: int) -> QuestionEntity | None:
        with SessionLocal() as session:
            row = cast(QuestionRow | None, session.get(QuestionRow, question_id))
            return None if row is None else question_row_to_entity(row)

    def __getitem__(self, question_id: int) -> QuestionEntity:
        question = self.get(question_id)
        if question is None:
            raise KeyError(question_id)
        return question

    def __setitem__(self, question_id: int, question: QuestionEntity) -> None:
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


class PaperStore:
    def values(self) -> list[PaperEntity]:
        with SessionLocal() as session:
            rows = session.scalars(select(PaperRow).options(selectinload(PaperRow.questions)).order_by(PaperRow.id)).all()
            return [paper_row_to_entity(row) for row in rows]

    def keys(self) -> list[int]:
        return [paper.id for paper in self.values()]

    def items(self) -> list[tuple[int, PaperEntity]]:
        return [(paper.id, paper) for paper in self.values()]

    def __iter__(self):
        return iter(self.keys())

    def __len__(self) -> int:
        with SessionLocal() as session:
            return int(session.scalar(select(func.count()).select_from(PaperRow)) or 0)

    def __contains__(self, paper_id: object) -> bool:
        return isinstance(paper_id, int) and self.get(paper_id) is not None

    def get(self, paper_id: int) -> PaperEntity | None:
        with SessionLocal() as session:
            row = cast(PaperRow | None, session.scalars(
                select(PaperRow).options(selectinload(PaperRow.questions)).where(PaperRow.id == paper_id)
            ).first())
            return None if row is None else paper_row_to_entity(row)

    def __getitem__(self, paper_id: int) -> PaperEntity:
        paper = self.get(paper_id)
        if paper is None:
            raise KeyError(paper_id)
        return paper

    def __setitem__(self, paper_id: int, paper: PaperEntity) -> None:
        payload = paper.model_copy(update={"id": paper_id}) if paper.id != paper_id else paper
        question_rows = [
            PaperQuestionRow(question_id=item.questionId, order_no=item.orderNo, marks=item.marks)
            for item in sorted(payload.questions, key=lambda item: item.orderNo)
        ]
        row_kwargs = paper_entity_to_row_kwargs(payload)
        with SessionLocal() as session:
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
            session.commit()

    def create(self, paper: PaperEntity) -> PaperEntity:
        question_rows = [
            PaperQuestionRow(question_id=item.questionId, order_no=item.orderNo, marks=item.marks)
            for item in sorted(paper.questions, key=lambda item: item.orderNo)
        ]
        row_kwargs = paper_entity_to_row_kwargs(paper)
        row_kwargs.pop("id", None)
        with SessionLocal() as session:
            row = PaperRow(**row_kwargs)
            row.questions = question_rows
            session.add(row)
            session.commit()
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
