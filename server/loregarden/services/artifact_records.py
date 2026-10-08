"""Which artifact rows are the platform's own bookkeeping, not work output.

The ticket artifact feed is every row in `artifacts`, and on a ticket that has
been through a workflow roughly half of them are machinery: one dispatch marker
per stage pass, one "Run context" row per run, one pointer per run to its log
storage. An operator asking "what did the agents produce?" has to read past all
of it — 59 of 127 rows on lg-initiatives-cross-755. The producers live here so
the feed and the writers agree on what a system record is by construction,
rather than by a reader matching titles it copied.
"""

from __future__ import annotations

from loregarden.models.domain import ArtifactKind, StageBudgetArtifactKind

#: Title of the per-run execution summary the CLI executor writes.
RUN_CONTEXT_ARTIFACT_TITLE = "Run context"

_STAGE_BUDGET_KINDS = frozenset(StageBudgetArtifactKind)


def run_log_artifact_title(run_code: str) -> str:
    """Title of the row that points at a run's stored log lines."""
    return f"Run {run_code}"


def is_system_record(
    *,
    kind: str,  # py-org: allow-string - artifacts.kind is open: agents coin their own kinds (~27 live)
    title: str,
    run_code: str | None,
) -> bool:
    """True for a row the platform writes to run itself, not as a result.

    `run_code` is the code of the row's own run, when it has one: a run-log
    pointer is recognised by naming its own run, so an agent titling an
    attachment "Run notes" is never mistaken for one.
    """
    if kind in _STAGE_BUDGET_KINDS:
        return True
    if kind == ArtifactKind.CONTEXT and title == RUN_CONTEXT_ARTIFACT_TITLE:
        return True
    return (
        kind == ArtifactKind.LOG
        and run_code is not None
        and title == run_log_artifact_title(run_code)
    )
