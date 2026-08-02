from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel


class MetaInfo(BaseModel):
    requestId: str


class Envelope[T](BaseModel):
    success: Literal[True] = True
    data: T
    meta: MetaInfo


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: Any | None = None


class ErrorEnvelope(BaseModel):
    success: Literal[False] = False
    error: ErrorDetail
    meta: MetaInfo


class PaginationInfo(BaseModel):
    page: int
    pageSize: int
    total: int
    totalPages: int


class PaginatedResponse[T](BaseModel):
    items: list[T]
    pagination: PaginationInfo
