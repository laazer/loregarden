"""Which dispatch preflight checks an orchestration run has already passed.

`preflight_run` used to ask git about `core.bare` and the git directory before
every stage dispatch — nine times over a nine-stage run, against a repository
whose shape no stage changes (lg-build-verification-847). Those two checks now
run once per orchestration run per repository.

The ledger belongs to one run's executor, not to the process: a new
orchestration run starts with an empty one and checks again, and so does a
dispatch outside any orchestration run. Only passes are kept. A failure parks
the stage for a human, who may fix the checkout and approve the re-run, and that
re-run must look again rather than replay the failure it was parked on.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from loregarden.models.domain import DoctorCheck, DoctorFinding, DoctorStatus

#: Checks of the repository's own shape. Nothing a stage does between two
#: dispatches of one run changes them, so one pass covers the run.
REPO_SHAPE_CHECKS = frozenset({DoctorCheck.GIT_CORE_BARE, DoctorCheck.GIT_WRITABLE})


class PreflightLedger:
    """Repository-shape findings that passed, per (orchestration run, repository)."""

    def __init__(self) -> None:
        self._passed: dict[tuple[str, Path], dict[DoctorCheck, DoctorFinding]] = {}

    def passed(
        self, orchestration_run_id: str | None, repo_root: Path
    ) -> dict[DoctorCheck, DoctorFinding]:
        """The findings this run already passed for `repo_root`; none outside a run."""
        if not orchestration_run_id:
            return {}
        return dict(self._passed.get((orchestration_run_id, repo_root), {}))

    def record(
        self,
        orchestration_run_id: str | None,
        repo_root: Path,
        findings: Iterable[DoctorFinding],
    ) -> None:
        if not orchestration_run_id:
            return
        kept = self._passed.setdefault((orchestration_run_id, repo_root), {})
        for finding in findings:
            if finding.check in REPO_SHAPE_CHECKS and finding.status is DoctorStatus.PASS:
                kept[finding.check] = finding
