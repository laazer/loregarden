"""The evidence ledger surfaced to a downstream *consumer* agent.

Commit-scoped evidence recorded by upstream stages is otherwise invisible to the
agents that could act on it. This block hands a downstream agent the proof that
already holds for the exact current tree, so it can rely on it instead of
re-deriving — the reviewer that need not re-run a suite already green, the gate
that can see the change was exercised on its real surface.

It is a *consumer* view: producing evidence stays stage-specific (each stage
records what it proved). And it is deliberately withheld from the verifier — a
verifier told what was already concluded becomes a reader of that reasoning
rather than an independent check, the exact failure `build_verify_context`
exists to prevent.
"""

from __future__ import annotations

from pathlib import Path

from loregarden.models.domain import Ticket
from loregarden.services.evidence import evidence_kinds_at_head
from sqlmodel import Session

# What each evidence kind lets a downstream consumer stop redoing, most- to
# least load-bearing. A kind absent here is intentionally not surfaced for reuse.
_LEDGER_LINES: dict[str, str] = {
    "full_suite_green": (
        "The full regression suite passed. Do not re-run it — review the change and run "
        "only fast static checks. If you edit anything, this no longer holds: re-run the "
        "full suite before reporting `pass`."
    ),
    "real_surface": (
        "The change was exercised on the real surface a user touches, with the output "
        "captured — you can rely on that capture rather than re-running it."
    ),
    "test_red_green": "A red-to-green test was captured for this change.",
    "verify_verdict": "An independent verifier already recorded a verdict for this commit.",
}


def build_evidence_ledger(
    session: Session,
    ticket: Ticket,
    repo_root: Path,
    *,
    is_verify: bool,
) -> str:
    """Reusable proof already established for the exact current commit, or "".

    Empty for a verifier (independence must not be primed), and empty when
    nothing is proven for the current clean tree.
    """
    if is_verify:
        return ""
    kinds = evidence_kinds_at_head(session, ticket, repo_root)
    rows = [f"- {_LEDGER_LINES[kind]}" for kind in _LEDGER_LINES if kind in kinds]
    if not rows:
        return ""
    return "\n".join(
        [
            "Already established for the exact current commit (clean working tree). Rely on",
            "these rather than re-deriving them; any edit you make invalidates them.",
            "",
            *rows,
        ]
    )
