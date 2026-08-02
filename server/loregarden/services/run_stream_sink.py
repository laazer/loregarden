"""Where ``PermissionBridgeRunner`` writes the lines it reads off a live CLI.

The bridge was typed against the only sink there was — ``RunLogStreamer``,
which persists the IDE's log artifact for a ticket run. Chat turns need the
same lines for a different destination (the operator's live thinking panel),
and the log artifact cannot serve them: it is keyed by ticket, and a Home chat
turn has no ticket.

Structural rather than inherited. The two implementations share no state and no
storage — only the four calls the bridge actually makes.
"""

from __future__ import annotations

from typing import Protocol


class RunStreamSink(Protocol):
    """The sink protocol the permission bridge writes to."""

    def append_stream_line(self, raw_line: str) -> None:
        """One raw stream-json line, exactly as the CLI emitted it."""

    def append(self, tag: str, text: str, *, force: bool = False) -> None:
        """A bridge-authored line — a tool decision, a steer, an error."""

    def set_live(self, text: str) -> None:
        """Replace the transient "what is happening now" line."""

    def touch(self) -> None:
        """Nothing arrived; keep whatever is persisted from going stale."""
