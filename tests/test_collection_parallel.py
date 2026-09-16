"""平台间并行采集（collection.parallelism）的调度回归。

- parallelism 缺省 0 = 所有启用平台并行，执行区间应重叠；
- parallelism = 1 = 旧行为串行，执行顺序与 platform_order 一致且区间不重叠；
- 并行模式结果汇总（platform_results / collected_job_ids）与串行等价。
各平台自身的节奏/额度/风控参数不受 parallelism 影响。
"""

import tempfile
import time
from pathlib import Path
from threading import Lock
from unittest import TestCase

from bosshunter.collection.models import PlatformCollectionResult
from bosshunter.collection.orchestrator import CollectionOrchestrator
from bosshunter.collection.registry import CollectorRegistry


class _SlowCollector:
    """记录执行区间并固定耗时，用于断言平台间是否并发重叠。"""

    def __init__(self, platform: str, spans: list, lock: Lock, duration: float = 0.3):
        self.platform = platform
        self.spans = spans
        self.lock = lock
        self.duration = duration

    def collect(self, _request, _hooks):
        with self.lock:
            start = time.monotonic()
        time.sleep(self.duration)
        with self.lock:
            self.spans.append((self.platform, start, time.monotonic()))
        return PlatformCollectionResult(self.platform, "completed", "", "ok")


def _options(order: list[str]) -> dict:
    values = {}
    for platform in order:
        values[platform] = {
            "keywords": ["AI"],
            "cities": ["北京"],
            "city_codes": {"北京": "101010100"},
            "max_pages": 1,
            "sort": "default",
        }
    return {"platform_order": order, "platforms": values}


def _overlaps(spans: list[tuple]) -> bool:
    (_, s1, e1), (_, s2, e2) = spans
    return min(e1, e2) > max(s1, s2)


class ParallelCollectionTests(TestCase):
    def test_parallel_overlaps_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            order = ["zhilian", "liepin"]
            lock = Lock()
            spans: list = []
            registry = CollectorRegistry({
                platform: (lambda p=platform: _SlowCollector(p, spans, lock))
                for platform in order
            })
            started = time.monotonic()
            result = CollectionOrchestrator(
                {}, db_path=Path(tmp) / "db.sqlite", registry=registry,
            ).run(_options(order))
            elapsed = time.monotonic() - started

            self.assertEqual(result["status"], "completed")
            self.assertEqual({r["platform"] for r in result["results"]}, set(order))
            self.assertLess(elapsed, 0.55, f"两平台应重叠执行，实际耗时 {elapsed:.2f}s")
            self.assertTrue(_overlaps(spans), "两平台执行区间应重叠")

    def test_parallelism_one_keeps_serial_order_and_no_overlap(self):
        with tempfile.TemporaryDirectory() as tmp:
            order = ["zhilian", "liepin"]
            lock = Lock()
            spans: list = []
            registry = CollectorRegistry({
                platform: (lambda p=platform: _SlowCollector(p, spans, lock, duration=0.12))
                for platform in order
            })
            config = {"collection": {"parallelism": 1}}
            result = CollectionOrchestrator(
                config, db_path=Path(tmp) / "db.sqlite", registry=registry,
            ).run(_options(order))

            self.assertEqual(result["status"], "completed")
            self.assertEqual([r["platform"] for r in result["results"]], order)
            self.assertFalse(_overlaps(spans), "串行模式执行区间不应重叠")

    def test_parallelism_cap_limits_worker_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            order = ["zhilian", "liepin"]
            lock = Lock()
            spans: list = []
            registry = CollectorRegistry({
                platform: (lambda p=platform: _SlowCollector(p, spans, lock, duration=0.2))
                for platform in order
            })
            config = {"collection": {"parallelism": 1 + 1}}
            orchestrator = CollectionOrchestrator(
                config, db_path=Path(tmp) / "db.sqlite", registry=registry,
            )
            self.assertEqual(orchestrator._resolve_parallelism(len(order)), 2)
            self.assertEqual(orchestrator._resolve_parallelism(4), 2)
            self.assertEqual(
                CollectionOrchestrator({}, db_path=orchestrator.db_path, registry=registry)._resolve_parallelism(4), 4,
            )
