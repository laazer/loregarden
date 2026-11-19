"""Vocabulary for timing a view save.

Split out of `enums`, which is at its size cap.
"""

from __future__ import annotations

from enum import StrEnum


class ViewSavePhase(StrEnum):
    """Where a view save spent its time, as `services.view_save_timing` reports it.

    The values are the words the slow-save log line prints.
    """

    THREAD_WAIT = "waiting for a worker thread"
    READ = "reading the view"
    WRITE = "write lock wait + commit"
