"""Refuse any memory store the suite points at the developer's real vault.

`isolated_memory_store` redirects `settings`, which covers everything that
resolves its path through `from_settings`. It does not cover a store handed a
path directly, and it did not exist at all until after the damage: the MCP smoke
test wrote 446 notes into a real iCloud Obsidian vault — 100% of its BlogPosts,
65% of its Memory notes, 68% of its Learnings — where they then competed with
real notes for the briefing's five recall slots.

That leak was invisible for two days and two months of stale residue because
nothing failed. A fixture that redirects is a default; this is the assertion, so
the next one is a red test on the first run instead of a directory listing
someone happens to read later.

Deliberately not a "must be under tmp" rule. A test may hold a path from
`tempfile`, from a fixture factory, or from a fake root, and none of those are
what went wrong. The invariant is narrower and exactly what was violated: no
store, however it got its path, may address the real vault or the iCloud root.
"""

from __future__ import annotations

from pathlib import Path

from loregarden.config import settings
from loregarden.services.memory_config import read_local_memory_config
from loregarden.services.path_resolve import detect_icloud_root, expand_path

#: Config keys naming a root the suite must never write inside.
FORBIDDEN_CONFIG_KEYS = ("obsidian_vault_dir", "icloud_root")


def forbidden_memory_roots() -> tuple[Path, ...]:
    """The real vault and iCloud root, read from disk and from autodetection.

    Read through `read_local_memory_config`, which reads
    `data/memory.local.json`, rather than through `settings` — `settings` is what
    `isolated_memory_store` has already redirected, so asking it what the real
    vault is would answer with the temporary one and the guard would forbid
    nothing.

    Empty on a machine with no vault configured, CI among them, where there is
    nothing to protect. `test_memory_guard.py` exercises the rejection directly
    so the logic is covered where the roots are not.
    """
    local = read_local_memory_config()
    roots = [
        expand_path(raw, repo_root=settings.repo_root)
        for key in FORBIDDEN_CONFIG_KEYS
        if (raw := (local.get(key) or "").strip())
    ]
    detected = detect_icloud_root()
    if detected:
        roots.append(detected)
    # dict.fromkeys rather than a set: order is what the failure message reads.
    return tuple(dict.fromkeys(roots))


def reject_if_forbidden(label: str, path: Path | str, roots: tuple[Path, ...]) -> None:
    """Raise if `path` is one of `roots` or sits inside one.

    `AssertionError`, not a custom exception: this is a test-suite invariant
    being violated, and it should read as the failed assertion it is.
    """
    resolved = Path(path).expanduser().resolve()
    for root in roots:
        if resolved == root or root in resolved.parents:
            raise AssertionError(
                f"{label} was pointed at {resolved}, inside the real memory root {root}. "
                "The suite must never write there — it left 446 notes in a live Obsidian "
                "vault once. Use the `isolated_memory_store` fixture's vault, or a tmp_path."
            )
