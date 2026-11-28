"""Per-workspace symbol map: line-numbered, sha-stamped, no LLM.

Ticket lg-code-knowledge-281, specification artifact
`590d5c54-a782-407b-85bd-e1b6b285070f`, requirements R1-R7. The package under
test is `loregarden.services.symbol_map`; it does not exist until the implement
stage, so every test in this file is red (indeed, a collection error) until
then. That is what this stage produces.

**Every repository used by a test is built by a fixture, never in a test
body.** A broken setup inside a test body fails in the call phase and is
indistinguishable from a healthy failure. The fixture shape follows
`tests/test_code_signals.py`, which is this ticket's placement precedent in
every other respect too (lg-code-knowledge-299).

`run_git` is used rather than bare `subprocess` so an ambient `GIT_DIR` from a
hook context cannot rebind a fixture repo to the checkout the hook fired for.

The real-checkout half of the suite — the git-cleanliness delta and the AC11
measurement — lives in `test_symbol_map_on_this_repo.py`, so the fast tests
here stay `tmp_path`-only and the selection graph stays honest about what the
slow one costs.
"""

from __future__ import annotations

import ast
import difflib
import logging
import os
import re
import shutil
import sys
from pathlib import Path
from unittest import mock

import pytest
from loregarden.config import settings
from loregarden.db.migrations import MIGRATIONS
from loregarden.db.versions import symbol_map as symbol_map_migration
from loregarden.db.versions import symbol_map_fingerprint as fingerprint_migration
from loregarden.models.domain import (
    ExtractorTier,
    Freshness,
    InvariantReentry,
    ParseStatus,
    SourceLanguage,
    SymbolKind,
    SymbolMapFile,
    SymbolMapPartition,
    SymbolMapSymbol,
)
from loregarden.services import symbol_map as symbol_map_package
from loregarden.services.directory_release import DirectoryState
from loregarden.services.invariant_registry import INVARIANTS
from loregarden.services.invariants import run_invariant
from loregarden.services.symbol_map import (
    InterpreterTooOldError,
    build_symbol_map,
    definitions_for,
    map_status,
    outline_for,
    suggest_names,
)
from loregarden.services.symbol_map import extract as extract_module
from loregarden.services.symbol_map import partitions as partitions_module
from loregarden.services.symbol_map import refresh as refresh_module
from loregarden.services.symbol_map import sources as sources_module
from loregarden.services.symbol_map import store as store_module
from loregarden.services.symbol_map.checkout_identity import identify
from loregarden.services.symbol_map.extract import extract
from loregarden.services.symbol_map.sources import detect, head_sha, tracked_files
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select
from tests.import_scan import imported_names
from tests.repo_templates import from_template
from tests.symbol_map_repos import (  # noqa: F401 - fixtures resolved by name
    BARREL_MODULE,
    OVERLOAD_MODULE,
    PY_MODULE,
    PYTHON_EXPECTED,
    TS_EXPECTED,
    TS_MODULE,
    TSX_MODULE,
    _commit,
    _git,
    _init,
    _write,
    _write_bytes,
    broken_repo_fixture,
    mapped_repo_fixture,
    migrated_engine_fixture,
    other_workspace_fixture,
    recorded_sql_fixture,
    session_fixture,
    suggest_repo_fixture,
    unreadable_repo_fixture,
    workspace_fixture,
    worktree_pair_fixture,
)

PACKAGE_ROOT = Path(sources_module.__file__).parent


# --------------------------------------------------------------------------
# Small readers over the stored rows.
# --------------------------------------------------------------------------


def _partition(session: Session, workspace_id: str, root: Path) -> SymbolMapPartition:
    return session.exec(
        select(SymbolMapPartition).where(
            SymbolMapPartition.workspace_id == workspace_id,
            SymbolMapPartition.checkout_identity == identify(root),
        )
    ).one()


def _file_rows(session: Session, partition_id: str) -> dict[str, SymbolMapFile]:
    rows = session.exec(
        select(SymbolMapFile).where(SymbolMapFile.partition_id == partition_id)
    ).all()
    return {row.path: row for row in rows}


def _symbol_rows(session: Session, partition_id: str) -> list[SymbolMapSymbol]:
    return list(
        session.exec(
            select(SymbolMapSymbol).where(SymbolMapSymbol.partition_id == partition_id)
        ).all()
    )


def _symbols_in(session: Session, partition_id: str, path: str) -> list[SymbolMapSymbol]:
    file_row = _file_rows(session, partition_id)[path]
    return sorted(
        (row for row in _symbol_rows(session, partition_id) if row.file_id == file_row.id),
        key=lambda row: row.line,
    )


def _indexes(engine, table: str) -> set[str]:
    with engine.connect() as conn:
        return {
            row[1]
            for row in conn.execute(text(f"PRAGMA index_list({table})"))
            if not row[1].startswith("sqlite_autoindex")
        }


def _columns(engine, table: str) -> dict[str, str]:
    with engine.connect() as conn:
        return {row[1]: row[2] for row in conn.execute(text(f"PRAGMA table_info({table})"))}


def _package_modules() -> list[Path]:
    return sorted(PACKAGE_ROOT.glob("*.py"))


def _tree_files(root: Path) -> set[str]:
    return {
        str(path.relative_to(root))
        for path in root.rglob("*")
        if path.is_file() and ".git" not in path.parts
    }


# ==========================================================================
# R1 / AC1.1-AC1.4 — schema, enums, migration
# ==========================================================================

EXPECTED_INDEXES = {
    "symbol_map_partitions": {"uq_symbol_map_partition"},
    "symbol_map_files": {"uq_symbol_map_file_path"},
    "symbol_map_symbols": {
        "ix_symbol_map_symbol_name",
        "ix_symbol_map_symbol_qname",
        "ix_symbol_map_symbol_file",
    },
}

MODELS_BY_TABLE = {
    "symbol_map_partitions": SymbolMapPartition,
    "symbol_map_files": SymbolMapFile,
    "symbol_map_symbols": SymbolMapSymbol,
}


def _declared_constraint_names(model) -> set[str]:
    declared = getattr(model, "__table_args__", ())
    return {name for name in (getattr(arg, "name", None) for arg in declared) if name}


def test_the_migration_builds_all_three_tables_and_exactly_five_named_indexes(migrated_engine):
    with migrated_engine.begin() as conn:
        symbol_map_migration.m_symbol_map(conn)

    built = {table: _indexes(migrated_engine, table) for table in EXPECTED_INDEXES}

    assert built == EXPECTED_INDEXES
    assert sum(len(names) for names in built.values()) == 5


def test_the_models_index_set_equals_the_migrations_index_set(migrated_engine):
    """AC3 wants equality, not containment: a model index the migration never
    builds exists in every test database and in no live one, so the query it
    was declared for is a table scan only in production.

    Both migrations run, because the models carry the union: 368 added
    `ix_symbol_map_symbol_fingerprint` in its own append-only module rather
    than editing this one, and the equality is over what a live database
    actually ends up with."""
    with migrated_engine.begin() as conn:
        symbol_map_migration.m_symbol_map(conn)
        fingerprint_migration.m_symbol_map_fingerprint(conn)

    declared = {
        name for model in MODELS_BY_TABLE.values() for name in _declared_constraint_names(model)
    }
    built = {name for table in EXPECTED_INDEXES for name in _indexes(migrated_engine, table)}

    assert declared == built
    assert "ix_symbol_map_symbol_fingerprint" in built


def test_running_the_migration_twice_is_a_no_op(migrated_engine):
    with migrated_engine.begin() as conn:
        symbol_map_migration.m_symbol_map(conn)
    before = {t: _indexes(migrated_engine, t) for t in EXPECTED_INDEXES}
    before_columns = {t: _columns(migrated_engine, t) for t in EXPECTED_INDEXES}

    with migrated_engine.begin() as conn:
        symbol_map_migration.m_symbol_map(conn)

    assert {t: _indexes(migrated_engine, t) for t in EXPECTED_INDEXES} == before
    assert {t: _columns(migrated_engine, t) for t in EXPECTED_INDEXES} == before_columns


def test_a_partial_build_is_completed_rather_than_failed(migrated_engine):
    with migrated_engine.begin() as conn:
        symbol_map_migration.m_symbol_map(conn)
    with migrated_engine.begin() as conn:
        conn.execute(text("DROP INDEX ix_symbol_map_symbol_name"))

    with migrated_engine.begin() as conn:
        symbol_map_migration.m_symbol_map(conn)

    assert "ix_symbol_map_symbol_name" in _indexes(migrated_engine, "symbol_map_symbols")


def test_the_migration_is_registered_once_after_the_code_signals_migration():
    """281's schema migration is registered exactly once, after the one it
    follows. The id is permanent: the database records it, and renaming it
    runs the body again under the new name.

    lg-code-knowledge-368 appends `20261118_symbol_map_fingerprint` beside it,
    for the same reason and under the same rule.

    lg-code-knowledge-284 appends `20261118_symbol_map_refresh` beside it.
    That one is its own ticket's test (`test_symbol_map_refresh.py` asserts it
    is registered AFTER this one, which is the ordering that matters); what
    this test owns is that 281's id is still there, still once, and still in
    the right place.
    """
    ids = [mid for mid, _ in MIGRATIONS]

    assert ids.count("20261117_symbol_map") == 1, "exactly one id for 281's schema"
    assert ids.index("20261117_symbol_map") > ids.index("20261116_code_signals")


def test_the_symbol_set_is_relational_and_carries_no_source_text(migrated_engine):
    """AC2: a row per symbol, and none of the columns the ticket rules out. A
    JSON blob makes both primary operations a deserialize-and-scan.

    368 added `fingerprint` — a 36-character scheme-prefixed digest, not source
    text — and deliberately did NOT add `end_line`: `ast.unparse` needs no
    extent, so the row stays as narrow as 281 left it plus one key."""
    with migrated_engine.begin() as conn:
        symbol_map_migration.m_symbol_map(conn)
        fingerprint_migration.m_symbol_map_fingerprint(conn)

    for table in EXPECTED_INDEXES:
        columns = _columns(migrated_engine, table)
        assert columns, table
        assert not any(name.endswith("_json") for name in columns), table
        declared_types = {(kind or "").upper() for kind in columns.values()}
        assert not any("JSON" in kind or "BLOB" in kind for kind in declared_types), table

    symbols = _columns(migrated_engine, "symbol_map_symbols")
    assert set(symbols) == {
        "id",
        "file_id",
        "partition_id",
        "name",
        "qualified_name",
        "kind",
        "line",
        "parent",
        "fingerprint",
    }
    for forbidden in ("source", "body", "text", "end_line", "content_hash"):
        assert forbidden not in symbols


def test_the_partition_records_the_sha_it_was_built_from(migrated_engine):
    """AC4: `head_sha` beside the identity, keyed UNIQUE(workspace, identity)."""
    with migrated_engine.begin() as conn:
        symbol_map_migration.m_symbol_map(conn)

    partitions = _columns(migrated_engine, "symbol_map_partitions")

    assert {"workspace_id", "checkout_identity", "checkout_root", "head_sha", "built_at"} <= set(
        partitions
    )


def test_every_enum_has_exactly_the_members_the_spec_names():
    assert {member.value for member in SymbolKind} == {
        "class",
        "function",
        "method",
        "constant",
        "interface",
        "type_alias",
        "enum",
        "variable",
    }
    assert {member.value for member in SourceLanguage} == {
        "python",
        "typescript",
        "tsx",
        "other",
    }
    assert {member.value for member in ExtractorTier} == {"python_ast", "regex_fallback"}
    assert {member.value for member in ParseStatus} == {
        "parsed",
        "unsupported_language",
        "parse_failed",
    }
    assert {member.value for member in Freshness} == {
        "matches",
        "sha_mismatch",
        "no_partition",
        "building",
    }


# ==========================================================================
# R2 / AC2.1-AC2.2 — checkout identity
# ==========================================================================


def test_two_checkouts_of_one_repository_get_different_identities(worktree_pair):
    primary, linked = worktree_pair

    assert identify(primary) != identify(linked)


def test_a_symlink_and_its_target_get_the_same_identity(tmp_path):
    """Identity is derived from the resolved realpath, so the same checkout
    reached by two paths is one partition rather than two."""
    real = _init(tmp_path / "real")
    link = tmp_path / "link"
    link.symlink_to(real)

    assert identify(link) == identify(real)


def test_the_identity_is_sixteen_hex_characters(tmp_path):
    assert re.fullmatch(r"[0-9a-f]{16}", identify(_init(tmp_path / "shape")))


