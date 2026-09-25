"""The suite's own guard against writing into the developer's real vault.

Tested directly because the guard is inert where it cannot matter: on a machine
with no vault configured — CI, a fresh checkout — `forbidden_memory_roots()` is
empty and the conftest fixture patches nothing. Covering only the live path
would mean the check is exercised on exactly one laptop.
"""

from pathlib import Path
from unittest.mock import patch

import pytest
from loregarden.services.memory_store import MemoryGraphStore, ObsidianMemoryStore
from tests.memory_guard import forbidden_memory_roots, reject_if_forbidden


def test_a_path_inside_a_forbidden_root_is_rejected(tmp_path):
    vault = tmp_path / "Project Vault"
    (vault / "Loregarden" / "Memory").mkdir(parents=True)

    with pytest.raises(AssertionError, match="inside the real memory root"):
        reject_if_forbidden("A store", vault / "Loregarden" / "Memory", (vault,))


def test_the_forbidden_root_itself_is_rejected(tmp_path):
    """The vault directory is what a store is handed, so the root is the common case."""
    vault = tmp_path / "Project Vault"
    vault.mkdir()

    with pytest.raises(AssertionError, match=str(vault)):
        reject_if_forbidden("A store", vault, (vault,))


def test_an_unrelated_path_passes(tmp_path):
    """The rule is "not the real vault", not "must be under tmp" — a test may hold
    a path from `tempfile`, a fixture factory or a fake root, and none of those
    are what went wrong."""
    forbidden = tmp_path / "Project Vault"
    forbidden.mkdir()
    elsewhere = tmp_path / "somewhere-else" / "vault"
    elsewhere.mkdir(parents=True)

    reject_if_forbidden("A store", elsewhere, (forbidden,))


def test_a_sibling_whose_name_extends_the_root_passes(tmp_path):
    """`Vault2` is not inside `Vault`. String prefixing would say it is."""
    forbidden = tmp_path / "Vault"
    forbidden.mkdir()
    sibling = tmp_path / "Vault2"
    sibling.mkdir()

    reject_if_forbidden("A store", sibling, (forbidden,))


def test_the_roots_come_from_disk_not_from_redirected_settings(tmp_path):
    """`isolated_memory_store` has already pointed `settings` at a temporary
    vault, so a guard reading settings would forbid that and protect nothing."""
    real = tmp_path / "iCloud" / "Project Vault"
    real.mkdir(parents=True)

    with patch(
        "tests.memory_guard.read_local_memory_config",
        return_value={"obsidian_vault_dir": str(real), "icloud_root": ""},
    ):
        roots = forbidden_memory_roots()

    assert real.resolve() in roots


def test_both_store_types_refuse_a_forbidden_path(tmp_path):
    """The conftest fixture patches both constructors. This pins that each one
    raises when guarded, so a store added later that skips the guard is visible
    as a gap here rather than as notes in someone's vault."""
    real = tmp_path / "Project Vault"
    real.mkdir()
    roots = (real,)

    real_obsidian = ObsidianMemoryStore.__init__
    real_graph = MemoryGraphStore.__init__

    def guarded_obsidian(self, vault_dir):
        reject_if_forbidden("An Obsidian memory store", vault_dir, roots)
        real_obsidian(self, vault_dir)

    def guarded_graph(self, db_path):
        reject_if_forbidden("A memory graph store", db_path, roots)
        real_graph(self, db_path)

    with (
        patch.object(ObsidianMemoryStore, "__init__", guarded_obsidian),
        patch.object(MemoryGraphStore, "__init__", guarded_graph),
    ):
        with pytest.raises(AssertionError, match="An Obsidian memory store"):
            ObsidianMemoryStore(real)
        with pytest.raises(AssertionError, match="A memory graph store"):
            MemoryGraphStore(real / "Loregarden" / "memory.db")
        # And the same constructors still work on a path that is not forbidden.
        ObsidianMemoryStore(tmp_path / "elsewhere")
        MemoryGraphStore(tmp_path / "elsewhere" / "memory.db")


def test_the_guard_is_live_for_this_suite_when_a_vault_is_configured():
    """On a machine with a vault, the real thing must actually be refused.

    Skipped where there is nothing to protect. Not a redundant copy of the test
    above: that one patches the constructors itself, this one relies on the
    conftest fixture having done it — which is the part that can silently stop
    working.
    """
    roots = forbidden_memory_roots()
    if not roots:
        pytest.skip("no Obsidian vault or iCloud root configured on this machine")

    with pytest.raises(AssertionError, match="inside the real memory root"):
        ObsidianMemoryStore(Path(roots[0]))
