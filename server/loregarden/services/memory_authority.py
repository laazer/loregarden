"""Single source of truth for which store owns each memory kind.

GRAPH is the record for durable knowledge (memory, learning, relations).
VAULT is the native record for blog posts and checkpoints. For memory and
learnings the vault is a one-way labelled export of the graph — never a peer.
"""

from __future__ import annotations

from typing import Literal

from loregarden.models.domain.enums import MemoryStoreKind

MemoryArtifactKind = Literal["memory", "learning", "relation", "blog_post", "checkpoint"]
VaultRole = Literal["export", "record", "none"]

_AUTHORITY: dict[MemoryArtifactKind, MemoryStoreKind] = {
    "memory": MemoryStoreKind.GRAPH,
    "learning": MemoryStoreKind.GRAPH,
    "relation": MemoryStoreKind.GRAPH,
    "blog_post": MemoryStoreKind.VAULT,
    "checkpoint": MemoryStoreKind.VAULT,
}

_VAULT_ROLE: dict[MemoryArtifactKind, VaultRole] = {
    "memory": "export",
    "learning": "export",
    "blog_post": "record",
    "checkpoint": "record",
    "relation": "none",
}


def authoritative_store(kind: MemoryArtifactKind) -> MemoryStoreKind:
    """Return the store that owns ``kind``. Raises on unknown kinds."""
    try:
        return _AUTHORITY[kind]
    except KeyError as exc:
        raise ValueError(f"unknown memory kind: {kind!r}") from exc


def vault_role(kind: MemoryArtifactKind) -> VaultRole:
    """How the Obsidian vault relates to ``kind``.

    ``export`` — one-way projection of the GRAPH record (memory/learning).
    ``record`` — vault is the native store (blog_post/checkpoint).
    ``none`` — no vault surface (relations).
    """
    try:
        return _VAULT_ROLE[kind]
    except KeyError as exc:
        raise ValueError(f"unknown memory kind: {kind!r}") from exc


#: Vault-native note types that may appear in ``search()["obsidian"]``.
SEARCH_OBSIDIAN_KINDS: frozenset[str] = frozenset({"blog_post", "checkpoint"})
#: Graph node types that may appear in ``search()["graph"]``.
SEARCH_GRAPH_KINDS: frozenset[str] = frozenset({"memory", "learning"})
