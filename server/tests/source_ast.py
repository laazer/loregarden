"""One parse per source text per test process, shared by the source censuses.

Dozens of tests assert something about every module in the package (every
writer of a column is guarded, every dispatcher asks the check, no module
resolves a base branch of its own) by parsing the whole tree. One full parse
of `loregarden/` costs about 2s of CPU on a quiet machine (several times that
under a loaded pre-push), and each of those tests paid it again.
A worker parses each module once and every census after the first reads the
cached tree.

The cache is keyed on the source text, not the path, so a file edited between
two reads is parsed afresh, and a temporary module a test writes never aliases
the real one. The trees are shared: a caller must not mutate one (no
`NodeTransformer`, no attributes stamped onto nodes). Parse a private copy with
`ast.parse` when a test needs to.
"""

from __future__ import annotations

import ast
import functools
import gc
from pathlib import Path


@functools.cache
def _parse(source: str) -> ast.Module:
    # A parse allocates a node per token and holds no cycles, yet each burst of
    # allocations sets off collector passes over the whole heap: with the
    # collector paused, a full parse of the package takes under half the time.
    was_enabled = gc.isenabled()
    gc.disable()
    try:
        return ast.parse(source)
    finally:
        if was_enabled:
            gc.enable()


def parse_source(source: str, filename: str = "<unknown>") -> ast.Module:
    """The (shared, read-only) tree of ``source``.

    ``filename`` only names the file in a `SyntaxError`; a failed parse is not
    cached, so the second `ast.parse` raises with the name attached.
    """
    try:
        return _parse(source)
    except SyntaxError:
        return ast.parse(source, filename=filename)


def parse_path(path: Path) -> ast.Module:
    """The (shared, read-only) tree of the module at ``path``."""
    return parse_source(path.read_text(encoding="utf-8"), str(path))