def test_the_identity_is_derived_in_exactly_one_module():
    """AC4 / AC2.2. A second derivation is where a correct-looking answer gets
    reported: the two spellings disagree and nothing says which one wrote the
    row."""
    # `fingerprint.py` (368) also hashes, but it hashes a symbol's unparsed
    # SOURCE and never a path — it is the other content hash in the package,
    # not a second spelling of the checkout identity. Excluded by name so a
    # third hashing module still reds this test.
    deriving = [
        module.name
        for module in _package_modules()
        if "sha256" in module.read_text(encoding="utf-8") and module.name != "fingerprint.py"
    ]

    assert deriving == ["checkout_identity.py"]


def test_the_identity_never_consults_a_worktrees_row_or_walks_upwards():
    """The database travels into worktrees, so an identity read out of it
    would travel too and an inherited copy would claim the originating
    checkout's rows. A parent-directory walk hands a nested worktree the
    primary's partition, which is the same bug from the other side."""
    source = (PACKAGE_ROOT / "checkout_identity.py").read_text(encoding="utf-8")

    assert "Worktree" not in source
    assert ".parent" not in source
    for name in imported_names(source):
        assert not name.startswith("sqlmodel")
        assert not name.startswith("sqlalchemy")
        assert not name.startswith("loregarden.models")


# ==========================================================================
# R3 / AC3.1-AC3.5 — source enumeration and Python extraction
# ==========================================================================


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("a/b.py", SourceLanguage.PYTHON),
        ("a/b.pyi", SourceLanguage.PYTHON),
        ("a/b.ts", SourceLanguage.TYPESCRIPT),
        ("a/b.tsx", SourceLanguage.TSX),
        ("a/b.md", SourceLanguage.OTHER),
        ("a/b.bin", SourceLanguage.OTHER),
        ("Taskfile", SourceLanguage.OTHER),
    ],
)
def test_the_language_is_decided_by_extension(path, expected):
    assert detect(path) == expected


def test_tracked_files_and_head_sha_read_the_root_they_are_handed(mapped_repo):
    files = tracked_files(mapped_repo)

    assert "pkg/widget.py" in files
    assert "assets/blob.bin" in files
    assert head_sha(mapped_repo) == _git(mapped_repo, "rev-parse", "HEAD").stdout.strip()


def test_an_ambient_git_dir_cannot_redirect_the_enumeration(monkeypatch, mapped_repo, broken_repo):
    """GIT_DIR overrides `cwd`, and this control plane's git runs from hooks.
    An unscrubbed call enumerates the hook's repository and stamps the answer
    with the other repository's HEAD."""
    monkeypatch.setenv("GIT_DIR", str(broken_repo / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(broken_repo))

    assert "pkg/widget.py" in tracked_files(mapped_repo)
    assert "bad.py" not in tracked_files(mapped_repo)
    assert head_sha(mapped_repo) != head_sha(broken_repo)


def test_python_extraction_records_classes_methods_functions_and_constants(
    session, workspace, mapped_repo
):
    """AC6 / AC3.1. Nested functions and class-level assignments are not
    recorded (A6): the ticket names four kinds and a closure is none of them."""
    build_symbol_map(session, workspace.id, mapped_repo)
    partition = _partition(session, workspace.id, mapped_repo)

    rows = _symbols_in(session, partition.id, "pkg/widget.py")

    assert {(r.name, r.kind, r.line, r.parent) for r in rows} == PYTHON_EXPECTED


def test_a_method_and_a_free_function_of_one_name_stay_distinguishable(
    session, workspace, mapped_repo
):
    """AC6 / AC3.2 — the reason `parent` is on the row at all."""
    build_symbol_map(session, workspace.id, mapped_repo)
    partition = _partition(session, workspace.id, mapped_repo)

    renders = [r for r in _symbols_in(session, partition.id, "pkg/widget.py") if r.name == "render"]

    assert {(r.kind, r.parent) for r in renders} == {
        (SymbolKind.METHOD, "Widget"),
        (SymbolKind.FUNCTION, ""),
    }


def test_a_python_qualified_name_is_the_dotted_module_path(session, workspace, mapped_repo):
    build_symbol_map(session, workspace.id, mapped_repo)
    partition = _partition(session, workspace.id, mapped_repo)

    rows = {r.name: r for r in _symbols_in(session, partition.id, "pkg/widget.py")}

    assert rows["Widget"].qualified_name == "pkg.widget.Widget"
    assert rows["fetch"].qualified_name == "pkg.widget.Widget.fetch"
    assert rows["amain"].qualified_name == "pkg.widget.amain"


def test_a_package_init_drops_its_trailing_init_from_the_qualified_name(
    session, workspace, tmp_path
):
    root = _init(tmp_path / "initmod")
    _write(root, "pkg/__init__.py", "def boot():\n    return 1\n")
    _commit(root, "feat: a package init")

    build_symbol_map(session, workspace.id, root)
    partition = _partition(session, workspace.id, root)

    assert _symbols_in(session, partition.id, "pkg/__init__.py")[0].qualified_name == "pkg.boot"


def test_a_python_source_with_a_coding_declaration_is_parsed_from_its_bytes(
    session, workspace, tmp_path
):
    """A5: the bytes go to `ast.parse`, which honours PEP 263. `read_text()`
    would guess UTF-8 and report a real module as unparseable."""
    root = _init(tmp_path / "latin")
    _write_bytes(
        root,
        "legacy.py",
        b"# -*- coding: latin-1 -*-\n# caf\xe9\ndef latin_one():\n    return 1\n",
    )
    _commit(root, "feat: latin-1 source")

    report = build_symbol_map(session, workspace.id, root)
    partition = _partition(session, workspace.id, root)

    assert report.parse_failures == 0
    assert _file_rows(session, partition.id)["legacy.py"].parse_status == ParseStatus.PARSED
    assert [r.name for r in _symbols_in(session, partition.id, "legacy.py")] == ["latin_one"]


def test_an_unparseable_supported_file_is_recorded_rather_than_fatal(
    session, workspace, broken_repo
):
    """AC7 / AC3.3: the row says PARSE_FAILED, carries the message, holds no
    symbols, and the rest of the build completes."""
    report = build_symbol_map(session, workspace.id, broken_repo)
    partition = _partition(session, workspace.id, broken_repo)
    rows = _file_rows(session, partition.id)

    assert rows["bad.py"].parse_status == ParseStatus.PARSE_FAILED
    assert rows["bad.py"].parse_error
    assert _symbols_in(session, partition.id, "bad.py") == []
    assert rows["good.py"].parse_status == ParseStatus.PARSED
    assert report.parse_failures == 2  # bad.py and the non-UTF-8 .ts


def test_a_non_utf8_typescript_file_is_a_parse_failure_not_a_crash(session, workspace, broken_repo):
    build_symbol_map(session, workspace.id, broken_repo)
    partition = _partition(session, workspace.id, broken_repo)

    row = _file_rows(session, partition.id)["undecodable.ts"]

    assert row.parse_status == ParseStatus.PARSE_FAILED
    assert row.parse_error


def test_every_tracked_path_gets_a_row_and_an_unsupported_one_holds_no_symbols(
    session, workspace, mapped_repo
):
    """AC7 / AC3.4. Absent from the map and present-but-unsupported must not
    be the same answer; a caller that cannot tell them apart cannot say
    whether the map covers a path at all."""
    build_symbol_map(session, workspace.id, mapped_repo)
    partition = _partition(session, workspace.id, mapped_repo)
    rows = _file_rows(session, partition.id)

    assert set(rows) == set(tracked_files(mapped_repo))

    blob = rows["assets/blob.bin"]
    assert blob.language == SourceLanguage.OTHER
    assert blob.parse_status == ParseStatus.UNSUPPORTED_LANGUAGE
    assert blob.parse_error == ""
    assert _symbols_in(session, partition.id, "assets/blob.bin") == []
    assert rows["README.md"].parse_status == ParseStatus.UNSUPPORTED_LANGUAGE


@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root reads a mode-000 file, so the permission cannot prove the rule",
)
def test_an_unsupported_file_is_never_opened(session, workspace, unreadable_repo):
    """R3.3. A build that opens it either raises or records PARSE_FAILED; a
    build that honours the rule never notices the file is unreadable. Not
    reading it is also why a binary path can never raise a decode error."""
    report = build_symbol_map(session, workspace.id, unreadable_repo)
    partition = _partition(session, workspace.id, unreadable_repo)

    assert report.parse_failures == 0
    assert (
        _file_rows(session, partition.id)["secret.bin"].parse_status
        == ParseStatus.UNSUPPORTED_LANGUAGE
    )


def test_the_two_failure_kinds_are_counted_separately_on_the_report(session, workspace, tmp_path):
    """AC7 / AC3.5: 'we could not read it' and 'we do not read this kind' are
    different facts, and one counter for both hides whichever is smaller."""
    root = _init(tmp_path / "counts")
    _write(root, "ok.py", "def ok():\n    return 1\n")
    _write(root, "bad.py", "def oops(:\n")
    _write(root, "notes.md", "# notes\n")
    _write(root, "data.csv", "a,b\n")
    _commit(root, "feat: two of each")

    report = build_symbol_map(session, workspace.id, root)

    assert report.files_seen == 4
    assert report.parse_failures == 1
    assert report.unsupported_files == 2


def test_the_build_refuses_an_interpreter_older_than_the_repo_requires(
    session, workspace, mapped_repo
):
    """AC6 / AC3.7. `ast.parse` is only as new as the interpreter: verified,
    seven files in `server/loregarden` fail under system 3.9 and parse clean
    under 3.11, and this repo's hook scripts run under 3.9. Silently reporting
    a seventh of `services/` as unparseable would make 282's NOT-FOUND a
    confident lie."""
    with mock.patch.object(sys, "version_info", (3, 9, 18, "final", 0)):
        with pytest.raises(InterpreterTooOldError) as raised:
            build_symbol_map(session, workspace.id, mapped_repo)

    assert "3.11" in str(raised.value)


# ==========================================================================
# R4 / AC4.1-AC4.8 — TypeScript, TSX and the tier dispatcher
# ==========================================================================


def test_typescript_declarations_are_extracted_at_their_lines(session, workspace, mapped_repo):
    build_symbol_map(session, workspace.id, mapped_repo)
    partition = _partition(session, workspace.id, mapped_repo)

    rows = _symbols_in(session, partition.id, "web/module.ts")

    assert {(r.name, r.kind, r.line) for r in rows} == TS_EXPECTED


def test_a_typescript_class_method_is_not_claimed_by_the_regex_tier(
    session, workspace, mapped_repo
):
    """Deferred to 386 by R4.1's constraint, and asserted here so the gap is a
    decision rather than an accident. The regex tier cannot bound a class body
    without a parser, and guessing by indentation ships exactly the
    plausible-looking wrongness this map exists to prevent."""
    build_symbol_map(session, workspace.id, mapped_repo)
    partition = _partition(session, workspace.id, mapped_repo)

    assert "start" not in {r.name for r in _symbols_in(session, partition.id, "web/module.ts")}


def test_a_barrel_contributes_no_symbol(session, workspace, mapped_repo):
    """AC7 / AC4.2. A re-export is not a definition site; matching one returns
    an indistinguishable hit per barrel in the chain and buries the real one."""
    build_symbol_map(session, workspace.id, mapped_repo)
    partition = _partition(session, workspace.id, mapped_repo)

    assert "web/index.ts" in _file_rows(session, partition.id)
    assert _symbols_in(session, partition.id, "web/index.ts") == []


def test_overload_signatures_collapse_to_the_implementations_line(session, workspace, mapped_repo):
    """AC7 / AC4.3. A caller asking where `parse` is defined wants the body."""
    build_symbol_map(session, workspace.id, mapped_repo)
    partition = _partition(session, workspace.id, mapped_repo)

    rows = _symbols_in(session, partition.id, "web/overloads.ts")

    assert [(r.name, r.line) for r in rows] == [("parse", 3)]


def test_a_tsx_component_is_extracted(session, workspace, mapped_repo):
    build_symbol_map(session, workspace.id, mapped_repo)
    partition = _partition(session, workspace.id, mapped_repo)

    rows = _symbols_in(session, partition.id, "web/Thing.tsx")

    assert [(r.name, r.kind, r.line) for r in rows] == [("Thing", SymbolKind.FUNCTION, 1)]
    assert _file_rows(session, partition.id)["web/Thing.tsx"].language == SourceLanguage.TSX


