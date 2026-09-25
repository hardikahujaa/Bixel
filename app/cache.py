"""Response cache. Owner: M2 (Claude.md section 8).

Cache.get_or_compute(query, siis, fn) -> response

Keyed on an embedding of the query's intent plus a hash of the SIIS content,
so a paraphrase of the same problem hits the same entry rather than needing
an exact string match. Hit/miss counts are exposed because they're scored
under A3 and needed for the Day 3 speed numbers (repeat-query p95 <=300ms,
>=90% hit rate; paraphrase hit rate >=80%).
"""
from typing import Any, Callable


class Cache:
    def __init__(self) -> None:
        self.hits = 0
        self.misses = 0

    def get_or_compute(self, query: str, siis: dict, fn: Callable[[], Any]) -> Any:
        raise NotImplementedError(
            "M2: embedding + content-hash cache key, see docstring"
        )
