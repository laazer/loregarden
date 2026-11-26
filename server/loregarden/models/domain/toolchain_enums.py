"""What a binary's Mach-O header says about where it can run.

Split out of `enums` (near its size cap). Read by `services.native_git`, which
picks the git to spawn, and by the doctor's `native_git` check.
"""

from __future__ import annotations

from enum import StrEnum


class BinaryArch(StrEnum):
    """A binary's architecture, as far as Apple silicon is concerned."""

    #: Has an arm64 slice: runs natively on Apple silicon.
    ARM64 = "arm64"
    #: x86_64 only: runs on Apple silicon through Rosetta, or not at all.
    X86_64_ONLY = "x86_64_only"
    #: The header could not be read or is not one we know. Never read as native.
    UNKNOWN = "unknown"
