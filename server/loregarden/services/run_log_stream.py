"""Persist run logs incrementally so the IDE logs tab updates during execution."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any

from loregarden.db.session import engine
from loregarden.models.domain import AgentRun, Artifact, RunStatus, Ticket
from sqlmodel import Session, select


def format_stream_payload(payload: dict[str, Any]) -> tuple[str, str] | None:
    """Extract a human-readable log line from Claude/Cursor stream-json events."""
    msg_type = payload.get("type", "")

    if msg_type == "assistant":
        message = payload.get("message") or {}
        parts: list[str] = []
        for block in message.get("content") or []:
            if isinstance(block, dict):
                text = block.get("text") or block.get("thinking")
                if text:
                    parts.append(str(text))
        if parts:
            return "OUT", " ".join(parts)

    if msg_type == "content_block_delta":
        delta = payload.get("delta") or {}
        text = delta.get("text") or delta.get("thinking")
        if text:
            return "OUT", str(text)

    if msg_type == "result":
        result = payload.get("result")
        if isinstance(result, str) and result.strip():
            return "OUT", result.strip()
        if isinstance(result, dict):
            text = result.get("text") or result.get("output")
            if text:
                return "OUT", str(text)

    if msg_type == "system":
        subtype = payload.get("subtype") or ""
        if subtype == "init":
            model = payload.get("model") or payload.get("permissionMode")
            if model:
                return "SYS", f"session init · {model}"
        return None

    if msg_type in {"tool_use", "tool_result"}:
        name = payload.get("tool_name") or payload.get("name") or msg_type
        return "TOOL", str(name)[:200]

    text = payload.get("text") or payload.get("message")
    if isinstance(text, str) and text.strip():
        return "OUT", text.strip()

    return None


class RunLogStreamer:
    """Write/update a single live log artifact for an agent run."""

    MAX_LINES = 1000
    MAX_LINE_CHARS = 32000
    MAX_LIVE_CHARS = 64000
    CHUNK_FLUSH_CHARS = 16000

    def __init__(
        self,
        *,
        run_id: str,
        ticket_id: str,
        run_code: str,
        agent_id: str,
        skill_name: str,
    ) -> None:
        self.run_id = run_id
        self.ticket_id = ticket_id
        self.run_code = run_code
        self.agent_id = agent_id
        self.skill_name = skill_name
        self.artifact_id: str | None = None
        self._lines: list[dict[str, str]] = []
        self._live = ""
        self._stream_buffer = ""
        self._last_persist = 0.0

    def _timestamp(self) -> str:
        return datetime.now(timezone.utc).strftime("%H:%M:%S")

    def _hydrate(self) -> None:
        with Session(engine) as session:
            artifact = session.exec(
                select(Artifact).where(
                    Artifact.run_id == self.run_id,
                    Artifact.kind == "log",
                )
            ).first()
            if not artifact:
                return
            self.artifact_id = artifact.id
            content = json.loads(artifact.content_json or "{}")
            self._lines = list(content.get("lines") or [])
            self._live = content.get("live") or ""
            self._stream_buffer = ""

    def _append_chunks(self, tag: str, text: str, *, force: bool = False) -> None:
        text = text.strip()
        if not text:
            return
        offset = 0
        while offset < len(text):
            chunk = text[offset : offset + self.MAX_LINE_CHARS]
            offset += self.MAX_LINE_CHARS
            self._lines.append({"time": self._timestamp(), "tag": tag, "text": chunk})
        if len(self._lines) > self.MAX_LINES:
            self._lines = self._lines[-self.MAX_LINES :]
        now = time.time()
        if (
            force
            or now - self._last_persist >= 0.4
            or tag in {"RUN", "CMD", "ERR", "OK", "FAIL", "TOOL"}
        ):
            self._persist()

    def _prefer_stream_text(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        if len(text) >= len(self._stream_buffer):
            self._stream_buffer = text
        elif text and text not in self._stream_buffer:
            self._stream_buffer += text

    def _flush_stream_buffer(self, *, force: bool = False, keep_remainder: bool = False) -> None:
        text = self._stream_buffer.strip()
        if not text:
            return
        if keep_remainder and len(text) > self.CHUNK_FLUSH_CHARS:
            chunk = text[: self.CHUNK_FLUSH_CHARS]
            self._stream_buffer = text[self.CHUNK_FLUSH_CHARS :]
            self._append_chunks("OUT", chunk, force=force)
            return
        self._append_chunks("OUT", text, force=force)
        self._stream_buffer = ""

    def _maybe_chunk_flush(self, *, force: bool = False) -> None:
        if len(self._stream_buffer) >= self.CHUNK_FLUSH_CHARS:
            self._flush_stream_buffer(force=force, keep_remainder=True)

    def _update_live_from_buffer(self) -> None:
        if not self._stream_buffer:
            return
        self.set_live(self._stream_buffer)

    def start(self, command: str = "") -> None:
        self._hydrate()
        has_run = any(line.get("tag") == "RUN" for line in self._lines)
        if not has_run:
            self.append(
                "RUN", f"{self.agent_id} invoked · skill={self.skill_name or '—'}", force=True
            )
        cmd_updated = False
        if command:
            updated = False
            for line in self._lines:
                if line.get("tag") == "CMD":
                    if line["text"] != command[:300]:
                        line["text"] = command[:300]
                        cmd_updated = True
                    updated = True
                    break
            if not updated:
                self.append("CMD", command[:300], force=True)
                cmd_updated = True
        if cmd_updated:
            self._persist()
        self.set_live("Agent running…")

    def append(self, tag: str, text: str, *, force: bool = False) -> None:
        self._append_chunks(tag, text, force=force)

    def append_stream_line(self, raw_line: str) -> None:
        raw_line = raw_line.strip()
        if not raw_line:
            return
        try:
            payload = json.loads(raw_line)
        except json.JSONDecodeError:
            self._flush_stream_buffer(force=True)
            self.append("OUT", raw_line, force=True)
            return
        if not isinstance(payload, dict):
            self._flush_stream_buffer(force=True)
            self.append("OUT", raw_line, force=True)
            return

        msg_type = payload.get("type", "")
        if msg_type == "content_block_delta":
            delta = payload.get("delta") or {}
            text = delta.get("text") or delta.get("thinking")
            if text:
                self._stream_buffer += str(text)
                self._maybe_chunk_flush()
                self._update_live_from_buffer()
            return

        if msg_type == "assistant":
            message = payload.get("message") or {}
            parts: list[str] = []
            for block in message.get("content") or []:
                if isinstance(block, dict):
                    chunk = block.get("text") or block.get("thinking")
                    if chunk:
                        parts.append(str(chunk))
            if parts:
                self._prefer_stream_text(" ".join(parts))
                snapshot = self._stream_buffer
                self._flush_stream_buffer(force=True)
                self.set_live(snapshot)
            return

        if msg_type == "result":
            result = payload.get("result")
            if isinstance(result, str) and result.strip():
                self._prefer_stream_text(result.strip())
                self._flush_stream_buffer(force=True)
            elif isinstance(result, dict):
                text = result.get("text") or result.get("output")
                if text:
                    self._prefer_stream_text(str(text))
                    self._flush_stream_buffer(force=True)
            self._stream_buffer = ""
            self.set_live("")
            return

        formatted = format_stream_payload(payload)
        if formatted:
            tag, text = formatted
            if tag == "OUT":
                self._prefer_stream_text(text)
                self._flush_stream_buffer(force=True)
                self._update_live_from_buffer()
            else:
                self.append(tag, text)
        elif payload.get("type") not in {"control_request", "sdk_control_request", "ping"}:
            self.set_live(f"{payload.get('type', 'event')}…")

    def set_live(self, text: str) -> None:
        if len(text) > self.MAX_LIVE_CHARS:
            text = text[-self.MAX_LIVE_CHARS :]
        if text == self._live:
            return
        self._live = text
        self._persist()

    def touch(self) -> None:
        """Re-persist the current log snapshot (heartbeat during long waits)."""
        self._update_live_from_buffer()
        self._persist()

    def finalize(self, *, status: RunStatus, stderr: str = "") -> None:
        self._flush_stream_buffer(force=True)
        for line in stderr.strip().splitlines()[:30]:
            self.append("ERR", line, force=False)
        tag = "OK" if status == RunStatus.SUCCEEDED else "FAIL"
        self.append(
            tag, "run completed" if status == RunStatus.SUCCEEDED else "run failed", force=True
        )
        self._stream_buffer = ""
        self._live = ""
        self._persist()

    def _persist(self) -> None:
        content = {"lines": self._lines, "live": self._live or None}
        with Session(engine) as session:
            artifact = None
            if self.artifact_id:
                artifact = session.get(Artifact, self.artifact_id)
            if not artifact:
                artifact = session.exec(
                    select(Artifact).where(
                        Artifact.run_id == self.run_id,
                        Artifact.kind == "log",
                    )
                ).first()
            if artifact:
                artifact.content_json = json.dumps(content)
                artifact.title = f"Run {self.run_code}"
                session.add(artifact)
                self.artifact_id = artifact.id
            else:
                artifact = Artifact(
                    ticket_id=self.ticket_id,
                    run_id=self.run_id,
                    kind="log",
                    title=f"Run {self.run_code}",
                    content_json=json.dumps(content),
                )
                session.add(artifact)
                session.flush()
                self.artifact_id = artifact.id

            ticket = session.get(Ticket, self.ticket_id)
            if ticket:
                ticket.revision += 1
                ticket.updated_at = datetime.now(timezone.utc)
                session.add(ticket)
            session.commit()
        self._last_persist = time.time()


def finalize_run_log_artifact(run: AgentRun, *, status: RunStatus, stderr: str = "") -> None:
    """Ensure live log tails are flushed and cleared when a run completes."""
    streamer = RunLogStreamer(
        run_id=run.id,
        ticket_id=run.ticket_id,
        run_code=run.run_code,
        agent_id=run.agent_id,
        skill_name=run.skill_name or "",
    )
    streamer._hydrate()
    if streamer.artifact_id or streamer._lines:
        streamer.finalize(status=status, stderr=stderr)


def bootstrap_run_log(run: AgentRun) -> RunLogStreamer:
    """Create the live log artifact as soon as a run is scheduled."""
    streamer = RunLogStreamer(
        run_id=run.id,
        ticket_id=run.ticket_id,
        run_code=run.run_code,
        agent_id=run.agent_id,
        skill_name=run.skill_name or "",
    )
    streamer.start("Queuing agent…")
    return streamer
