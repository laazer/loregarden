"""Where the symbol map is stored (lg-code-knowledge-281).

Three relational tables, one row per symbol. Not a JSON blob per repository
the way the reference implementation writes it: lookup by name is the hot
path and `suggest_names` scans the name set, and a blob forces loading the
entire map to answer either.

Keyed per CHECKOUT, not per workspace. This database is copied into linked
checkouts and scratch directories, so a partition built in one travels into
every copy; the identity is derived from the filesystem
(`services.symbol_map.checkout_identity`) precisely so an inherited copy
reads as absent rather than current.

The rows are narrow on purpose — name, kind, file, line, parent, and 368's
scheme-prefixed `fingerprint`. No source text, no `end_line` (368 hashes an
unparsed node, which needs no extent), no edges (385).
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from loregarden.models.domain.enums import str_enum_column, utcnow
from loregarden.models.domain.symbol_map_enums import (
    ExtractorTier,
    ParseStatus,
    SourceLanguage,
    SymbolKind,
)
from sqlalchemy import Index
from sqlmodel import Field, SQLModel


class SymbolMapPartition(SQLModel, table=True):
    """One checkout's map, stamped with the sha it was built from."""

    __tablename__ = "symbol_map_partitions"
    __table_args__ = (
        # One partition per (workspace, checkout). A unique INDEX rather than a
        # UniqueConstraint so the name the migration creates and the name the
        # models declare are the same string in both places (AC3).
        Index("uq_symbol_map_partition", "workspace_id", "checkout_identity", unique=True),
    )

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    workspace_id: str = Field(foreign_key="workspaces.id")
    #: 16 hex characters of sha256 over the checkout's resolved root.
    checkout_identity: str
    #: That resolved root, so the retirement invariant has a path to ask the
    #: filesystem about. On the partition and not on every file row: repeating
    #: an absolute path per file is the width this map is told to keep small.
    checkout_root: str
    #: The git sha this partition was built from. A reader trusts the line
    #: numbers only while this equals the working copy's HEAD.
    head_sha: str = ""
    built_at: datetime = Field(default_factory=utcnow)
    #: Stamped before a refresh writes its first row and cleared when it
    #: commits (lg-code-knowledge-284). Two jobs in one column: a reader
    #: entering mid-refresh is told `Freshness.BUILDING` rather than handed a
    #: half-written index as complete, and a refresh that dies leaves no stamp
    #: behind — a stamp nothing clears pins every later read at BUILDING
    #: forever while every test about a successful refresh still passes.
    refresh_started_at: datetime | None = Field(default=None)


class SymbolMapFile(SQLModel, table=True):
    """One row per tracked path per partition, supported or not."""

    __tablename__ = "symbol_map_files"
    __table_args__ = (
        # Outline-by-path, and the one-generation-per-(partition, path) rule.
        Index("uq_symbol_map_file_path", "partition_id", "path", unique=True),
    )

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    partition_id: str = Field(foreign_key="symbol_map_partitions.id")
    #: Repo-relative, exactly as `git ls-files -z` emits it.
    path: str
    # Through `str_enum_column` so the stored text is the member's VALUE,
    # the way every other enum column in this schema stores it: these are
    # read by 282, 283 and 304, so they are a wire format.
    language: SourceLanguage = Field(sa_column=str_enum_column(SourceLanguage))
    extractor_tier: ExtractorTier = Field(sa_column=str_enum_column(ExtractorTier))
    parse_status: ParseStatus = Field(sa_column=str_enum_column(ParseStatus))
    #: The exception's message when `parse_status` is PARSE_FAILED, else "".
    parse_error: str = ""
    #: The partition's `head_sha` when this row was written. A column, never a
    #: key component: 284's per-file refresh updates this row in place, so no
    #: current-generation selection rule exists anywhere.
    indexed_sha: str = ""
    index_built_at: datetime = Field(default_factory=utcnow)


class SymbolMapSymbol(SQLModel, table=True):
    """One definition site."""

    __tablename__ = "symbol_map_symbols"
    __table_args__ = (
        # Lookup by name across the checkout, and `suggest_names`' name-only
        # scan. `partition_id` leads so one checkout's names are one range.
        Index("ix_symbol_map_symbol_name", "partition_id", "name"),
        # 282/283 exact-key lookup by the qualified spelling.
        Index("ix_symbol_map_symbol_qname", "partition_id", "qualified_name"),
        # Outline-by-path's second hop, once the file row is known.
        Index("ix_symbol_map_symbol_file", "file_id"),
        # 368's MOVED lookup: the partition-wide search by content, for a
        # qualified name that missed because its file moved.
        Index("ix_symbol_map_symbol_fingerprint", "partition_id", "fingerprint"),
    )

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    file_id: str = Field(foreign_key="symbol_map_files.id")
    #: Denormalised from the file row so a name lookup is one index seek
    #: rather than a join through `symbol_map_files`.
    partition_id: str = Field(foreign_key="symbol_map_partitions.id")
    name: str
    #: `pkg.module.Class.method` for Python, `path/to/file.ts#name` for the
    #: regex tier. `#` occurs in neither a TS identifier nor a path.
    qualified_name: str
    kind: SymbolKind = Field(sa_column=str_enum_column(SymbolKind))
    #: 1-based, `ast.AST.lineno` semantics — the `def`/`class` line, never a
    #: decorator above it.
    line: int
    #: The enclosing class name for a Python method, "" otherwise.
    parent: str = ""
    #: `sym1:<32 hex>` over `ast.unparse(node)` (368). "" for the regex tier,
    #: which cannot bound a body — anchoring one of those is refused at write
    #: time rather than hashing a guessed span (D5). Still no `end_line`:
    #: `ast.unparse` needs no extent.
    fingerprint: str = ""


__all__ = ["SymbolMapFile", "SymbolMapPartition", "SymbolMapSymbol"]
