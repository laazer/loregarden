"""The `chat_waits` table: one outstanding background operation per chat thread."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from loregarden.models.domain.chat_wait_enums import ChatWaitStatus
from loregarden.models.domain.enums import ChatSurface, str_enum_column, utcnow
from sqlalchemy import Index, text
from sqlmodel import Field, SQLModel


class ChatWait(SQLModel, table=True):
    """A detached child a chat turn registered so the control plane can resume."""

    __tablename__ = "chat_waits"
    __table_args__ = (
        Index(
            "ux_chat_waits_outstanding_thread",
            "surface",
            "thread_key",
            unique=True,
            sqlite_where=text("status = 'outstanding'"),
        ),
    )

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    surface: ChatSurface = Field(sa_column=str_enum_column(ChatSurface))
    thread_key: str = Field(index=True)
    origin_run_id: str = Field(foreign_key="agent_runs.id", index=True)
    pid: int
    pid_identity: str
    command: str
    log_path: str
    registered_at: datetime = Field(default_factory=utcnow)
    deadline: datetime
    status: ChatWaitStatus = Field(
        sa_column=str_enum_column(ChatWaitStatus, ChatWaitStatus.OUTSTANDING, index=True)
    )
    continued_index: int | None = None
    # Written before schedule_* so the continuation turn can build its prompt
    # after this process is gone. log_tail is None until that write.
    exit_code: int | None = None
    log_tail: str | None = None
    continuation_run_id: str | None = Field(default=None, foreign_key="agent_runs.id", index=True)
