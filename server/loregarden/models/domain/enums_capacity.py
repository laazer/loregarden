"""Capacity-ledger vocabularies — split from enums.py for the size gate."""

from enum import StrEnum


class CapacityResource(StrEnum):
    """One dimension a claim must fit in. A waiter is short of one or more."""

    CPUS = "cpus"
    MEMORY_MB = "memory_mb"
    #: A lease-count slot. Every top-level lease books one.
    SLOTS = "slots"
