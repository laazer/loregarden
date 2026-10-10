"""Migrations that register themselves, ordered by what each one follows.

The numbered list in `migrations.py` made every branch claim the next free
number when its migration was written. Main moves before the branch merges, so
the second of any two parallel branches had to renumber — and a renumbered
migration runs a second time against every database that applied the old id.
Both branches also appended to the same two list tails, so they always
conflicted as text.

Here a migration is a function in its own module under `db/versions/`:

    @migration("20261002_workspace_flag", after="0151_ticket_criteria_checked")
    def m_workspace_flag(conn: Connection) -> None: ...

The id is a date and a name, so two branches cannot claim the same one by
accident. The order is the `after` chain, like Alembic's `down_revision`. Two
branches that both follow X still merge without a textual conflict; the chain
then forks, the suite fails on it, and the fix is to point the later one's
`after` at the earlier one. The id does not change, so nothing runs twice, and
a fresh database applies them in the order main merged them, the same order the
live database saw.
"""

from __future__ import annotations

import ast
import heapq
import importlib
import pkgutil
import re
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from types import ModuleType

from sqlalchemy import Connection

Migration = Callable[[Connection], None]

#: A date and a snake_case name: `20261002_workspace_flag`. The date is for
#: people reading the ledger; uniqueness comes from the name.
ID_PATTERN = re.compile(r"^\d{8}_[a-z0-9]+(?:_[a-z0-9]+)*$")

#: The decorator's name, as `ids_declared_in_source` looks for it.
DECORATOR_NAME = "migration"


@dataclass(frozen=True)
class VersionedMigration:
    id: str
    #: The id this migration runs after: the frozen list's last id, or another
    #: versioned migration.
    after: str
    migrate: Migration


@dataclass(frozen=True)
class ChainFork:
    """Two or more migrations that claim to follow the same id."""

    parent: str
    children: tuple[str, ...]


@dataclass(frozen=True)
class ChainOrder:
    ordered: tuple[VersionedMigration, ...]
    #: Empty on a sound chain. A fork still orders deterministically (siblings
    #: by id), but that order is a guess at merge order, not a record of it.
    forks: tuple[ChainFork, ...]


class MigrationChainError(RuntimeError):
    """The versioned migrations do not form a chain that can be ordered."""


class MigrationRegistry:
    """Collects the migrations `db/versions/` modules declare."""

    def __init__(self) -> None:
        self._by_id: dict[str, VersionedMigration] = {}

    def migration(self, migration_id: str, *, after: str) -> Callable[[Migration], Migration]:
        """Register the decorated function under ``migration_id``.

        The id must be a string literal at the call site: the shared-database
        guard reads ids from `origin/main` by parsing source, not by importing it.
        """
        if not ID_PATTERN.match(migration_id):
            raise MigrationChainError(
                f"Migration id {migration_id!r} must be YYYYMMDD_snake_case_name, "
                "e.g. '20261002_workspace_flag'."
            )

        def register(migrate: Migration) -> Migration:
            existing = self._by_id.get(migration_id)
            if existing is not None:
                raise MigrationChainError(
                    f"Duplicate migration id {migration_id!r}: declared by "
                    f"{existing.migrate.__module__} and {migrate.__module__}. Rename the "
                    "one that has not merged."
                )
            self._by_id[migration_id] = VersionedMigration(migration_id, after, migrate)
            return migrate

        return register

    def registered(self) -> list[VersionedMigration]:
        return list(self._by_id.values())


def import_submodules(package: ModuleType) -> list[str]:
    """Import every module directly inside ``package``; return their names."""
    names = sorted(info.name for info in pkgutil.iter_modules(package.__path__))
    for name in names:
        importlib.import_module(f"{package.__name__}.{name}")
    return names


def chain_order(root: str, migrations: Sequence[VersionedMigration]) -> ChainOrder:
    """Order ``migrations`` by their `after` links, starting below ``root``.

    Raises when an `after` names an id that is neither ``root`` nor another
    versioned migration, or when the links form a cycle: neither can be ordered
    at all. A fork can, so it is returned rather than raised — a merge that
    forked the chain must not stop the server booting.
    """
    known = {item.id for item in migrations}
    dangling = sorted(
        f"{item.id} -> {item.after}"
        for item in migrations
        if item.after != root and item.after not in known
    )
    if dangling:
        raise MigrationChainError(
            f"Migration `after` names an unknown id: {', '.join(dangling)}. It must be "
            f"{root!r} or another migration in db/versions/. A renamed migration breaks "
            "the link to it — ids are permanent once merged."
        )

    children: dict[str, list[VersionedMigration]] = defaultdict(list)
    for item in migrations:
        children[item.after].append(item)

    ready = [(item.id, item) for item in children[root]]
    heapq.heapify(ready)
    ordered: list[VersionedMigration] = []
    while ready:
        _, item = heapq.heappop(ready)
        ordered.append(item)
        for child in children[item.id]:
            heapq.heappush(ready, (child.id, child))

    if len(ordered) != len(migrations):
        reached = {item.id for item in ordered}
        cycle = sorted(known - reached)
        raise MigrationChainError(
            f"Migrations {', '.join(cycle)} form a cycle through their `after` links "
            f"and never reach {root!r}."
        )

    forks = tuple(
        ChainFork(parent, tuple(sorted(child.id for child in siblings)))
        for parent, siblings in sorted(children.items())
        if len(siblings) > 1
    )
    return ChainOrder(tuple(ordered), forks)


def ids_declared_in_source(source: str) -> list[str]:
    """The ids a `db/versions/` module registers, read without importing it.

    Matches `@migration("<id>", ...)` with a literal id. The suite asserts this
    finds every id the registry holds, so a non-literal id cannot hide a
    migration from the shared-database guard.
    """
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        match node:
            case ast.Call(
                func=ast.Name(id=name) | ast.Attribute(attr=name),
                args=[ast.Constant(value=str() as migration_id), *_],
            ) if name == DECORATOR_NAME:
                found.append(migration_id)
    return found
