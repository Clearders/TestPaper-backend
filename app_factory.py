"""Compatibility exports for the FastAPI application factory."""

from testpaper_backend.core.factory import Lifespan, create_app

__all__ = ["Lifespan", "create_app"]

