from __future__ import annotations

import logging
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from threading import Lock
from time import perf_counter

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SyncObservation:
    mutation_count: int = 0
    conflict_count: int = 0
    queue_backlog: int = 0


@dataclass(frozen=True)
class SyncMetricSnapshot:
    operation_count: int
    failure_count: int
    mutation_count: int
    conflict_count: int
    latency_p95_ms: float
    queue_backlog_latest: int
    queue_backlog_max: int

    @property
    def failure_ratio(self) -> float:
        return self.failure_count / self.operation_count if self.operation_count else 0.0

    @property
    def conflict_ratio(self) -> float:
        return self.conflict_count / self.mutation_count if self.mutation_count else 0.0

    def as_dict(self) -> dict[str, int | float]:
        return {
            "operationCount": self.operation_count,
            "failureCount": self.failure_count,
            "failureRatio": self.failure_ratio,
            "mutationCount": self.mutation_count,
            "conflictCount": self.conflict_count,
            "conflictRatio": self.conflict_ratio,
            "latencyP95Ms": self.latency_p95_ms,
            "queueBacklogLatest": self.queue_backlog_latest,
            "queueBacklogMax": self.queue_backlog_max,
        }


@dataclass(frozen=True)
class _SyncMetricSample:
    observed_at: float
    latency_ms: float
    success: bool
    observation: SyncObservation


class _SyncMetricState:
    def __init__(self) -> None:
        self._lock = Lock()
        self._samples: deque[_SyncMetricSample] = deque(maxlen=100_000)

    def record(self, *, latency_ms: float, success: bool, observation: SyncObservation) -> None:
        with self._lock:
            self._samples.append(
                _SyncMetricSample(
                    observed_at=perf_counter(),
                    latency_ms=latency_ms,
                    success=success,
                    observation=observation,
                )
            )

    def snapshot(self) -> SyncMetricSnapshot:
        with self._lock:
            cutoff = perf_counter() - 15 * 60
            while self._samples and self._samples[0].observed_at < cutoff:
                self._samples.popleft()
            samples = list(self._samples)
            ordered = sorted(sample.latency_ms for sample in samples)
            percentile_index = max(0, (len(ordered) * 95 + 99) // 100 - 1)
            latency_p95_ms = ordered[percentile_index] if ordered else 0.0
            return SyncMetricSnapshot(
                operation_count=len(samples),
                failure_count=sum(not sample.success for sample in samples),
                mutation_count=sum(sample.observation.mutation_count for sample in samples),
                conflict_count=sum(sample.observation.conflict_count for sample in samples),
                latency_p95_ms=latency_p95_ms,
                queue_backlog_latest=samples[-1].observation.queue_backlog if samples else 0,
                queue_backlog_max=max((sample.observation.queue_backlog for sample in samples), default=0),
            )

    def reset(self) -> None:
        with self._lock:
            self._samples.clear()


_state = _SyncMetricState()


def observe_sync_call[T](
    operation: str,
    callback: Callable[[], T],
    summarize: Callable[[T], SyncObservation] | None = None,
) -> T:
    started = perf_counter()
    try:
        result = callback()
    except Exception:
        latency_ms = (perf_counter() - started) * 1000
        _state.record(latency_ms=latency_ms, success=False, observation=SyncObservation())
        logger.info("sync_metric operation=%s latency_ms=%.3f success=false conflicts=0 mutations=0 queue_backlog=0", operation, latency_ms)
        raise
    observation = summarize(result) if summarize else SyncObservation()
    latency_ms = (perf_counter() - started) * 1000
    _state.record(latency_ms=latency_ms, success=True, observation=observation)
    logger.info(
        "sync_metric operation=%s latency_ms=%.3f success=true conflicts=%d mutations=%d queue_backlog=%d",
        operation,
        latency_ms,
        observation.conflict_count,
        observation.mutation_count,
        observation.queue_backlog,
    )
    return result


def sync_metric_snapshot() -> SyncMetricSnapshot:
    return _state.snapshot()


def reset_sync_metrics_for_test() -> None:
    _state.reset()
