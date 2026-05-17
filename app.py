"""Compatibility entrypoint for `uvicorn app:app`."""

from testpaper_backend.application import app

__all__ = ["app"]

