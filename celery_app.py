"""Compatibility entrypoint for `celery -A celery_app`."""

from testpaper_backend.worker.celery_app import BaseTask, celery

__all__ = ["BaseTask", "celery"]

