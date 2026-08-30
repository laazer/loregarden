"""Whether a chat rail can act, why not, and what would fix it.

One resolver, read by three consumers that used to disagree:

- the **snapshot**, so the UI can say what mode the next turn is in *before* the
  operator types, rather than after the reply comes back read-only;
- the **prompt**, so Baxter can answer "why can't you edit this?" with the same
  sentence the UI is showing;
- the **turn**, which decides `TurnIntent` from the same call.

Two of these causes — a branch with no worktree, and a bridge turn with no run —
were previously decided inside the turn and never published anywhere. The UI
therefore promised a rail could act and then ran it read-only, which is the
failure this module exists to remove: the mode shown and the mode executed come
from one function or they drift.

``ASIDE_OBSERVER`` is here for completeness and is *not* a fault. A BTW aside is
read-only by design, and the UI labels it as an aside rather than as a problem.
"""

from __future__ import annotations

from dataclasses import dataclass

from loregarden.models.domain.enums import ChatAdvisoryCause, ChatMode
from loregarden.services.agent_turn_runner import adapter_capabilities

#: Causes an operator can actually clear, and how. Anything absent from here is
#: either internal or intended, and the UI must not offer a knob for it — a fix
#: button that cannot fix is worse than no button.
_REMEDIABLE: frozenset[ChatAdvisoryCause] = frozenset(
    {
        ChatAdvisoryCause.ADAPTER_CANNOT_EXECUTE,
        ChatAdvisoryCause.ADAPTER_NEEDS_PERMISSION_BYPASS,
        ChatAdvisoryCause.BRANCH_NOT_CHECKED_OUT,
    }
)

#: What the operator would do about it, in their words. Kept beside the cause so
#: the UI, the prompt and the API cannot describe the same state differently.
_ADVICE: dict[ChatAdvisoryCause, str] = {
    ChatAdvisoryCause.ADAPTER_CANNOT_EXECUTE: (
        "Switch this workspace's agent runtime to one that can run tools — Claude Code "
        "routes approvals through the inbox; Cursor, Codex and LM Studio write directly."
    ),
    ChatAdvisoryCause.ADAPTER_NEEDS_PERMISSION_BYPASS: (
        "This adapter can edit files, but its own approval prompts have nowhere to appear "
        "in a headless run. Set LOREGARDEN_ALLOW_PERMISSION_BYPASS=1 to let it act, or pick "
        "an adapter that routes approvals through the inbox."
    ),
    ChatAdvisoryCause.BRANCH_NOT_CHECKED_OUT: (
        "Check the branch out into a worktree. Baxter needs somewhere for the edits to land."
    ),
    ChatAdvisoryCause.NO_RUN_FOR_APPROVALS: (
        "Nothing to change on your side — this turn had no run to attach approvals to. "
        "Send the message again; if it repeats, it is a bug worth reporting."
    ),
    ChatAdvisoryCause.SURFACE_IS_READ_ONLY: (
        "Nothing to fix — this view answers from the record. Use the ticket or Home "
        "conversation to ask for a change."
    ),
    ChatAdvisoryCause.ASIDE_OBSERVER: (
        "Nothing to fix — an aside is answered by reading the running agent's log, on "
        "purpose. Use Escalate to put the question to the agent itself."
    ),
}


@dataclass(frozen=True)
class ChatModeView:
    """The mode of a rail's next turn, with the reason and the way out.

    ``cause`` and ``reason`` are ``None`` in ``ACT`` mode: there is nothing to
    explain, and an empty string would render as an explanation that says
    nothing.
    """

    mode: ChatMode
    cause: ChatAdvisoryCause | None = None
    reason: str = ""
    advice: str = ""
    remediable: bool = False

    @property
    def can_act(self) -> bool:
        return self.mode is ChatMode.ACT

    def as_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode.value,
            "cause": self.cause.value if self.cause else None,
            "reason": self.reason,
            "advice": self.advice,
            "remediable": self.remediable,
        }


def acting() -> ChatModeView:
    """The rail can change things. No cause, because nothing is wrong."""
    return ChatModeView(mode=ChatMode.ACT)


def advisory(cause: ChatAdvisoryCause, reason: str) -> ChatModeView:
    """An advisory rail, carrying why and what to do about it."""
    return ChatModeView(
        mode=ChatMode.ADVISORY,
        cause=cause,
        reason=reason,
        advice=_ADVICE[cause],
        remediable=cause in _REMEDIABLE,
    )


def resolve_chat_mode(
    adapter: str,
    *,
    branch_checked_out: bool = True,
    has_run_for_approvals: bool = True,
    read_only_surface: bool = False,
) -> ChatModeView:
    """The mode this rail's next turn will run in.

    Ordered by how fundamental the block is, so the operator is told the thing
    they must fix *first* rather than a downstream symptom of it.

    ``branch_checked_out`` and ``has_run_for_approvals`` default True because
    only branch triage can violate them; every other rail leaves them alone.
    """
    if read_only_surface:
        return advisory(
            ChatAdvisoryCause.SURFACE_IS_READ_ONLY,
            "This view answers from the record and does not change the repository.",
        )

    caps = adapter_capabilities(adapter)
    if not (caps.permission_bridge or caps.plan_execute):
        # "Cannot" and "is not allowed to" read identically as plan_execute=False
        # and need opposite advice — one says switch tools, the other says change
        # a setting. Telling an operator their adapter is incapable when it is
        # merely unconfigured sends them to replace a tool that works.
        if caps.requires_permission_bypass:
            return advisory(
                ChatAdvisoryCause.ADAPTER_NEEDS_PERMISSION_BYPASS,
                f"The {adapter} adapter can edit files, but permission bypass is off and it "
                "has no headless way to answer its own approval prompts.",
            )
        return advisory(
            ChatAdvisoryCause.ADAPTER_CANNOT_EXECUTE,
            f"The selected {adapter} adapter cannot execute turns "
            "(no permission bridge or writable oneshot path).",
        )

    if not branch_checked_out:
        return advisory(
            ChatAdvisoryCause.BRANCH_NOT_CHECKED_OUT,
            "This branch is not checked out in a worktree, so there is nowhere for edits to land.",
        )

    if caps.permission_bridge and not has_run_for_approvals:
        return advisory(
            ChatAdvisoryCause.NO_RUN_FOR_APPROVALS,
            "This turn has no agent run to attach approvals to, so the permission bridge "
            "cannot supervise it.",
        )

    return acting()


def aside_mode() -> ChatModeView:
    """A BTW aside: read-only by design, and correct.

    Separate from ``resolve_chat_mode`` because it is not a capability question
    — an aside is read-only however capable the adapter is.
    """
    return advisory(
        ChatAdvisoryCause.ASIDE_OBSERVER,
        "An aside is answered by reading the running agent's log, without touching its run.",
    )
