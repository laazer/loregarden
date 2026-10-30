"""The branch names a ticket, a milestone or a chat thread resolves to.

A leaf: `re` and the domain models, and nothing else in `services`. That is
load-bearing rather than tidy. These names used to live in `git_branch`, and 20
modules imported them from there — among them `worktree_service`,
`worktree_receipt` and `target_branch`, which between them closed a cycle
through `worktree_lifecycle` back to `git_branch`. So the checkout self-repair
could not import the receipt gate at module level, and force-removed the tree
itself instead (lg-external-harness-1452).

No re-export is left in `git_branch`: the posture is `internal`, so every
caller moved here, and a shim would keep the cycle alive at import time.
"""

import re

from loregarden.models.domain import BaxterChatSession, Ticket

_BRANCH_RE = re.compile(r"^[A-Za-z0-9._/-]+$")
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(text: str) -> str:
    slug = _SLUG_RE.sub("-", text.lower()).strip("-")
    return slug[:48]


def default_ticket_branch(ticket: Ticket) -> str:
    slug = ticket.external_id.strip() or ticket.id[:8]
    prefix = _slugify(ticket.milestone) or "loregarden"
    return f"{prefix}/{slug}"


def chat_session_branch(chat_session: BaxterChatSession) -> str:
    """The branch a Home chat thread's acting turns commit to.

    Derived from the thread's own id rather than its title: a title is derived
    from the first message and the operator can rename it, and a branch that
    moves when a conversation is renamed is a branch that loses its commits.
    The title only decorates, so a thread is recognisable in `git branch`.
    """
    slug = _slugify(chat_session.title)
    stem = f"{slug}-{chat_session.id[:8]}" if slug else chat_session.id[:8]
    return f"chat/{stem}"


def resolve_ticket_branch(ticket: Ticket) -> str:
    branch = ticket.branch.strip()
    return branch or default_ticket_branch(ticket)


def validate_branch_name(branch: str) -> None:
    if not branch or not _BRANCH_RE.match(branch):
        raise ValueError(f"Invalid branch name: {branch!r}")