def test_a_typescript_qualified_name_is_the_path_hash_name(session, workspace, mapped_repo):
    """AC7 / AC4.5. `#` occurs in neither a TS identifier nor a repo-relative
    path, so the key splits without escaping."""
    build_symbol_map(session, workspace.id, mapped_repo)
    partition = _partition(session, workspace.id, mapped_repo)

    rows = {r.name: r for r in _symbols_in(session, partition.id, "web/module.ts")}

    assert rows["makeThing"].qualified_name == "web/module.ts#makeThing"


def test_every_typescript_row_is_stamped_regex_fallback(session, workspace, mapped_repo):
    """AC7 / AC4.6. The tier is how a reader tells a 281-era TS node from a
    post-386 one, so 283's brief and 304's envelope can weigh them."""
    build_symbol_map(session, workspace.id, mapped_repo)
    partition = _partition(session, workspace.id, mapped_repo)
    rows = _file_rows(session, partition.id)

    assert rows["web/module.ts"].extractor_tier == ExtractorTier.REGEX_FALLBACK
    assert rows["web/Thing.tsx"].extractor_tier == ExtractorTier.REGEX_FALLBACK
    assert rows["pkg/widget.py"].extractor_tier == ExtractorTier.PYTHON_AST


@pytest.mark.parametrize(
    "name",
    ["MAX_RETRIES", "useThing"],
)
def test_the_all_caps_convention_decides_constant_from_variable(
    session, workspace, mapped_repo, name
):
    """A7: TypeScript's `const` is a binding rule, not a constant-ness claim,
    and the all-caps convention is what a caller searching for a constant
    means. Flagged medium-confidence in the spec; 386 may revisit it."""
    build_symbol_map(session, workspace.id, mapped_repo)
    partition = _partition(session, workspace.id, mapped_repo)
    rows = {r.name: r for r in _symbols_in(session, partition.id, "web/module.ts")}

    expected = SymbolKind.CONSTANT if name.isupper() else SymbolKind.VARIABLE
    assert rows[name].kind == expected


@pytest.mark.parametrize(
    ("path", "data"),
    [
        ("a.py", b"def oops(:\n"),
        ("a.py", b""),
        ("a.py", b"\xff\xfe\x00not python"),
        ("a.ts", b"\xff\xfe not utf-8"),
        ("a.ts", b""),
        ("a.bin", b"\x00\x01\x02"),
        ("noext", b"anything"),
    ],
)
def test_extract_answers_for_every_input_and_raises_for_none(path, data):
    """AC7 / AC4.7. `extract` is the pure boundary 386 swaps behind: every
    failure is a `parse_status` on the returned value, never an exception the
    build loop has to know about."""
    result = extract(path, data)

    assert result.parse_status in set(ParseStatus)
    assert isinstance(result.symbols, list)  # py-org: allow-isinstance - shape of a return value


def test_a_language_no_tier_claims_is_unsupported_even_when_it_reads_like_code():
    result = extract("notes.txt", b"export function looksLikeTypeScript() {}\n")

    assert result.parse_status is ParseStatus.UNSUPPORTED_LANGUAGE
    assert result.symbols == []


def test_extract_knows_nothing_about_a_session_git_or_the_filesystem():
    """AC12 / AC4.8. The dispatcher is pure; reading happens in the build loop,
    which hands it the path and the bytes."""
    source = Path(extract_module.__file__).read_text(encoding="utf-8")
    imported = imported_names(source)

    for forbidden in (
        "sqlmodel",
        "sqlalchemy",
        "subprocess",
        "os",
        "io",
        "shutil",
        "loregarden.db",
        "loregarden.services.git_subprocess",
    ):
        assert not any(
            name == forbidden or name.startswith(f"{forbidden}.") for name in imported
        ), forbidden

    for forbidden_call in ("open(", ".read_text(", ".read_bytes(", "run_git"):
        assert forbidden_call not in source


# ==========================================================================
# R2, R5 / AC2.3-AC2.5, AC5.1-AC5.9 — the public surface
# ==========================================================================


def test_the_report_counts_match_the_stored_rows_exactly(session, workspace, mapped_repo):
    """AC1 / AC5.1-AC5.2. A count nobody can check against the rows cannot
    tell a working build from a no-op."""
    report = build_symbol_map(session, workspace.id, mapped_repo)
    partition = _partition(session, workspace.id, mapped_repo)
    files = _file_rows(session, partition.id)

    assert report.files_seen == len(files)
    assert report.symbol_rows == len(_symbol_rows(session, partition.id))
    assert report.unsupported_files == sum(
        1 for row in files.values() if row.parse_status == ParseStatus.UNSUPPORTED_LANGUAGE
    )
    assert report.parse_failures == sum(
        1 for row in files.values() if row.parse_status == ParseStatus.PARSE_FAILED
    )
    assert report.head_sha == head_sha(mapped_repo) == partition.head_sha
    assert report.checkout_identity == identify(mapped_repo) == partition.checkout_identity
    assert report.elapsed_seconds >= 0

    with pytest.raises((AttributeError, TypeError, ValueError)):
        report.files_seen = 0


def test_a_rebuild_replaces_the_partitions_rows_rather_than_accumulating_them(
    session, workspace, mapped_repo
):
    first = build_symbol_map(session, workspace.id, mapped_repo)
    # The edit has to REMOVE a definition, not rename one: swapping
    # `def render()` for `def only_one()` leaves the count where it was, and
    # the assertion below then fails against a build that replaced correctly.
    (mapped_repo / "pkg" / "other.py").write_text("# every definition here is gone\n")
    _commit(mapped_repo, "refactor: empty other.py")

    second = build_symbol_map(session, workspace.id, mapped_repo)
    partition = _partition(session, workspace.id, mapped_repo)

    assert second.symbol_rows < first.symbol_rows
    assert len(_symbol_rows(session, partition.id)) == second.symbol_rows
    assert len(_file_rows(session, partition.id)) == second.files_seen
    assert len(session.exec(select(SymbolMapPartition)).all()) == 1, (
        "one partition per (workspace, checkout), not one per build"
    )
    assert [r.path for r in definitions_for(session, workspace.id, mapped_repo, "render").rows] == [
        "pkg/widget.py",
        "pkg/widget.py",
    ], "the removed definition is gone from the map, not left behind beside the new rows"


def test_a_failed_rebuild_leaves_the_previous_partition_intact(session, workspace, mapped_repo):
    """AC2 / AC5.8. Compute the new rows before deleting the old ones. The
    other order turns a transient read failure into a map that is gone."""
    build_symbol_map(session, workspace.id, mapped_repo)
    partition = _partition(session, workspace.id, mapped_repo)
    before = {(r.name, r.line, r.parent) for r in _symbol_rows(session, partition.id)}
    assert before

    calls = {"n": 0}
    real_extract = store_module.extract

    def failing_extract(path: str, data: bytes):
        calls["n"] += 1
        if calls["n"] > 2:
            raise RuntimeError("extraction died midway")
        return real_extract(path, data)

    with mock.patch.object(store_module, "extract", failing_extract):
        with pytest.raises(RuntimeError):
            build_symbol_map(session, workspace.id, mapped_repo)
    session.rollback()

    partition = _partition(session, workspace.id, mapped_repo)
    assert {(r.name, r.line, r.parent) for r in _symbol_rows(session, partition.id)} == before


def test_definitions_for_returns_every_site_ordered_by_path_then_line(
    session, workspace, mapped_repo
):
    """AC8 / AC5.3. Unranked and deterministic: with no edges a ranker would
    ship a constant sort key, which 282 and 283 own instead."""
    build_symbol_map(session, workspace.id, mapped_repo)

    read = definitions_for(session, workspace.id, mapped_repo, "render")

    assert read.status.freshness == Freshness.MATCHES
    assert [(row.path, row.line, row.kind, row.parent) for row in read.rows] == [
        ("pkg/other.py", 1, SymbolKind.FUNCTION, ""),
        ("pkg/widget.py", 11, SymbolKind.METHOD, "Widget"),
        ("pkg/widget.py", 21, SymbolKind.FUNCTION, ""),
    ]


def test_a_definition_read_does_not_cross_into_another_workspaces_partition(
    session, workspace, other_workspace, mapped_repo
):
    build_symbol_map(session, workspace.id, mapped_repo)

    read = definitions_for(session, other_workspace.id, mapped_repo, "render")

    assert read.status.freshness == Freshness.NO_PARTITION
    assert read.rows == []


def test_outline_for_returns_one_files_symbols_ordered_by_line(session, workspace, mapped_repo):
    build_symbol_map(session, workspace.id, mapped_repo)

    read = outline_for(session, workspace.id, mapped_repo, "pkg/widget.py")

    assert read.status.freshness == Freshness.MATCHES
    assert [row.line for row in read.rows] == sorted(line for _, _, line, _ in PYTHON_EXPECTED)
    assert {(row.name, row.kind, row.line, row.parent) for row in read.rows} == PYTHON_EXPECTED


def test_a_symbol_location_carries_its_own_values_rather_than_a_live_row(
    session, workspace, mapped_repo
):
    """A9: a frozen value, so a consumer cannot lazy-load through a closed
    session and get an exception where it expected a line number."""
    build_symbol_map(session, workspace.id, mapped_repo)
    row = outline_for(session, workspace.id, mapped_repo, "pkg/widget.py").rows[0]

    assert row.name and row.qualified_name and row.path
    assert row.extractor_tier == ExtractorTier.PYTHON_AST
    with pytest.raises((AttributeError, TypeError, ValueError)):
        row.line = 0


def test_suggest_names_returns_the_real_name_for_a_misspelling(session, workspace, suggest_repo):
    build_symbol_map(session, workspace.id, suggest_repo)

    read = suggest_names(session, workspace.id, suggest_repo, "calculate_total_pric")

    assert read.status.freshness == Freshness.MATCHES
    assert "calculate_total_price" in read.rows
    assert len(read.rows) <= 5


def test_suggest_names_honours_its_limit(session, workspace, suggest_repo):
    build_symbol_map(session, workspace.id, suggest_repo)

    read = suggest_names(session, workspace.id, suggest_repo, "aaaaaaaaaaaa", limit=2)

    assert len(read.rows) <= 2


def test_suggest_names_fetches_fewer_names_than_the_partition_holds(
    session, workspace, suggest_repo, recorded_sql
):
    """AC8 / AC5.5. The claim is 'it narrows in SQL and never selects a symbol
    row'; this measures the claim instead of restating it. The window
    [ceil(3L/7), floor(7L/3)] is provably lossless for cutoff 0.6, since
    ratio = 2M/(L+n) <= 2*min(L,n)/(L+n) puts every name outside it below the
    cutoff — so narrowing here cannot drop a suggestion."""
    build_symbol_map(session, workspace.id, suggest_repo)
    partition = _partition(session, workspace.id, suggest_repo)
    distinct_names = (
        session.connection()
        .exec_driver_sql(
            "SELECT COUNT(DISTINCT name) FROM symbol_map_symbols WHERE partition_id = ?",
            (partition.id,),
        )
        .scalar_one()
    )

    recorded_sql.clear()
    suggest_names(session, workspace.id, suggest_repo, "calculate_total_pric")

    over_symbols = [
        (statement, params)
        for statement, params in recorded_sql
        if "symbol_map_symbols" in statement
    ]
    assert len(over_symbols) == 1, over_symbols
    statement, params = over_symbols[0]

    selected = statement.upper().split("FROM")[0]
    assert "*" not in selected
    for column in ("QUALIFIED_NAME", "KIND", "LINE", "PARENT", "FILE_ID", "ID"):
        assert column not in selected, selected

    fetched = len(session.connection().exec_driver_sql(statement, params).fetchall())
    assert 0 < fetched < distinct_names


def test_freshness_is_four_valued_and_every_reader_carries_its_status(
    session, workspace, mapped_repo
):
    """AC9 / AC2.3-AC2.5. No partition, a sha mismatch and a genuine zero hit
    all render as `[]` otherwise — the collapse 'No silent failures' forbids,
    and the one 282's explicit NOT-FOUND cannot be built on."""
    assert map_status(session, workspace.id, mapped_repo).freshness == Freshness.NO_PARTITION

    build_symbol_map(session, workspace.id, mapped_repo)
    assert map_status(session, workspace.id, mapped_repo).freshness == Freshness.MATCHES

    genuinely_absent = definitions_for(session, workspace.id, mapped_repo, "no_such_symbol")
    assert genuinely_absent.rows == []
    assert genuinely_absent.status.freshness == Freshness.MATCHES

    _write(mapped_repo, "pkg/later.py", "def later():\n    return 1\n")
    _commit(mapped_repo, "feat: move HEAD past the build")

    assert map_status(session, workspace.id, mapped_repo).freshness == Freshness.SHA_MISMATCH
    stale = definitions_for(session, workspace.id, mapped_repo, "render")
    assert stale.rows
    assert stale.status.freshness == Freshness.SHA_MISMATCH
    assert stale.status.freshness != genuinely_absent.status.freshness


