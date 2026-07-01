from __future__ import annotations

import json

import pytest

from testpaper_backend.services import metadata


class FakeRedis:
    def __init__(self, cached: object | None = None) -> None:
        self.cached = cached
        self.setex_calls: list[tuple[str, int, str]] = []

    def get(self, key: str) -> object | None:
        return self.cached

    def setex(self, key: str, ttl: int, value: str) -> None:
        self.setex_calls.append((key, ttl, value))
        self.cached = value


def test_metadata_cache_returns_valid_cached_values(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_redis = FakeRedis(b'["Math", "Physics"]')
    monkeypatch.setattr(metadata, "get_redis", lambda: fake_redis)

    def load_data() -> list[str]:
        raise AssertionError("cache hit should not load from the database")

    assert metadata._with_redis_cache(metadata.CACHE_KEY_SUBJECTS, load_data) == ["Math", "Physics"]
    assert fake_redis.setex_calls == []


@pytest.mark.parametrize("cached", [b'{"Math": true}', b'["Math", 2]', b"not-json"])
def test_metadata_cache_ignores_invalid_payloads(monkeypatch: pytest.MonkeyPatch, cached: bytes) -> None:
    fake_redis = FakeRedis(cached)
    monkeypatch.setattr(metadata, "get_redis", lambda: fake_redis)

    assert metadata._with_redis_cache(metadata.CACHE_KEY_SUBJECTS, lambda: ["Math"]) == ["Math"]
    assert fake_redis.setex_calls == [
        (metadata.CACHE_KEY_SUBJECTS, metadata.CACHE_TTL, json.dumps(["Math"])),
    ]


def test_metadata_cache_falls_back_when_redis_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    def get_unavailable_redis() -> object:
        raise ConnectionError("redis unavailable")

    monkeypatch.setattr(metadata, "get_redis", get_unavailable_redis)

    assert metadata._with_redis_cache(metadata.CACHE_KEY_TAGS, lambda: ["algebra"]) == ["algebra"]
