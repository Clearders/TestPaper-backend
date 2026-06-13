from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class MetaInfo(BaseModel):
    requestId: str


class Envelope[T](BaseModel):
    success: bool = True
    data: T
    meta: MetaInfo


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: Any | None = None


class ErrorEnvelope(BaseModel):
    success: bool = False
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