@pytest.mark.parametrize(
    "reader",
    [
        lambda s, w, r: definitions_for(s, w, r, "render"),
        lambda s, w, r: outline_for(s, w, r, "pkg/widget.py"),
        lambda s, w, r: suggest_names(s, w, r, "rendr"),
    ],
    ids=["definitions_for", "outline_for", "suggest_names"],
)
def test_no_reader_returns_bare_rows(session, workspace, mapped_repo, reader):
    """AC9 / AC2.4. A caller must not be able to obtain rows without the
    verdict; a `map_status()` the caller is trusted to call first is a
    convention, not a contract."""
    build_symbol_map(session, workspace.id, mapped_repo)

    read = reader(session, workspace.id, mapped_repo)

    assert read.status.freshness in set(Freshness)
    assert isinstance(read.rows, list)  # py-org: allow-isinstance - shape of a return value


def test_a_build_against_a_linked_worktree_stores_the_worktrees_line_numbers(
    session, workspace, worktree_pair
):
    """AC9 / AC5.6, and R2's highest-ranked risk made falsifiable. The obvious
    signature `build_symbol_map(session, workspace)` would walk
    `Workspace.repo_path` while `identify()` derives from wherever the caller
    is, so a build invoked from a worktree writes rows stamped with the
    worktree's identity holding the primary checkout's line numbers. Every
    fixture that passes one root to both derivations passes either way; this
    one passes two."""
    primary, linked = worktree_pair
    build_symbol_map(session, workspace.id, primary)

    report = build_symbol_map(session, workspace.id, linked)

    assert report.checkout_identity == identify(linked) != identify(primary)
    assert report.head_sha == head_sha(linked) != head_sha(primary)

    primary_rows = {
        r.name: r.line
        for r in _symbols_in(
            session, _partition(session, workspace.id, primary).id, "pkg/widget.py"
        )
    }
    linked_rows = {
        r.name: r.line
        for r in _symbols_in(session, _partition(session, workspace.id, linked).id, "pkg/widget.py")
    }

    assert linked_rows["Widget"] == primary_rows["Widget"] + 2
    assert linked_rows == {name: line + 2 for name, line in primary_rows.items()}
    assert outline_for(session, workspace.id, linked, "pkg/widget.py").status.freshness == (
        Freshness.MATCHES
    )


def test_the_package_never_reads_the_workspaces_recorded_repo_path():
    """AC9 / AC5.7. One derivation, at the top of the request: a second one in
    a helper is where a correct-looking answer gets reported."""
    for module in _package_modules():
        assert "repo_path" not in module.read_text(encoding="utf-8"), module.name


def test_the_public_surface_is_exactly_nine_functions():
    """R5, widened by lg-code-knowledge-284 and -368: consumers (282, 283,
    300, 304, 385) import from here and nothing deeper, so the seam is worth
    pinning. 284 adds the writer that keeps the map current and renames the
    verdict reader (the old spelling is gone rather than aliased, an internal
    posture); 368 adds the write-time half and the read paths' entry point.
    `verdicts_for` is deliberately not here: `claim_verdicts` calls it and
    nothing outside the package does."""
    exported = {
        name
        for name in getattr(symbol_map_package, "__all__", [])
        if callable(getattr(symbol_map_package, name, None))
    }

    assert exported == {
        "build_symbol_map",
        "claim_verdicts",
        "definitions_for",
        "outline_for",
        "resolve_anchors",
        "suggest_names",
        "map_status",
        "refresh_at_run_entry",
        "refresh_symbol_map",
    }


def test_a_build_writes_no_file_into_the_repository(session, workspace, mapped_repo):
    """AC2's last clause: the map lives in the database, and a file written
    into the tree gets swept into an unrelated ticket's commit."""
    before = _tree_files(mapped_repo)

    build_symbol_map(session, workspace.id, mapped_repo)

    assert _tree_files(mapped_repo) == before


# ==========================================================================
# R6 / AC6.1-AC6.5, AC10 — the partition-retirement invariant
# ==========================================================================


