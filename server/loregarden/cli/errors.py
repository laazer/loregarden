"""Process-level result contract for the `loregarden` CLI.

Every subcommand reports through these three codes so callers — shell scripts, cron jobs,
external services — can tell "you asked wrong" (2) apart from "the operation failed" (1)
without parsing stderr.
"""

from __future__ import annotations

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2


class UsageError(ValueError):
    """Bad invocation — an unknown tool, an undeclared argument, an unparsable value."""
