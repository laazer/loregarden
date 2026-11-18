"""How a checkout names itself to the symbol map (lg-code-knowledge-281).

One derivation, from the filesystem, in one module. The database travels into
linked checkouts and scratch directories, so an identity read out of a row
would travel with it and an inherited copy would claim the originating
checkout's map, line numbers included. Deriving it from the resolved path on
disk is exactly what makes an inherited copy read as absent instead.

The root is the one the caller hands in, resolved. Nothing here searches
upwards for a toplevel: many checkouts here sit inside the primary one, and a
walk towards the filesystem root would hand a linked checkout the primary's
partition — the same bug from the other side.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

#: 64 bits over the ~100 checkout paths this machine holds. The unique key on
#: `symbol_map_partitions` turns a collision into a write failure rather than
#: a silent merge of two checkouts' maps.
_DIGEST_CHARS = 16


def identify(repo_root: Path | str) -> str:
    """A stable 16-hex-character name for the checkout rooted at *repo_root*.

    Resolved, so one checkout reached by four spellings — a trailing slash, a
    `.`, a `..` hop, a relative path, a symlink — is one partition and not
    four.
    """
    resolved = os.path.realpath(repo_root)
    return hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:_DIGEST_CHARS]
