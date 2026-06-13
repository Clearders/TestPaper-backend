from __future__ import annotations

import contextvars
import logging

_current_request_id: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")


def set_request_id(request_id: str) -> None:
    _current_request_id.set(request_id)


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _current_request_id.get()
        return True


def configure_library_loggers() -> None:
    handler = logging.StreamHandler()
    handler.addFilter(RequestIdFilter())
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(request_id)s] %(name)s %(levelname)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    ))
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
