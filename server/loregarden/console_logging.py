"""Give loregarden's own loggers the console format uvicorn's lines already have.

Without a handler, records from `loregarden.*` fall through to Python's
last-resort handler, which prints the bare message: no level, no colour, and a
warning that reads exactly like the routine line above it. Uvicorn's formatter
gives them the same `WARNING:  ` prefix (coloured on a terminal) its own lines
carry.

The handler sits at WARNING, the threshold the last-resort handler applied, so
this changes how startup looks and not how much of it there is. The logger's
own level is left alone: tests that lower it with `caplog` still see INFO.
"""

from __future__ import annotations

import logging

from uvicorn.logging import DefaultFormatter

_HANDLER = logging.StreamHandler()
_HANDLER.setLevel(logging.WARNING)
_HANDLER.setFormatter(DefaultFormatter("%(levelprefix)s %(message)s", use_colors=None))


def configure_console_logging() -> None:
    """Attach the console handler once; repeat calls (reload, tests) are no-ops."""
    logger = logging.getLogger("loregarden")
    if _HANDLER not in logger.handlers:
        logger.addHandler(_HANDLER)
