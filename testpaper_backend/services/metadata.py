from __future__ import annotations

import json
import logging
from collections.abc import Callable

from sqlalchemy import Select, func, select

from testpaper_backend.db import QuestionRow, SessionLocal
from testpaper_backend.redis_client import get_redis

logger = logging.getLogger(__name__)

CACHE_TTL = 300
CACHE_KEY_SUBJECTS = "meta:subjects"
CACHE_KEY_TAGS = "meta:tags"

ALL_META_KEYS = (CACHE_KEY_SUBJECTS, CACHE_KEY_TAGS)
MetaStatementFactory = Callable[[], Select[tuple[str]]]


def _decode_cached_values(cached: object) -> list[str] | None:
    if not isinstance(cached, str | bytes | bytearray):
        return None
    try:
        payload = json.loads(cached)
    except (TypeError, ValueError):
        return None
    if isinstance(payload, list) and all(isinstance(item, str) for item in payload):
        return payload
    return None


def _with_redis_cache(cache_key: str, load_data: Callable[[], list[str]]) -> list[str]:
    try:
        client = get_redis()
        cached = client.get(cache_key)
        if cached is not None:
            cached_values = _decode_cached_values(cached)
            if cached_values is not None:
                return cached_values
            logger.warning("Ignoring invalid metadata cache payload for key %s", cache_key)
    except Exception:
        logger.debug("Metadata cache read failed for key %s; falling back to database.", cache_key, exc_info=True)

    data = load_data()
    try:
        client = get_redis()
        client.setex(cache_key, CACHE_TTL, json.dumps(data))
    except Exception:
        logger.debug("Metadata cache write failed for key %s.", cache_key, exc_info=True)
    return data


def _load_distinct_values(statement_factory: MetaStatementFactory) -> list[str]:
    with SessionLocal() as session:
        return list(session.scalars(statement_factory()).all())


def _subjects_statement() -> Select[tuple[str]]:
    return select(func.jsonb_array_elements_text(QuestionRow.subjects).label("value")).distinct().order_by("value")


def _tags_statement() -> Select[tuple[str]]:
    return select(func.jsonb_array_elements_text(QuestionRow.tags).label("value")).distinct().order_by("value")


def invalidate_meta_cache() -> None:
    try:
        client = get_redis()
        client.delete(*ALL_META_KEYS)
    except Exception:
        logger.debug("Metadata cache invalidation failed.", exc_info=True)


def list_subjects_metadata() -> list[str]:
    return _with_redis_cache(CACHE_KEY_SUBJECTS, lambda: _load_distinct_values(_subjects_statement))


def list_tags_metadata() -> list[str]:
    return _with_redis_cache(CACHE_KEY_TAGS, lambda: _load_distinct_values(_tags_statement))
