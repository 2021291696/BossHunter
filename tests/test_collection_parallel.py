"""平台间并行采集（collection.parallelism）的调度回归。

- 默认（键缺省）= 串行，执行顺序与 platform_order 一致且区间不重叠；
- parallelism = 0 = 全部启用平台并行，执行区间应重叠；
- parallelism = 1 = 显式串行；N = 最多 N 路（封顶到平台数）；
- 各平台自身的节奏/额度/风控参数不受 parallelism 影响。
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


def _registry(order: list[str], spans: list, lock: Lock, duration: float = 0.3) -> CollectorRegistry:
    return CollectorRegistry({
        platform: (lambda p=platform: _SlowCollector(p, spans, lock, duration=duration))
        for platform in order
    })


def _overlaps(spans: list[tuple]) -> bool:
    (_, s1, e1), (_, s2, e2) = spans
    return min(e1, e2) > max(s1, s2)


class ParallelCollectionTests(TestCase):
    def test_default_is_serial_with_matching_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            order = ["zhilian", "liepin"]
            lock = Lock()
            spans: list = []
            result = CollectionOrchestrator(
                {}, db_path=Path(tmp) / "db.sqlite", registry=_registry(order, spans, lock, duration=0.12),
            ).run(_options(order))

            self.assertEqual(result["status"], "completed")
            self.assertEqual([r["platform"] for r in result["results"]], order)
            self.assertFalse(_overlaps(spans), "默认（串行）执行区间不应重叠")

    def test_parallelism_zero_runs_all_platforms_in_parallel(self):
        with tempfile.TemporaryDirectory() as tmp:
            order = ["zhilian", "liepin"]
            lock = Lock()
            spans: list = []
            config = {"collection": {"parallelism": 0}}
            started = time.monotonic()
            result = CollectionOrchestrator(
                config, db_path=Path(tmp) / "db.sqlite", registry=_registry(order, spans, lock),
            ).run(_options(order))
            elapsed = time.monotonic() - started

            self.assertEqual(result["status"], "completed")
            self.assertEqual({r["platform"] for r in result["results"]}, set(order))
            self.assertLess(elapsed, 0.55, f"显式 0 应并行执行，实际耗时 {elapsed:.2f}s")
            self.assertTrue(_overlaps(spans), "显式 0 的执行区间应重叠")

    def test_parallelism_one_keeps_serial_order_and_no_overlap(self):
        with tempfile.TemporaryDirectory() as tmp:
            order = ["zhilian", "liepin"]
            lock = Lock()
            spans: list = []
            config = {"collection": {"parallelism": 1}}
            result = CollectionOrchestrator(
                config, db_path=Path(tmp) / "db.sqlite", registry=_registry(order, spans, lock, duration=0.12),
            ).run(_options(order))

            self.assertEqual(result["status"], "completed")
            self.assertEqual([r["platform"] for r in result["results"]], order)
            self.assertFalse(_overlaps(spans), "串行模式执行区间不应重叠")

    def test_resolve_parallelism_semantics(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "db.sqlite"
            registry = CollectorRegistry({})

            def lanes(config: dict, count: int) -> int:
                return CollectionOrchestrator(config, db_path=db_path, registry=registry)._resolve_parallelism(count)

            # 默认（键缺省）与显式 1 都是串行；非法值保守回退串行
            self.assertEqual(lanes({}, 4), 1)
            self.assertEqual(lanes({"collection": {}}, 4), 1)
            self.assertEqual(lanes({"collection": {"parallelism": 1}}, 4), 1)
            self.assertEqual(lanes({"collection": {"parallelism": "bad"}}, 4), 1)
            self.assertEqual(lanes({"collection": {"parallelism": -2}}, 4), 1)
            # 显式 0 = 全并行；N = 封顶
            self.assertEqual(lanes({"collection": {"parallelism": 0}}, 4), 4)
            self.assertEqual(lanes({"collection": {"parallelism": 2}}, 4), 2)
            self.assertEqual(lanes({"collection": {"parallelism": 9}}, 3), 3)
