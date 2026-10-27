"""Vocabularies owned by the Home Baxter chat.

Kept out of ``enums.py``, which sits at the module size cap.
"""

from enum import StrEnum


class BaxterChatScope(StrEnum):
    """What one Home Baxter conversation is about.

    ``workspace`` threads answer about a single workspace. ``global`` threads
    answer across all of them, which is what working on an initiative needs,
    since an initiative's milestones can live in any workspace. A global thread
    still runs in a host workspace's checkout (a CLI needs a directory), but its
    snapshot, references and tools are not confined to that workspace.
    """

    WORKSPACE = "workspace"
    GLOBAL = "global"


class ChatFocusKind(StrEnum):
    """What one row of a thread's focus names: a workspace or an initiative.

    A thread's focus is the set the operator ticked in the chat's Scope card,
    on top of what its archive already implies (a workspace thread's own
    workspace, a focused global thread's initiative).
    """

    WORKSPACE = "workspace"
    INITIATIVE = "initiative"


class FeatureCriterionStatus(StrEnum):
    """Where one acceptance criterion is in a chat's feature loop.

    ``proposed`` is Baxter's draft and reaches nothing until the operator
    accepts it (``queued``) or drops it (``rejected``). The loop moves queued
    criteria onto the ticket as one round (``in_round``), and marks them
    ``met`` when that round's workflow finishes with the ticket done.
    """

    PROPOSED = "proposed"
    QUEUED = "queued"
    IN_ROUND = "in_round"
    MET = "met"
    REJECTED = "rejected"


class FeatureLoopStatus(StrEnum):
    """What a chat's feature loop is doing, as the Feature card reports it."""

    #: Nothing queued and no round running: waiting for the operator.
    IDLE = "idle"
    #: A round's workflow is running on the ticket.
    RUNNING = "running"
    #: The ticket is blocked; the loop will not start a round until it is cleared.
    BLOCKED = "blocked"


class ChatMessageRole(StrEnum):
    """Who wrote a Home Baxter chat message (``BaxterChatMessage.role``).

    The column stays a plain string; this is the vocabulary it holds, so a
    reader compares against a member rather than a literal.
    """

    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
