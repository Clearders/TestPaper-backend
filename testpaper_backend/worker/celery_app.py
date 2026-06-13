from __future__ import annotations

import logging

from celery import Celery, Task

from testpaper_backend.config import get_celery_broker_url, get_celery_result_backend_url

# ---------------------------------------------------------------------------
# Celery application
# ---------------------------------------------------------------------------

celery = Celery("testpaper")

celery.conf.update(
    broker_url=get_celery_broker_url(),
    result_backend=get_celery_result_backend_url(),
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,          # Re-deliver on worker crash
    worker_prefetch_multiplier=1,  # Fair dispatch for long-running tasks
    result_expires=3600,           # Keep results for 1 hour
)

# ---------------------------------------------------------------------------
# Optional base task class with retry + error handling helpers
# ---------------------------------------------------------------------------
class BaseTask(Task):
    """Base Celery task that logs failures and supports automatic retries."""

    abstract = True

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        logger = logging.getLogger("celery.task")
        logger.error("Task %s[%s] failed: %s", self.name, task_id, exc, exc_info=einfo)
        super().on_failure(exc, task_id, args, kwargs, einfo)


# Auto-discover tasks after BaseTask is defined so task modules can import it.
celery.autodiscover_tasks(["testpaper_backend.worker"])