def _register_partition(session: Session, workspace_id: str, root: Path) -> SymbolMapPartition:
    row = SymbolMapPartition(
        workspace_id=workspace_id,
        checkout_identity=identify(root),
        checkout_root=str(Path(os.path.realpath(root))),
        head_sha="0" * 40,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def test_the_invariant_is_registered_expected_with_a_repair_and_a_reason():
    """AC10 / AC6.1. Verified `services/invariants.py:84-105`: `repair=None` is
    a permanent property, and a report-only invariant reports every violation
    on every pass forever. Over ~83 ephemeral checkouts that is a
    backlog-finding firehose, so 'report-only first run' is not a mode the
    registry has."""
    registered = [inv for inv in INVARIANTS if inv.name == "orphaned_symbol_map_partition"]

    assert len(registered) == 1
    invariant = registered[0]
    assert invariant.reentry == InvariantReentry.EXPECTED
    assert invariant.repair is not None
    assert invariant.expected_because
    assert invariant.ticket == "lg-code-knowledge-281"


def test_no_partitions_registered_finds_nothing_and_files_nothing(session, workspace):
    """AC10 / AC6.2. 'Nothing to judge' is the state 281 actually ships in:
    no production writer, so the live table is empty."""
    assert partitions_module.orphaned_partitions(session) == []


def test_partitions_registered_but_none_resolving_present_refuses_the_pass(
    session, workspace, tmp_path
):
    """AC10 / AC6.3. A detector that examines nothing and reports zero is the
    classic failure here; 'nothing to do' and 'I could not tell' must not be
    the same answer. Every root absent at once is what an unmounted volume or
    a wrong-host database looks like, and repairing on it deletes every map."""
    gone = _register_partition(session, workspace.id, tmp_path / "ghost-one")
    also_gone = _register_partition(session, workspace.id, tmp_path / "ghost-two")

    violations = partitions_module.orphaned_partitions(session)

    assert len(violations) == 1
    assert {gone.id, also_gone.id}.isdisjoint({v.subject_id for v in violations})
    assert "refus" in violations[0].detail.lower()

    assert partitions_module.retire_orphaned_partitions(session, violations) == 0
    assert len(session.exec(select(SymbolMapPartition)).all()) == 2


def test_only_the_absent_partition_is_retired_when_others_are_present(
    session, workspace, mapped_repo, tmp_path
):
    """AC10 / AC6.4. Symbol rows, then file rows, then the partition row last,
    one transaction per partition — enumerated from `symbol_map_partitions`
    and never from the rows being deleted."""
    build_symbol_map(session, workspace.id, mapped_repo)
    live = _partition(session, workspace.id, mapped_repo)
    live_symbols = len(_symbol_rows(session, live.id))
    assert live_symbols
    orphan = _register_partition(session, workspace.id, tmp_path / "removed-checkout")

    violations = partitions_module.orphaned_partitions(session)
    assert [v.subject_id for v in violations] == [orphan.id]

    assert partitions_module.retire_orphaned_partitions(session, violations) == 1

    assert [p.id for p in session.exec(select(SymbolMapPartition)).all()] == [live.id]
    assert len(_symbol_rows(session, live.id)) == live_symbols
    assert len(_file_rows(session, live.id)) == len(tracked_files(mapped_repo))


def test_an_orphans_file_and_symbol_rows_go_with_it(session, workspace, mapped_repo, tmp_path):
    build_symbol_map(session, workspace.id, mapped_repo)
    orphan = _partition(session, workspace.id, mapped_repo)
    orphan.checkout_root = str(tmp_path / "vanished")
    session.add(orphan)
    session.commit()

    violations = partitions_module.orphaned_partitions(session)
    partitions_module.retire_orphaned_partitions(session, violations)

    assert session.exec(select(SymbolMapPartition)).all() == []
    assert session.exec(select(SymbolMapFile)).all() == []
    assert session.exec(select(SymbolMapSymbol)).all() == []


def test_a_filesystem_that_will_not_answer_aborts_the_pass(session, workspace, mapped_repo):
    """AC10 / AC6.5. Verified `directory_release.py:143`: ABSENT means ENOENT
    or ENOTDIR only, and every other OSError is raised. Reading EACCES or EIO
    as 'absent' is one step short of deleting a live checkout's map."""
    build_symbol_map(session, workspace.id, mapped_repo)

    with mock.patch.object(
        partitions_module, "directory_state", side_effect=PermissionError("EACCES")
    ):
        violations = partitions_module.orphaned_partitions(session)

    assert len(violations) == 1
    assert "refus" in violations[0].detail.lower()
    assert partitions_module.retire_orphaned_partitions(session, violations) == 0
    assert len(session.exec(select(SymbolMapPartition)).all()) == 1


def test_liveness_is_decided_by_directory_state_and_not_by_a_weaker_probe():
    """`git worktree list` cannot see a plain checkout, and `Path.exists()`
    turns 'the kernel will not say' into 'there is nothing here'."""
    source = (PACKAGE_ROOT / "partitions.py").read_text(encoding="utf-8")

    assert "directory_state" in source
    assert "worktree list" not in source
    assert ".exists()" not in source


# ==========================================================================
# AC10 / AC6.6-AC6.7 — a refusal reaches a surface
#
# Addendum artifact `881fcc5c-72c7-4d96-b531-10d8dac293be` (R6 v2). The tests
# above this block assert the detector's RETURN VALUE, and they pass today
# against code whose refusal is written nowhere a person reads: the
# mass-absence arm returns silently, and `invariants.py:231-238` emits only
# "repaired 0 of 1" for an EXPECTED-with-repair invariant. So these tests
# drive the refusal through `services.invariants.run_invariant` — the
# framework call, per assumption A11 — and read the reason off what that call
# emits (A10: one `logger.warning` inside `_refusal()`).
#
# "Exactly one record" is the assertion, not "at least one": the easy
# regression is keeping the site WARNING in the filesystem arm AND adding one
# in `_refusal()`, which gives an operator two records for one event.
# ==========================================================================

PARTITIONS_LOGGER = "loregarden.services.symbol_map.partitions"


def _the_invariant():
    return next(inv for inv in INVARIANTS if inv.name == "orphaned_symbol_map_partition")


def _refusal_warnings(caplog) -> list[logging.LogRecord]:
    """Only this package's records. `run_invariant` logs its own WARNING for
    every partial repair ("repaired 0 of 1"), which is the count line this
    criterion exists because of — counting it would let the generic line
    satisfy the assertion."""
    return [
        r for r in caplog.records if r.name == PARTITIONS_LOGGER and r.levelno == logging.WARNING
    ]


def test_the_mass_absence_refusal_is_observable_through_run_invariant(
    session, workspace, tmp_path, caplog
):
    """AC6.6 / AC6.7, mass-absence arm. Two partitions registered, both roots
    removed: the pass refuses, deletes nothing, and says so at WARNING naming
    THIS cause — 'they are all gone' calls for a different operator action
    (a database opened somewhere unexpected) than 'I could not tell'."""
    _register_partition(session, workspace.id, tmp_path / "ghost-one")
    _register_partition(session, workspace.id, tmp_path / "ghost-two")

    with caplog.at_level(logging.WARNING):
        check = run_invariant(session, _the_invariant())

    assert check.found == 1
    assert check.repaired == 0
    assert len(session.exec(select(SymbolMapPartition)).all()) == 2

    records = _refusal_warnings(caplog)
    assert len(records) == 1, [r.getMessage() for r in records]
    message = records[0].getMessage()
    assert "all 2 registered checkouts resolved absent at once" in message


def test_the_filesystem_refusal_is_observable_through_run_invariant(
    session, workspace, mapped_repo, caplog
):
    """AC6.6 / AC6.7, filesystem arm. The record names the checkout_root it
    could not resolve, and does NOT read as a mass disappearance: an EIO on
    one mount and every checkout being gone are different incidents."""
    build_symbol_map(session, workspace.id, mapped_repo)
    live = _partition(session, workspace.id, mapped_repo)

    with (
        caplog.at_level(logging.WARNING),
        mock.patch.object(partitions_module, "directory_state", side_effect=OSError("device busy")),
    ):
        check = run_invariant(session, _the_invariant())

    assert check.found == 1
    assert check.repaired == 0
    assert len(session.exec(select(SymbolMapPartition)).all()) == 1

    records = _refusal_warnings(caplog)
    assert len(records) == 1, [r.getMessage() for r in records]
    message = records[0].getMessage()
    assert live.checkout_root in message
    assert "resolved absent at once" not in message


def test_a_pass_that_repairs_rather_than_refusing_logs_no_refusal(
    session, workspace, mapped_repo, tmp_path, caplog
):
    """The other half of AC6.6's count. A WARNING that fires on an ordinary
    retirement is a warning an operator learns to ignore, and the refusal
    then reaches a surface nobody is reading."""
    build_symbol_map(session, workspace.id, mapped_repo)
    orphan = _register_partition(session, workspace.id, tmp_path / "removed-checkout")

    with caplog.at_level(logging.WARNING):
        check = run_invariant(session, _the_invariant())

    assert check.found == 1
    assert check.repaired == 1
    assert orphan.id not in {p.id for p in session.exec(select(SymbolMapPartition)).all()}
    assert _refusal_warnings(caplog) == []


# --------------------------------------------------------------------------
# AC10 / AC6.6-AC6.7, adversarial. The three tests above read one pass of one
# arm and count records from this module's logger. Four ways that passes over
# a refusal an operator still cannot act on:
#
#   1. the log sits at the two CALL SITES rather than in `_refusal()`, so the
#      count is right today and the third arm someone adds next ships silent;
#   2. the message carries a constant rather than the population it judged,
#      so "all 2 …" is right only for the two-partition fixture;
#   3. a `retire_orphaned_partitions` keep-warning — same logger, same level,
#      an ordinary pass — is counted as a refusal, so "exactly one refusal"
#      stops meaning a refusal happened;
#   4. a once-per-process guard added later to cut log spam leaves every pass
#      after the first refusing in silence, which is the defect AC6.6 names.
#
# (3) is why these tests read `_recorded_refusals` rather than every WARNING
# this module emits: a refusal says it refused, in both arms, and the record
# that merely keeps one row does not.
# --------------------------------------------------------------------------


def _module_warnings(caplog) -> list[logging.LogRecord]:
    return [
        r for r in caplog.records if r.name == PARTITIONS_LOGGER and r.levelno == logging.WARNING
    ]


def _recorded_refusals(caplog) -> list[logging.LogRecord]:
    """A refused pass says so, the same way the Violation detail does
    ("refused this pass: …"). `retire_orphaned_partitions` also warns from
    this logger when it keeps a row it could not recheck, and that is not a
    refusal — nothing was refused, one row was left alone."""
    return [r for r in _module_warnings(caplog) if "refus" in r.getMessage().lower()]


def _probe(*, raising: frozenset[str] = frozenset(), absent: frozenset[str] = frozenset()):
    """A `directory_state` stand-in keyed by path, so a test can make ONE
    checkout unreadable while the others answer."""

    def side_effect(path):
        key = str(path)
        if key in raising:
            raise OSError("device busy")
        if key in absent:
            return DirectoryState.ABSENT
        return DirectoryState.PRESENT

    return side_effect


def test_the_refusal_helper_is_the_single_record_site(caplog):
    """AC6.6 names `_refusal()` as the place the reason is recorded, and that
    placement is the whole guard: a log at each call site is indistinguishable
    today and silent on the next arm anyone adds. Calling the helper on its
    own is the only way to tell the two apart."""
    with caplog.at_level(logging.WARNING):
        violations = partitions_module._refusal("the volume did not answer")

    assert len(violations) == 1
    records = _recorded_refusals(caplog)
    assert len(records) == 1, [r.getMessage() for r in _module_warnings(caplog)]
    assert "the volume did not answer" in records[0].getMessage()


def test_the_mass_absence_record_counts_the_checkouts_it_saw(session, workspace, tmp_path, caplog):
    """The arm's record must name the population it judged. With the fixture
    above fixed at two partitions, a message that hardcodes 2 — or counts the
    absent list when a later change makes the two differ — reads as correct
    forever. Three partitions is the cheapest way to ask."""
    for name in ("ghost-one", "ghost-two", "ghost-three"):
        _register_partition(session, workspace.id, tmp_path / name)

    with caplog.at_level(logging.WARNING):
        check = run_invariant(session, _the_invariant())

    assert check.found == 1
    assert check.repaired == 0
    assert len(session.exec(select(SymbolMapPartition)).all()) == 3

    records = _recorded_refusals(caplog)
    assert len(records) == 1, [r.getMessage() for r in _module_warnings(caplog)]
    assert "all 3 registered checkouts resolved absent at once" in records[0].getMessage()


def test_the_filesystem_record_names_the_checkout_that_would_not_answer(
    session, workspace, mapped_repo, tmp_path, caplog
):
    """The arm's test above makes EVERY probe raise, so naming the first row
    and naming the raising row are the same answer there. One live checkout
    beside one unreadable one separates them: an operator given the wrong
    path goes and looks at a mount that is fine."""
    build_symbol_map(session, workspace.id, mapped_repo)
    live = _partition(session, workspace.id, mapped_repo)
    unreadable = _register_partition(session, workspace.id, tmp_path / "busy-mount")

    with (
        caplog.at_level(logging.WARNING),
        mock.patch.object(
            partitions_module,
            "directory_state",
            side_effect=_probe(raising=frozenset({unreadable.checkout_root})),
        ),
    ):
        check = run_invariant(session, _the_invariant())

    assert check.found == 1
    assert check.repaired == 0
    assert len(session.exec(select(SymbolMapPartition)).all()) == 2

    records = _recorded_refusals(caplog)
    assert len(records) == 1, [r.getMessage() for r in _module_warnings(caplog)]
    message = records[0].getMessage()
    assert unreadable.checkout_root in message
    assert live.checkout_root not in message


def test_a_repair_that_cannot_recheck_is_not_recorded_as_a_refusal(
    session, workspace, mapped_repo, tmp_path, caplog
):
    """A volume that goes away BETWEEN detect and repair produces a WARNING
    from this module on a pass that refused nothing — the repair kept a row
    it could not recheck. If that record reads as a refusal, the operator is
    told every map may have been at risk when one row was simply left alone,
    and the refusal record stops carrying information."""
    build_symbol_map(session, workspace.id, mapped_repo)
    orphan = _register_partition(session, workspace.id, tmp_path / "removed-checkout")
    probes = {"n": 0}

    def absent_then_unreadable(path):
        if str(path) != orphan.checkout_root:
            return DirectoryState.PRESENT
        probes["n"] += 1
        if probes["n"] == 1:
            return DirectoryState.ABSENT
        raise OSError("the volume went away between detect and repair")

    with (
        caplog.at_level(logging.WARNING),
        mock.patch.object(partitions_module, "directory_state", side_effect=absent_then_unreadable),
    ):
        check = run_invariant(session, _the_invariant())

    assert probes["n"] == 2
    assert check.found == 1
    assert check.repaired == 0
    assert {orphan.id} <= {p.id for p in session.exec(select(SymbolMapPartition)).all()}

    # Kept, not refused — and not silent either.
    assert _recorded_refusals(caplog) == []
    assert [r.getMessage() for r in _module_warnings(caplog)]


def test_a_second_pass_records_the_refusal_again(session, workspace, tmp_path, caplog):
    """The reconcile pass runs on a timer, and a refusal is a condition that
    persists until someone acts on it. A once-per-process guard added later to
    quieten the log would leave every pass after the first refusing in silence
    — which is exactly the state AC6.6 exists to end, reintroduced as a
    performance tidy-up."""
    _register_partition(session, workspace.id, tmp_path / "ghost-one")
    _register_partition(session, workspace.id, tmp_path / "ghost-two")

    with caplog.at_level(logging.WARNING):
        first = run_invariant(session, _the_invariant())
        second = run_invariant(session, _the_invariant())

    assert (first.found, first.repaired) == (1, 0)
    assert (second.found, second.repaired) == (1, 0)
    assert len(session.exec(select(SymbolMapPartition)).all()) == 2
    assert len(_recorded_refusals(caplog)) == 2, [r.getMessage() for r in _module_warnings(caplog)]


def test_a_pass_with_nothing_registered_records_nothing(session, workspace, caplog):
    """The state 281 actually ships in. 'No population to judge' is not a
    refusal, and a record there would fire on every reconcile pass in every
    workspace from the day this lands until something builds a map."""
    with caplog.at_level(logging.WARNING):
        check = run_invariant(session, _the_invariant())

    assert check.found == 0
    assert _module_warnings(caplog) == []


# ==========================================================================
# AC12 — structural assertions: one process seam, no trigger, no surface
# ==========================================================================


def test_only_sources_shells_git():
    """R3.1. Eleven-plus sites in `loregarden/` spell `rev-parse HEAD` their
    own way; inside this package there is one."""
    call_sites = {
        module.name: module.read_text(encoding="utf-8").count("run_git(")
        for module in _package_modules()
    }

    assert {name for name, count in call_sites.items() if count} == {"sources.py"}


def test_every_git_call_in_the_package_passes_an_explicit_cwd():
    """AC12 / AC3.6. Verified: `run_git` defaults `cwd=None`, i.e. the
    process's cwd — and both `GIT_DIR` and an ambient cwd override intent."""
    source = (PACKAGE_ROOT / "sources.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)  # py-org: allow-isinstance - ast node dispatch
        and getattr(node.func, "id", getattr(node.func, "attr", "")) == "run_git"
    ]

    assert calls, "sources.py is the module that shells git"
    for call in calls:
        cwd = next((kw for kw in call.keywords if kw.arg == "cwd"), None)
        assert cwd is not None, ast.get_source_segment(source, call)
        assert ast.get_source_segment(source, cwd.value) == "repo_root"


@pytest.mark.parametrize(
    "forbidden",
    [
        "subprocess.run",
        "subprocess.Popen",
        "subprocess.call",
        "subprocess.check_call",
        "subprocess.check_output",
        "os.system",
        "os.popen",
        "os.execv",
        "os.spawnv",
        "importlib.import_module",
    ],
)
def test_no_module_reaches_a_process_by_another_spelling(forbidden):
    """AC12 / AC3.8. A single-module process seam is satisfiable while still
    spawning one by a name a static import walk cannot see."""
    for module in _package_modules():
        assert forbidden not in module.read_text(encoding="utf-8"), module.name


FORBIDDEN_IMPORTS = (
    "loregarden.mcp",
    "loregarden.api",
    "loregarden.cli",
    "loregarden.services.builtin_orchestrator",
    "loregarden.services.workflow_state",
    "loregarden.services.workflow_routing",
    "loregarden.services.orchestration",
)


@pytest.mark.parametrize("module", _package_modules(), ids=lambda p: p.name)
def test_no_module_in_the_package_can_reach_the_orchestrator(module):
    """A derived index must not be able to move a ticket."""
    for name in imported_names(module.read_text(encoding="utf-8")):
        for forbidden in FORBIDDEN_IMPORTS:
            assert not (name == forbidden or name.startswith(f"{forbidden}.")), (
                f"{module.name} imports {name}"
            )


TRIGGER_SURFACES = (
    Path(settings.repo_root) / "server" / "loregarden" / "services" / "orchestration.py",
    Path(settings.repo_root) / "server" / "loregarden" / "services" / "builtin_orchestrator.py",
    Path(settings.repo_root) / "server" / "loregarden" / "services" / "workflow_state.py",
    Path(settings.repo_root) / "server" / "loregarden" / "services" / "reconciliation.py",
    Path(settings.repo_root) / "server" / "loregarden" / "mcp",
    Path(settings.repo_root) / "server" / "loregarden" / "cli",
    Path(settings.repo_root) / "client" / "src",
    Path(settings.repo_root) / "agent_context" / "orchestration",
    Path(settings.repo_root) / "lefthook.yml",
)


@pytest.mark.parametrize("surface", TRIGGER_SURFACES, ids=lambda p: p.name)
def test_nothing_builds_or_schedules_the_map(surface):
    """AC12 / AC5.9, and the sentence the gate is owed: 281 ships a schema, an
    extractor, five helpers, an invariant and the tests that measure them.
    NOTHING BUILDS THE MAP IN PRODUCTION WHEN 281 LANDS — no MCP tool, no CLI
    noun, no git hook, no scheduler entry. Those are 282 and 284.

    `services/invariant_registry.py` is deliberately not in this list: the
    invariant entry is the one reference 281 is supposed to add.

    What is forbidden is a BUILD or a schedule, so the needle is the builder's
    own name rather than the package's. 368 reads the map from
    `mcp/memory_tools.py` to annotate a claim with a verdict — a read of
    whatever 284 eventually builds, which cannot make the map exist and
    changes nothing about this sentence.
    """
    if not surface.exists():
        pytest.skip(f"{surface} is not present in this checkout")

    sources = sorted(surface.rglob("*")) if surface.is_dir() else [surface]
    offenders = [
        path.name
        for path in sources
        # A recorded fixture is data, not a caller: client/src/test/fixtures/
        # hive-runs/failing.json quotes a run's `git status` naming symbol_map.py.
        if path.is_file()
        and "__pycache__" not in path.parts
        and "fixtures" not in path.parts
        and "build_symbol_map" in path.read_text(encoding="utf-8", errors="ignore")
    ]

    assert offenders == []


# ==========================================================================
# ADVERSARIAL SUITE (test-break stage)
#
# Everything below this line was added by the test-break stage against the
# suite above. Each test names the specific way a build that passes every
# test above can still be wrong. Grouped by the dimension it attacks rather
# than by the requirement it defends, because the gaps are in the corners
# between requirements.
# ==========================================================================


#: A module whose every definition is decorated, so `lineno` is measured
#: against the `def`/`class` line and not against the first decorator.
#: `ast.FunctionDef.lineno` has been the `def` line since 3.8; a hand-rolled
#: "first line of the node's source" is where it stops being.
DECORATED_MODULE = '''\
"""Decorated shapes."""

import functools

REGISTRY = {}


def register(fn):
    REGISTRY[fn.__name__] = fn
    return fn


@register
@functools.cache
def cached_lookup():
    return 1


@register
class Registered:
    @property
    def value(self):
        return 1

    @staticmethod
    @functools.cache
    def helper():
        return 2
'''

#: (name, kind, line, parent) for DECORATED_MODULE. Every line is the
#: `def`/`class` line, never the decorator above it.
DECORATED_EXPECTED = {
    ("REGISTRY", SymbolKind.CONSTANT, 5, ""),
    ("register", SymbolKind.FUNCTION, 8, ""),
    ("cached_lookup", SymbolKind.FUNCTION, 15, ""),
    ("Registered", SymbolKind.CLASS, 20, ""),
    ("value", SymbolKind.METHOD, 22, "Registered"),
    ("helper", SymbolKind.METHOD, 27, "Registered"),
}

#: Export shapes this repository actually uses, measured at HEAD a4d45f9f8f
#: over `client/src/**`: `export async function` in 15 files, a generic
#: `export function name<T>` in 18, `export default function` in 2,
#: `export abstract class` in 1. A tier that handles only the shapes in
#: `TS_MODULE` above loses every symbol in those files and reports nothing.
TS_REAL_SHAPES = """\
export async function loadThing(): Promise<number> {
  return 1;
}

export function identity<T extends object>(value: T): T {
  return value;
}

export abstract class BaseThing {
  abstract run(): void;
}

export default function Entry(): number {
  return 1;
}

export type Handler<T> = (value: T) => void;

export enum Flag {
  On,
}
"""

TS_REAL_SHAPES_EXPECTED = {
    ("loadThing", SymbolKind.FUNCTION, 1),
    ("identity", SymbolKind.FUNCTION, 5),
    ("BaseThing", SymbolKind.CLASS, 9),
    ("Entry", SymbolKind.FUNCTION, 13),
    ("Handler", SymbolKind.TYPE_ALIAS, 17),
    ("Flag", SymbolKind.ENUM, 19),
}

#: Declarations that are not declarations. A line-oriented regex that does not
#: discard comments matches every one of these and invents six identifiers
#: that exist nowhere — the precise failure the ticket's opening paragraph
#: says a map is built to prevent.
TS_GHOSTS = """\
// export function lineCommented() {}
/**
 * export class DocCommented {}
 * export const DOC_CONSTANT = 1;
 */
/*
export function blockCommented() {}
export interface BlockCommented {}
*/
export const quoted = "export interface InsideAString {}";
export const real = 1;
"""


@pytest.fixture(name="rich_repo")
def rich_repo_fixture(tmp_path: Path) -> Path:
    """Decorated Python, the TypeScript shapes this repo really uses, and the
    declarations that only look like declarations."""
    return from_template(tmp_path / "rich", "test_symbol_map_rich_repo", _build_rich_rich_repo)


def _build_rich_rich_repo(root: Path) -> None:
    root = _init(root)
    _write(root, "pkg/decorated.py", DECORATED_MODULE)
    _write(root, "pkg/stub.pyi", "def stubbed() -> int: ...\n")
    _write(root, "web/shapes.ts", TS_REAL_SHAPES)
    _write(root, "web/ghosts.ts", TS_GHOSTS)
    _commit(root, "feat: the shapes the first fixture does not hold")


@pytest.fixture(name="awkward_paths_repo")
def awkward_paths_repo_fixture(tmp_path: Path) -> Path:
    """Tracked paths whose names git does not print verbatim.

    `git ls-files` C-quotes any path holding a byte above 0x7f, so a tracked
    `pkg_Ω.py` is printed as `"pkg_\\316\\251.py"` — a path that exists on no
    filesystem. Verified in a scratch repository on this machine. A build
    that splits the output into lines and opens what it finds records a
    parse failure for a file that parses, under a path nothing can look up.
    """
    return from_template(
        tmp_path / "awkward",
        "test_symbol_map_awkward_paths_repo",
        _build_awkward_awkward_paths_repo,
    )


def _build_awkward_awkward_paths_repo(root: Path) -> None:
    root = _init(root)
    _write(root, "pkg/ascii.py", "def plain():\n    return 1\n")
    _write(root, "pkg/omega_Ω.py", "def omega():\n    return 1\n")
    _write(root, "web/with space.ts", "export const SPACED = 1;\n")
    _commit(root, "feat: paths git does not print verbatim")


@pytest.fixture(name="unreadable_source_repo")
def unreadable_source_repo_fixture(tmp_path: Path, request: pytest.FixtureRequest) -> Path:
    """Tracked, *supported* paths that cannot be read, and two that are deleted.

    All are reachable without doing anything exotic: deleting a file without
    committing the delete is what every working tree looks like mid-edit, and a
    symlink whose target is gone is an ordinary state of a tree that tracks
    links. `pkg` became a file, so the path under it is gone by another error,
    and `locked` may not be entered, so nothing can say whether its file is.
    """
    root = _init(tmp_path / "unreadable-source")
    _write(root, "survivor.py", "def survivor():\n    return 1\n")
    _write(root, "deleted.py", "def deleted():\n    return 1\n")
    _write(root, "pkg/replaced.py", "def replaced():\n    return 1\n")
    (root / "dangling.ts").symlink_to(root / "nowhere.ts")
    _write(root, "locked/inside.py", "def inside():\n    return 1\n")
    _commit(root, "feat: supported paths that will not read")
    (root / "deleted.py").unlink()
    shutil.rmtree(root / "pkg")
    _write(root, "pkg", "a file where a directory was\n")
    (root / "locked").chmod(0)
    request.addfinalizer(lambda: (root / "locked").chmod(0o755))
    return root


@pytest.fixture(name="duplicate_names_repo")
def duplicate_names_repo_fixture(tmp_path: Path) -> Path:
    """One name defined in three files, plus two paths a SQL `LIKE` confuses.

    `a_b.py` and `axb.py` differ by one character, and that character is
    `LIKE`'s single-character wildcard.
    """
    return from_template(
        tmp_path / "dupes",
        "test_symbol_map_duplicate_names_repo",
        _build_dupes_duplicate_names_repo,
    )


def _build_dupes_duplicate_names_repo(root: Path) -> None:
    root = _init(root)
    for n in range(3):
        _write(root, f"pkg/mod{n}.py", "def calculate_total_price():\n    return 0\n")
    _write(root, "pkg/a_b.py", "def underscored():\n    return 1\n")
    _write(root, "pkg/axb.py", "def crossed():\n    return 1\n")
    _commit(root, "feat: one name, three sites")


@pytest.fixture(name="window_repo")
def window_repo_fixture(tmp_path: Path) -> Path:
    """Names sitting exactly on both edges of R5.5's lossless window.

    For the 13-character query `parse_request` the window is
    [ceil(39/7), floor(91/3)] = [6, 30]. `parse_` is 6 characters and scores
    2*6/19 = 0.63; the 29-character name scores 2*13/42 = 0.62. Both clear
    difflib's 0.6 cutoff, both are inside the window, and both are dropped by
    any narrower window an implementer might reach for instead — a length
    delta of +-5, say, which looks reasonable and silently loses them.
    """
    return from_template(
        tmp_path / "window", "test_symbol_map_window_repo", _build_window_window_repo
    )


def _build_window_window_repo(root: Path) -> None:
    root = _init(root)
    _write(
        root,
        "edges.py",
        "def parse_():\n    return 1\n\n\ndef parse_request_xxxxxxxxxxxxxxx():\n    return 2\n",
    )
    _write(root, "other.py", "def wholly_unrelated_identifier():\n    return 3\n")
    _commit(root, "feat: both window edges")


def _source_line(root: Path, path: str, line: int) -> str:
    return (root / path).read_text(encoding="utf-8").splitlines()[line - 1]


def _declares(text: str, name: str, kind: SymbolKind) -> bool:
    """Whether a line of source really declares `name` as `kind`.

    An oracle independent of the extractor: the row says a symbol is at a
    line, and the file either holds its declaration there or the row is
    wrong. An off-by-one, a decorator line, and a name read out of a comment
    all fail it, and none of them fail an expected-set comparison that was
    written from the same misreading.
    """
    if not re.search(rf"\b{re.escape(name)}\b", text):
        return False
    if kind == SymbolKind.CLASS:
        return bool(re.search(r"\b(class)\b", text))
    if kind in (SymbolKind.FUNCTION, SymbolKind.METHOD):
        return bool(re.search(r"\b(def|function|=>|\()", text))
    if kind == SymbolKind.INTERFACE:
        return "interface" in text
    if kind == SymbolKind.TYPE_ALIAS:
        return bool(re.search(r"\btype\b", text))
    if kind == SymbolKind.ENUM:
        return bool(re.search(r"\benum\b", text))
    return bool(re.search(r"[:=]", text))


# --------------------------------------------------------------------------
# AC4 — the inherited-copy property the identity tests only approach
# --------------------------------------------------------------------------


def test_an_inherited_database_reads_as_absent_from_another_checkout(
    session, workspace, worktree_pair
):
    """AC4's headline sentence, as behaviour rather than as derivation.

    The database is copied into worktrees and scratch dirs, so a partition
    built in the primary travels into every copy. The identity tests above
    prove the two checkouts hash differently; none of them proves that a
    reader in the second checkout therefore says NO_PARTITION. An
    implementation that resolved the partition by workspace alone — one row
    per workspace, the obvious shape — passes every one of them and hands the
    worktree the primary's line numbers, which is the exact failure the sha
    stamp exists to prevent and worse, because the sha would match.
    """
    primary, linked = worktree_pair
    build_symbol_map(session, workspace.id, primary)

    assert map_status(session, workspace.id, linked).freshness == Freshness.NO_PARTITION
    for read in (
        definitions_for(session, workspace.id, linked, "render"),
        outline_for(session, workspace.id, linked, "pkg/widget.py"),
        suggest_names(session, workspace.id, linked, "rendr"),
    ):
        assert read.status.freshness == Freshness.NO_PARTITION
        assert list(read.rows) == []


def test_a_read_does_not_cross_into_another_checkouts_partition(session, workspace, worktree_pair):
    """Two partitions, one workspace, one symbol name. A name lookup missing
    its `partition_id` predicate returns both checkouts' rows, and the caller
    cannot tell which line number belongs to the tree it is looking at —
    `ix_symbol_map_symbol_name` is `(partition_id, name)` for this reason.
    """
    primary, linked = worktree_pair
    build_symbol_map(session, workspace.id, primary)
    build_symbol_map(session, workspace.id, linked)

    from_primary = definitions_for(session, workspace.id, primary, "Widget")
    from_linked = definitions_for(session, workspace.id, linked, "Widget")

    assert [(r.path, r.line) for r in from_primary.rows] == [("pkg/widget.py", 8)]
    assert [(r.path, r.line) for r in from_linked.rows] == [("pkg/widget.py", 10)]
    assert from_primary.status.freshness == from_linked.status.freshness == Freshness.MATCHES


def test_the_identity_ignores_how_the_path_is_spelled(tmp_path, monkeypatch):
    """One checkout reached four ways is one partition. A hash over `str(path)`
    gives four, and the fourth build silently founds a fifth partition rather
    than replacing the first — the row growth the per-checkout key was priced
    against, multiplied by however many spellings the callers use.
    """
    root = _init(tmp_path / "spelling")
    (root / "sub").mkdir()
    canonical = identify(root)

    assert identify(Path(f"{root}/")) == canonical
    assert identify(root / ".") == canonical
    assert identify(root / "sub" / "..") == canonical
    monkeypatch.chdir(tmp_path)
    assert identify(Path("spelling")) == canonical


def test_one_workspace_and_one_identity_can_hold_only_one_partition(session, workspace, tmp_path):
    """AC4 names the UNIQUE key; nothing above asks the database to enforce it.
    Without it a rebuild that inserts before deleting leaves two partitions
    for one checkout and every reader picks one arbitrarily.
    """
    root = _init(tmp_path / "unique")
    _register_partition(session, workspace.id, root)

    session.add(
        SymbolMapPartition(
            workspace_id=workspace.id,
            checkout_identity=identify(root),
            checkout_root=str(Path(os.path.realpath(root))),
            head_sha="1" * 40,
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_one_checkout_under_two_workspaces_keeps_two_partitions(
    session, workspace, other_workspace, mapped_repo
):
    """The key is (workspace, identity), not identity. Two workspaces pointed
    at one tree must not share a partition, and the second build must not
    delete the first's rows on its way in.
    """
    first = build_symbol_map(session, workspace.id, mapped_repo)
    second = build_symbol_map(session, other_workspace.id, mapped_repo)

    assert len(session.exec(select(SymbolMapPartition)).all()) == 2
    assert first.symbol_rows == second.symbol_rows
    for ws in (workspace, other_workspace):
        read = definitions_for(session, ws.id, mapped_repo, "render")
        assert read.status.freshness == Freshness.MATCHES
        assert len(read.rows) == 3


# --------------------------------------------------------------------------
# R3.1 — enumeration against paths and trees that do not cooperate
# --------------------------------------------------------------------------


def test_a_path_git_does_not_print_verbatim_is_still_the_path_on_disk(
    session, workspace, awkward_paths_repo
):
    """Measured, not supposed: `git ls-files` prints `pkg/omega_Ω.py` as
    `"pkg/omega_\\316\\251.py"`, quotes included, unless the call passes `-z`
    or `core.quotePath=false`. Splitting the default output on newlines yields
    a path that cannot be opened, so the file parses nowhere, is recorded as a
    parse failure, and is filed under a name no `outline_for` will ever be
    asked for. Nothing above would notice: every other fixture is ASCII.
    """
    files = tracked_files(awkward_paths_repo)

    assert "pkg/omega_Ω.py" in files
    assert "web/with space.ts" in files
    assert not any(path.startswith('"') or "\\3" in path for path in files)

    report = build_symbol_map(session, workspace.id, awkward_paths_repo)
    partition = _partition(session, workspace.id, awkward_paths_repo)
    rows = _file_rows(session, partition.id)

    assert report.parse_failures == 0
    assert rows["pkg/omega_Ω.py"].parse_status == ParseStatus.PARSED
    assert [r.name for r in _symbols_in(session, partition.id, "pkg/omega_Ω.py")] == ["omega"]
    assert [r.name for r in _symbols_in(session, partition.id, "web/with space.ts")] == ["SPACED"]


def test_an_untracked_or_ignored_file_is_not_in_the_map(session, workspace, mapped_repo):
    """The map's domain is what git tracks. A build that walked the directory
    instead would index `node_modules`, `.venv` and every build artifact —
    tens of thousands of rows per partition, against a design priced at a few
    thousand — and would report symbols for files no commit contains.
    """
    _write(mapped_repo, ".gitignore", "ignored.py\n")
    _write(mapped_repo, "ignored.py", "def ignored_symbol():\n    return 1\n")
    _commit(mapped_repo, "chore: ignore one of them")
    _write(mapped_repo, "untracked.py", "def untracked_symbol():\n    return 1\n")

    build_symbol_map(session, workspace.id, mapped_repo)
    partition = _partition(session, workspace.id, mapped_repo)
    names = {r.name for r in _symbol_rows(session, partition.id)}

    assert set(_file_rows(session, partition.id)) == set(tracked_files(mapped_repo))
    assert "ignored.py" not in _file_rows(session, partition.id)
    assert "untracked.py" not in _file_rows(session, partition.id)
    assert "ignored_symbol" not in names
    assert "untracked_symbol" not in names


@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root enters a mode-000 directory, so the permission cannot prove the rule",
)
def test_a_tracked_source_that_cannot_be_read_is_recorded_rather_than_fatal(
    session, workspace, unreadable_source_repo
):
    """R3.3 proves an *unsupported* file is never opened. A supported one is,
    and git lists paths the filesystem will not produce bytes for: a symlink
    whose target is gone, a directory that may not be entered. An unguarded `read_bytes()` raises `FileNotFoundError` out of the
    build loop, so one mid-edit working tree loses the whole map rather than
    one file — and the failure arrives as a traceback from a cache refresh,
    which is the shape 'No silent failures' asks to be a recorded result.
    """
    report = build_symbol_map(session, workspace.id, unreadable_source_repo)
    partition = _partition(session, workspace.id, unreadable_source_repo)
    rows = _file_rows(session, partition.id)

    assert [r.name for r in _symbols_in(session, partition.id, "survivor.py")] == ["survivor"]
    for path in ("dangling.ts", "locked/inside.py"):
        assert rows[path].parse_status == ParseStatus.PARSE_FAILED, path
        assert rows[path].parse_error, path
        assert _symbols_in(session, partition.id, path) == []
    assert report.parse_failures == 2
    assert report.files_seen == len(rows)


def test_a_file_deleted_but_not_committed_is_absent_and_not_a_parse_failure(
    session, workspace, unreadable_source_repo
):
    """The refresh calls such a path deleted and holds no row for it. A full
    build that kept one answered differently about the same tree, and counted a
    file nobody can read as one that failed to parse."""
    build_symbol_map(session, workspace.id, unreadable_source_repo)
    partition = _partition(session, workspace.id, unreadable_source_repo)

    assert not {"deleted.py", "pkg/replaced.py"} & set(_file_rows(session, partition.id))


def test_a_full_build_and_a_refresh_agree_on_a_tree_with_deletions(session, workspace, tmp_path):
    root = _init(tmp_path / "agree")
    _write(root, "kept.py", "def kept():\n    return 1\n")
    _write(root, "gone.py", "def gone():\n    return 1\n")
    _write(root, "gone.bin", "unsupported")
    _commit(root, "feat: three files")
    build_symbol_map(session, workspace.id, root)
    partition = _partition(session, workspace.id, root)
    (root / "gone.py").unlink()
    (root / "gone.bin").unlink()

    refresh_module.refresh_symbol_map(session, workspace.id, root)
    refreshed = set(_file_rows(session, partition.id))
    report = build_symbol_map(session, workspace.id, root)

    assert set(_file_rows(session, partition.id)) == refreshed == {"kept.py"}
    assert (report.files_seen, report.unsupported_files, report.parse_failures) == (1, 0, 0)


def test_a_tree_with_no_supported_source_is_an_empty_map_not_a_missing_one(
    session, workspace, tmp_path
):
    """Zero symbols is a legitimate answer and must not be spelled the same
    way as 'never built'. A build that declines to write a partition when it
    extracted nothing makes every later read say NO_PARTITION, and 282's
    not-found channel then reports 'no map' for a workspace that has one.
    """
    root = _init(tmp_path / "docs-only")
    _write(root, "README.md", "# docs\n")
    _write(root, "notes.txt", "notes\n")
    _commit(root, "docs: no source at all")

    report = build_symbol_map(session, workspace.id, root)

    assert report.files_seen == 2
    assert report.symbol_rows == 0
    assert report.unsupported_files == 2
    assert map_status(session, workspace.id, root).freshness == Freshness.MATCHES
    read = definitions_for(session, workspace.id, root, "anything")
    assert read.status.freshness == Freshness.MATCHES
    assert list(read.rows) == []


def test_a_repository_with_no_commits_never_stores_a_sha_that_reads_as_fresh(
    session, workspace, tmp_path
):
    """An unborn HEAD is the state of every repository between `git init` and
    its first commit, and `git rev-parse HEAD` exits 128 there. Refusing is a
    correct answer. Storing `""` is not: a partition whose `head_sha` is empty
    compares equal to the next empty read, so the map reports MATCHES for a
    tree it has never seen.
    """
    root = _init(tmp_path / "unborn")
    _write(root, "pkg/thing.py", "def thing():\n    return 1\n")

    try:
        report = build_symbol_map(session, workspace.id, root)
    except Exception as exc:
        assert str(exc), "a refusal has to say what it refused"
        session.rollback()
        assert session.exec(select(SymbolMapPartition)).all() == []
        return

    assert report.head_sha, "an empty sha makes every later read falsely fresh"
    assert _partition(session, workspace.id, root).head_sha == report.head_sha


# --------------------------------------------------------------------------
# R3.4 — Python shapes the first fixture does not hold
# --------------------------------------------------------------------------


def test_a_decorated_definition_is_recorded_at_its_own_line(session, workspace, rich_repo):
    """Nothing in `PY_MODULE` carries a decorator, and this repository is full
    of them. `ast` reports the `def` line; a line taken from the node's source
    segment, or from `decorator_list[0]`, reports the decorator's. Either is
    plausible, both are off by one or more, and a caller sent to `@property`
    instead of the method is sent to a line that is not the definition.
    """
    build_symbol_map(session, workspace.id, rich_repo)
    partition = _partition(session, workspace.id, rich_repo)

    rows = _symbols_in(session, partition.id, "pkg/decorated.py")

    assert {(r.name, r.kind, r.line, r.parent) for r in rows} == DECORATED_EXPECTED


def test_every_python_row_points_at_a_line_that_declares_it(session, workspace, rich_repo):
    """The oracle the expected-sets cannot be: the row claims a line, and the
    file is asked whether that line declares that name. An expected set
    written from the same misreading as the extractor agrees with it; the
    source does not.
    """
    build_symbol_map(session, workspace.id, rich_repo)
    partition = _partition(session, workspace.id, rich_repo)

    checked = 0
    for path in ("pkg/decorated.py", "pkg/stub.pyi"):
        for row in _symbols_in(session, partition.id, path):
            text = _source_line(rich_repo, path, row.line)
            assert _declares(text, row.name, row.kind), f"{path}:{row.line} {row.name!r} {text!r}"
            checked += 1

    assert checked >= len(DECORATED_EXPECTED)


def test_a_pyi_stub_is_extracted_as_python(session, workspace, rich_repo):
    """`detect` maps `.pyi` to PYTHON above; no build test then asks whether a
    stub actually produces rows. A dispatcher keyed on `.py` alone passes the
    first and silently indexes none of the stubs."""
    build_symbol_map(session, workspace.id, rich_repo)
    partition = _partition(session, workspace.id, rich_repo)
    row = _file_rows(session, partition.id)["pkg/stub.pyi"]

    assert row.language == SourceLanguage.PYTHON
    assert row.extractor_tier == ExtractorTier.PYTHON_AST
    assert row.parse_status == ParseStatus.PARSED
    assert [r.name for r in _symbols_in(session, partition.id, "pkg/stub.pyi")] == ["stubbed"]


def test_a_utf8_bom_does_not_make_a_module_unparseable(session, workspace, tmp_path):
    """The sibling of the latin-1 case, and the one that actually occurs in
    the wild. `ast.parse` skips a BOM in bytes; a decode to `str` leaves
    `\\ufeff` in front of the first statement and raises. Verified under this
    suite's 3.11.
    """
    root = _init(tmp_path / "bom")
    _write_bytes(root, "bom.py", b"\xef\xbb\xbfdef after_a_bom():\n    return 1\n")
    _commit(root, "feat: a BOM")

    report = build_symbol_map(session, workspace.id, root)
    partition = _partition(session, workspace.id, root)

    assert report.parse_failures == 0
    assert [r.name for r in _symbols_in(session, partition.id, "bom.py")] == ["after_a_bom"]


def test_the_interpreter_guard_fires_before_anything_is_written(session, workspace, mapped_repo):
    """AC3.7 pins the raise. A guard placed at the end of the build — after
    the rows are computed and the old partition deleted — raises the same
    error and leaves the workspace with no map at all, which is the state the
    guard exists to avoid reaching by a different route.
    """
    with mock.patch.object(sys, "version_info", (3, 9, 18, "final", 0)):
        with pytest.raises(InterpreterTooOldError):
            build_symbol_map(session, workspace.id, mapped_repo)
    session.rollback()

    assert session.exec(select(SymbolMapPartition)).all() == []
    assert session.exec(select(SymbolMapFile)).all() == []
    assert session.exec(select(SymbolMapSymbol)).all() == []
    assert map_status(session, workspace.id, mapped_repo).freshness == Freshness.NO_PARTITION


# --------------------------------------------------------------------------
# R4 — TypeScript shapes this repository really contains, and ghosts
# --------------------------------------------------------------------------


def test_the_export_shapes_this_repository_uses_are_extracted(session, workspace, rich_repo):
    """Counted at HEAD a4d45f9f8f over `client/src/**`: `export async
    function` in 15 files, a generic `export function f<T>` in 18,
    `export default function` in 2, `export abstract class` in 1. `TS_MODULE`
    above holds none of them, so a tier that matches only its shapes passes
    the whole suite and loses the exports of thirty-odd real files — silently,
    because a missing symbol is indistinguishable from an absent one without
    this assertion.
    """
    build_symbol_map(session, workspace.id, rich_repo)
    partition = _partition(session, workspace.id, rich_repo)

    rows = _symbols_in(session, partition.id, "web/shapes.ts")

    assert {(r.name, r.kind, r.line) for r in rows} == TS_REAL_SHAPES_EXPECTED


def test_a_declaration_inside_a_comment_or_a_string_is_not_a_symbol(session, workspace, rich_repo):
    """Five invented identifiers in twelve lines, two of them at column zero
    inside a commented-out block — the shape a reader meets in any file whose
    old implementation is still sitting above the new one. A line-oriented
    regex that does not discard comments reports every one as a definition,
    and `suggest_names` then offers an identifier that exists nowhere — the
    'confidently wrong' answer the ticket's opening paragraph calls worse than
    no map, arriving through the one tier that cannot parse its way out.
    """
    build_symbol_map(session, workspace.id, rich_repo)
    partition = _partition(session, workspace.id, rich_repo)

    names = {r.name for r in _symbols_in(session, partition.id, "web/ghosts.ts")}

    assert names == {"quoted", "real"}


def test_every_typescript_row_points_at_a_line_that_declares_it(session, workspace, rich_repo):
    """The source as oracle, for the tier that has no parser to check itself
    against. An off-by-one in a line counter that walks the file by hand
    survives every expected-set in this suite and sends every TypeScript
    lookup one line wrong.
    """
    build_symbol_map(session, workspace.id, rich_repo)
    partition = _partition(session, workspace.id, rich_repo)

    checked = 0
    for path in ("web/shapes.ts", "web/ghosts.ts"):
        for row in _symbols_in(session, partition.id, path):
            text = _source_line(rich_repo, path, row.line)
            assert _declares(text, row.name, row.kind), f"{path}:{row.line} {row.name!r} {text!r}"
            checked += 1

    assert checked >= len(TS_REAL_SHAPES_EXPECTED)


# --------------------------------------------------------------------------
# R5 — the readers, adversarially
# --------------------------------------------------------------------------


def test_every_reader_says_no_partition_before_any_build(session, workspace, mapped_repo):
    """`map_status` is checked for NO_PARTITION above; the three readers
    are not. A reader that assumes a partition exists raises `NoResultFound`
    out of a code-hits channel on the first query against a fresh workspace,
    which is every workspace once.
    """
    for read in (
        definitions_for(session, workspace.id, mapped_repo, "render"),
        outline_for(session, workspace.id, mapped_repo, "pkg/widget.py"),
        suggest_names(session, workspace.id, mapped_repo, "rendr"),
    ):
        assert read.status.freshness == Freshness.NO_PARTITION
        assert list(read.rows) == []


def test_every_reader_carries_the_mismatch_verdict_after_head_moves(
    session, workspace, mapped_repo
):
    """Only `definitions_for` is exercised against a moved HEAD above. The
    verdict has to travel on all three, or a consumer that reads an outline or
    takes a suggestion gets pre-edit line numbers with no way to know.
    """
    build_symbol_map(session, workspace.id, mapped_repo)
    _write(mapped_repo, "pkg/later.py", "def later():\n    return 1\n")
    _commit(mapped_repo, "feat: move HEAD past the build")

    outline = outline_for(session, workspace.id, mapped_repo, "pkg/widget.py")
    suggested = suggest_names(session, workspace.id, mapped_repo, "rendr")

    assert outline.status.freshness == Freshness.SHA_MISMATCH
    assert outline.rows, "stale rows are still returned, labelled stale"
    assert suggested.status.freshness == Freshness.SHA_MISMATCH
    assert "render" in suggested.rows


def test_suggest_names_agrees_with_a_full_scan_oracle(session, workspace, window_repo):
    """AC8's narrowing is only safe if it is lossless, and 'provably lossless'
    is a claim about an implementation nobody has written yet. This computes
    the unnarrowed answer with `difflib` over every name in the partition and
    demands the narrowed one equal it. A window tightened for speed, a cutoff
    nudged, or a limit applied before ranking all fail here and nowhere else.
    """
    build_symbol_map(session, workspace.id, window_repo)
    partition = _partition(session, workspace.id, window_repo)
    every_name = sorted({row.name for row in _symbol_rows(session, partition.id)})

    read = suggest_names(session, workspace.id, window_repo, "parse_request")

    assert list(read.rows) == difflib.get_close_matches("parse_request", every_name, 5, 0.6)


def test_the_narrowing_window_keeps_the_names_on_its_own_boundaries(
    session, workspace, window_repo
):
    """The two names a plausible-but-wrong window drops. For the 13-character
    query the lossless window is [6, 30]: `parse_` is 6 characters and scores
    0.63, `parse_request_xxxxxxxxxxxxxxx` is 29 and scores 0.62. A window of
    +-5 characters, or one derived from the cutoff with the inequality the
    wrong way round, returns neither and returns no error either.
    """
    build_symbol_map(session, workspace.id, window_repo)

    read = suggest_names(session, workspace.id, window_repo, "parse_request")

    assert set(read.rows) >= {"parse_", "parse_request_xxxxxxxxxxxxxxx"}
    assert "wholly_unrelated_identifier" not in read.rows


def test_suggest_names_offers_each_name_once(session, workspace, duplicate_names_repo):
    """One name defined in three files fills three of the five suggestion
    slots with the same string if the query selects rows rather than distinct
    names — so a caller checking an invented identifier is offered one
    candidate where it asked for five.
    """
    build_symbol_map(session, workspace.id, duplicate_names_repo)

    read = suggest_names(session, workspace.id, duplicate_names_repo, "calculate_total_pric")

    assert read.rows.count("calculate_total_price") == 1
    assert len(read.rows) == len(set(read.rows))


def test_no_reader_treats_its_argument_as_a_pattern(session, workspace, duplicate_names_repo):
    """`_` and `%` are SQL's wildcards and ordinary characters in a Python
    identifier and a path. A `LIKE` anywhere in the lookup turns
    `outline_for("pkg/a_b.py")` into a match on `pkg/axb.py` as well, and a
    name query of `%` into the whole map. The arguments are caller-supplied —
    282 passes query terms an LLM wrote — so this is reachable, not theatrical.
    """
    build_symbol_map(session, workspace.id, duplicate_names_repo)

    outline = outline_for(session, workspace.id, duplicate_names_repo, "pkg/a_b.py")
    assert [r.name for r in outline.rows] == ["underscored"]

    for pattern in ("%", "_", "calculate%", "'"):
        read = definitions_for(session, workspace.id, duplicate_names_repo, pattern)
        assert list(read.rows) == [], pattern
        assert read.status.freshness == Freshness.MATCHES


def test_the_readers_use_the_indexes_the_criteria_pay_for(
    session, workspace, mapped_repo, recorded_sql
):
    """AC3 asks for five indexes that 'cover both access patterns'. Asserting
    the indexes exist does not assert anything uses them: a lookup that filters
    in Python after selecting the partition's rows, or one whose predicate
    column order defeats `(partition_id, name)`, passes the DDL test and scans
    the largest table in the schema on every query term.
    """
    build_symbol_map(session, workspace.id, mapped_repo)
    recorded_sql.clear()

    definitions_for(session, workspace.id, mapped_repo, "render")
    outline_for(session, workspace.id, mapped_repo, "pkg/widget.py")

    over_symbols = [
        (statement, params)
        for statement, params in recorded_sql
        if "symbol_map_symbols" in statement and statement.lstrip().upper().startswith("SELECT")
    ]
    assert over_symbols, "the readers are expected to query the symbol table"
    for statement, params in over_symbols:
        plan = (
            session.connection()
            .exec_driver_sql(f"EXPLAIN QUERY PLAN {statement}", params)
            .fetchall()
        )
        details = [row[-1] for row in plan]
        assert not any(
            re.match(r"SCAN (TABLE )?symbol_map_symbols\b", str(detail)) for detail in details
        ), (statement, details)


def test_two_builds_of_one_unchanged_tree_store_the_same_rows(session, workspace, mapped_repo):
    """Determinism, stated as a property rather than hoped for. Extraction
    order, a set iterated into the insert, or a dict keyed on something
    unstable all produce a map that differs between two runs over identical
    input — and a map that cannot reproduce itself cannot be compared against
    the previous one, which is how 284 decides what to refresh.
    """
    first_report = build_symbol_map(session, workspace.id, mapped_repo)
    first = sorted(
        (r.qualified_name, r.name, r.kind, r.line, r.parent)
        for r in _symbol_rows(session, _partition(session, workspace.id, mapped_repo).id)
    )

    second_report = build_symbol_map(session, workspace.id, mapped_repo)
    second = sorted(
        (r.qualified_name, r.name, r.kind, r.line, r.parent)
        for r in _symbol_rows(session, _partition(session, workspace.id, mapped_repo).id)
    )

    assert first == second
    assert first_report.symbol_rows == second_report.symbol_rows
    assert first_report.head_sha == second_report.head_sha


def test_a_file_with_thousands_of_declarations_is_extracted_completely(
    session, workspace, tmp_path
):
    """The scale the measured repository actually has, in one file: ~47,000
    symbols over 3,028 files is an average of fifteen and a tail far above it.
    A regex that backtracks, a recursion over the AST, or a per-symbol
    round-trip to the database each turn a 2,000-declaration module into a
    test that does not finish — which is how this one fails. No wall-clock
    bound is asserted: an absolute literal passes on an idle machine and
    fails under load (`test_suite_clock_hermeticity.py`), and the failure
    mode worth catching here is non-termination, not slowness.
    """
    root = _init(tmp_path / "wide")
    _write(root, "wide.py", "".join(f"def fn_{n}():\n    return {n}\n\n" for n in range(2_000)))
    _write(root, "wide.ts", "".join(f"export const CONST_{n} = {n};\n" for n in range(2_000)))
    _commit(root, "feat: two thousand of each")

    report = build_symbol_map(session, workspace.id, root)
    partition = _partition(session, workspace.id, root)

    assert report.symbol_rows == 4_000
    assert len(_symbol_rows(session, partition.id)) == 4_000
    last = {r.name: r.line for r in _symbols_in(session, partition.id, "wide.py")}
    assert last["fn_0"] == 1
    assert last["fn_1999"] == 1 + 3 * 1_999


def test_the_retirement_repair_over_an_empty_finding_list_deletes_nothing(
    session, workspace, mapped_repo
):
    """The repair's own boundary case. A repair that enumerates partitions
    itself rather than acting on the violations it is handed deletes on a pass
    that found nothing — including on the refusal path, where the detector
    deliberately returns a finding that is not a partition.
    """
    build_symbol_map(session, workspace.id, mapped_repo)
    before = len(_symbol_rows(session, _partition(session, workspace.id, mapped_repo).id))

    assert partitions_module.retire_orphaned_partitions(session, []) == 0

    assert len(session.exec(select(SymbolMapPartition)).all()) == 1
    assert len(_symbol_rows(session, _partition(session, workspace.id, mapped_repo).id)) == before
