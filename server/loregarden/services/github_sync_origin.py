"""Marks ticket writes that the GitHub sync itself is making.

Push-on-edit watches every committed ticket edit. An edit *pulled from* GitHub
is one of them, and pushing it straight back would cost a round trip per pull
and, with two servers, could ping-pong. The sync wraps its own writes in
`applying_remote_changes()`; the edit listener skips anything written inside it.

A context variable rather than a flag on the ticket: it follows the thread (and
task) doing the sync and nothing else, so a person's edit committed at the same
moment on another request is still pushed.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_applying_remote: ContextVar[bool] = ContextVar("github_applying_remote", default=False)


@contextmanager
def applying_remote_changes() -> Iterator[None]:
    token = _applying_remote.set(True)
    try:
        yield
    finally:
        _applying_remote.reset(token)


def is_applying_remote_changes() -> bool:
    return _applying_remote.get()
