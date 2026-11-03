"""Raise this process's soft open-file limit, so the suites it spawns inherit one they can run under.

A server started from a GUI terminal inherits launchd's soft limit of 256
descriptors. Every child inherits it too, and `pytest -n auto` across ten
workers runs out: from 2026-11-02 land verification refused landings on
`OSError: [Errno 24] Too many open files` and "unable to open database file"
in setup, in tests that pass from a shell whose limit is a million. Only the
soft limit moves, never past the hard one, and nothing needs privilege.
"""

from __future__ import annotations

import logging
import resource

logger = logging.getLogger(__name__)

#: Enough for a parallel suite with headroom. macOS refuses RLIM_INFINITY for a
#: soft RLIMIT_NOFILE and caps it at `kern.maxfilesperproc` (245760 here), so a
#: finite target is the portable one.
TARGET_OPEN_FILES = 65536


def raise_open_file_limit() -> int:
    """Lift the soft RLIMIT_NOFILE toward `TARGET_OPEN_FILES`. Returns the limit now in force."""
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    target = TARGET_OPEN_FILES if hard == resource.RLIM_INFINITY else min(TARGET_OPEN_FILES, hard)
    if soft != resource.RLIM_INFINITY and soft >= target:
        return soft
    try:
        resource.setrlimit(resource.RLIMIT_NOFILE, (target, hard))
    except (ValueError, OSError):
        # Not silent: the suites this server runs will hit EMFILE at the old
        # limit, and this line is what names why.
        logger.warning(
            "Could not raise the open-file limit from %s to %s; parallel suites this "
            "server runs may fail with 'Too many open files'",
            soft,
            target,
            exc_info=True,
        )
        return soft
    return target
