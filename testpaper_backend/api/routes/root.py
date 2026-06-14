from __future__ import annotations

from fastapi import APIRouter, Request

from testpaper_backend.core.responses import envelope

router = APIRouter()


@router.get("/")
def root(request: Request):
    return envelope({"service": "TestPaper Backend", "version": "1.0.0"}, request)
