"""Log-line builders — pathlib-style ``/`` for tags and mid-dot labels.

``Dot`` joins display segments with `` · `` (empty parts skipped).
``Tag`` / ``SYS`` / ``TOOL`` / … bind a channel and return a ``LogLine`` that
keeps accepting ``/`` for more segments, and compares equal to
``(tag, text)`` tuples so existing call sites and tests stay honest.

Formatting helpers (``shell``, ``kv``, ``clip``, ``Tag.maybe``,
``LogLine.with_body``) keep the stream formatter free of ad-hoc f-strings.
"""

from __future__ import annotations

from collections.abc import Iterator


class Dot:
    """Compose display labels with `` · ``, skipping empty parts.

    >>> str(Dot("codex thread") / "abc")
    'codex thread · abc'
    >>> str(Dot("$ pytest") / "")
    '$ pytest'
    >>> str(Dot("session init") / "haiku")
    'session init · haiku'
    """

    __slots__ = ("_parts",)

    SEP = " · "

    def __init__(self, *parts: object) -> None:
        self._parts: list[str] = []
        for part in parts:
            self._parts.extend(_parts_from(part))

    def __truediv__(self, other: object) -> Dot:
        return Dot(*self._parts, other)

    def __rtruediv__(self, other: object) -> Dot:
        return Dot(other, *self._parts)

    def __str__(self) -> str:
        return self.SEP.join(self._parts)

    def __repr__(self) -> str:
        return f"Dot({', '.join(map(repr, self._parts))})"


class LogLine:
    """A tagged log line: ``SYS / "codex thread" / thread_id`` → ``("SYS", "codex thread · …")``."""

    __slots__ = ("tag", "_parts")

    def __init__(self, tag: str, *parts: object) -> None:
        self.tag = tag
        self._parts: list[str] = []
        for part in parts:
            self._parts.extend(_parts_from(part))

    def __truediv__(self, other: object) -> LogLine:
        return LogLine(self.tag, *self._parts, other)

    def with_body(self, body: object, *, width: int = 2000) -> LogLine:
        """Keep the mid-dot headline, then a clipped newline body (tool stdout)."""
        text = str(body).strip() if body is not None else ""
        if not text:
            return self
        return LogLine(self.tag, f"{self.text}\n{text[:width]}")

    @property
    def text(self) -> str:
        return Dot.SEP.join(self._parts)

    def as_tuple(self) -> tuple[str, str]:
        return self.tag, self.text

    def __iter__(self) -> Iterator[str]:
        yield self.tag
        yield self.text

    def __len__(self) -> int:
        return 2

    def __getitem__(self, index: int) -> str:
        return self.as_tuple()[index]

    def __eq__(self, other: object) -> bool:
        if isinstance(other, LogLine):
            return self.tag == other.tag and self._parts == other._parts
        if isinstance(other, tuple) and len(other) == 2:
            return self.as_tuple() == other
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self.as_tuple())

    def __repr__(self) -> str:
        return f"LogLine({self.tag!r}, {self.text!r})"


class Tag:
    """Channel name that starts a ``LogLine`` via ``/``.

    >>> SYS / "codex turn started"
    LogLine('SYS', 'codex turn started')
    >>> SYS / "codex thread" / "abc"
    LogLine('SYS', 'codex thread · abc')
    """

    __slots__ = ("name",)

    def __init__(self, name: str) -> None:
        self.name = name

    def __truediv__(self, other: object) -> LogLine:
        return LogLine(self.name, other)

    def __call__(self, *parts: object) -> LogLine:
        """``SYS("codex turn started")`` for a single segment with no ``/`` chain."""
        return LogLine(self.name, *parts)

    def maybe(self, text: object) -> LogLine | None:
        """``OUT.maybe(raw)`` — strip and drop blank text instead of an empty line."""
        if text is None:
            return None
        stripped = str(text).strip()
        if not stripped:
            return None
        return LogLine(self.name, stripped)

    def __repr__(self) -> str:
        return f"Tag({self.name!r})"


def _parts_from(part: object) -> list[str]:
    if part is None:
        return []
    if isinstance(part, Dot):
        return list(part._parts)
    if isinstance(part, LogLine):
        return list(part._parts)
    text = str(part)
    if not text:
        return []
    return [text]


def mid_dot(*parts: object) -> str:
    """``str(Dot(...))`` for call sites that do not want the ``/`` form."""
    return str(Dot(*parts))


def clip(text: object, width: int, *, fallback: str = "") -> str:
    """Truncate display text; empty input yields ``fallback``."""
    if text is None:
        return fallback
    clipped = str(text)[:width]
    return clipped if clipped else fallback


def shell(command: object, *, width: int = 180) -> str:
    """Shell-style tool headline: ``$ pytest -q``."""
    return (
        f"$ {clip(str(command).strip() if command is not None else '', width, fallback='command')}"
    )


def size(chars: int) -> str:
    """Human-readable byte count for a shape segment: ``11.2 KB``."""
    if chars < 1024:
        return f"{chars} B"
    return f"{chars / 1024:.1f} KB"


def kv(key: str, value: object, *, empty: str = "—") -> str:
    """Single ``key=value`` segment; blank values become ``empty`` (default em dash)."""
    text = "" if value is None else str(value)
    if not text:
        text = empty
    return f"{key}={text}"


def kv_space(**pairs: object) -> str:
    """Space-joined ``key=value`` group for one mid-dot segment (``in=1 out=2``).

    Trailing underscores on keys are stripped so ``in_=…`` yields ``in=…``.
    """
    return " ".join(kv(key.removesuffix("_"), value, empty="?") for key, value in pairs.items())


# Stream / artifact channel tags used by run_log_stream and friends.
SYS = Tag("SYS")
OUT = Tag("OUT")
#: Model reasoning. Its own channel because a reader that cannot tell reasoning
#: from an answer will quote one as the other.
THINK = Tag("THINK")
TOOL = Tag("TOOL")
RUN = Tag("RUN")
CMD = Tag("CMD")
ERR = Tag("ERR")
OK = Tag("OK")
FAIL = Tag("FAIL")

LogPayload = LogLine | tuple[str, str]
