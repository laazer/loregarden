"""Shared test helpers for the memory stores.

Lives outside both memory test modules because the clock freeze is needed by
`test_memory_store.py` (the store's own recency ordering) and by
`test_inherited_wisdom.py` (the same ordering seen through the briefing). Two
copies drifting apart would make one of those suites quietly stop pinning what
its docstrings claim.
"""

from __future__ import annotations

from unittest.mock import patch


def frozen_clock(*stamps: str):
    """Freeze `memory_store`'s clock to a fixed sequence of ISO timestamps.

    Both stores stamp whole seconds, so two writes inside one test would
    otherwise carry the same `updated_at` and no recency assertion could mean
    anything. The last stamp repeats once the sequence is exhausted, so a test
    that only cares about one write passes one stamp.
    """
    remaining = list(stamps)

    def _next() -> str:
        return remaining.pop(0) if len(remaining) > 1 else remaining[0]

    return patch("loregarden.services.memory_store._utcnow_iso", side_effect=_next)
