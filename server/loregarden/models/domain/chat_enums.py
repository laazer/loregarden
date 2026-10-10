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
