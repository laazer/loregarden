"""Stored directory maps with a directory too big for one pass, and their repair.

beluga's `packages/mac` (895 files) was one leaf of a map taken two levels deep.
`assign_slice` fills a slice to the pass budget, so that leaf was never picked and
five passes read around the repo's whole desktop UI (2026-11-06). The map now
splits a directory larger than the budget (`coverage.map_tree`'s `split_over`),
and the feeder re-takes a stored map that still has one. This is what both use
to find such a map, and what the invariant registry repairs from: the local
clone, when it still sits at the commit the map was taken at.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from loregarden.config import settings
from loregarden.models.domain.survey_enums import LicenceUse
from loregarden.models.domain.survey_tables import SurveySource
from loregarden.services.git_subprocess import run_git
from loregarden.services.reference_repo_service import clone_path_for, parse_repo_url
from loregarden.services.reference_survey.coverage import map_tree, oversized_directory_leaves
from loregarden.services.reference_survey.passes import coverage_rows, store_map
from sqlmodel import Session, col, select

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class UnsplitMap:
    source: SurveySource
    #: The oversized directory leaves, by path.
    leaves: list[str]


def unsplit_leaves(session: Session, source: SurveySource, budget: int) -> list[str]:
    """The stored map's directory leaves over `budget` that a fresh map would split."""
    stored = {
        row.path: row.file_count or 0
        for row in coverage_rows(session, source.id)
        if row.in_map and row.mapped_commit == source.mapped_commit
    }
    return oversized_directory_leaves(stored, budget)


def unsplit_maps(session: Session) -> list[UnsplitMap]:
    """Every mapped, admitted source whose map still has such a leaf."""
    budget = settings.reference_survey_pass_file_budget
    sources = session.exec(
        select(SurveySource).where(
            SurveySource.mapped_commit != "",
            col(SurveySource.licence_use) != LicenceUse.EXCLUDED,
        )
    ).all()
    found: list[UnsplitMap] = []
    for source in sources:
        leaves = unsplit_leaves(session, source, budget)
        if leaves:
            found.append(UnsplitMap(source, leaves))
    return found


def remap_from_clone(session: Session, source: SurveySource) -> bool:
    """Re-take the map from the local clone, split, at the commit it was taken at.

    False, with the reason logged, when the clone is gone or has moved on: the
    map must describe `mapped_commit`, so a clone at another commit cannot
    repair it. The feeder re-maps at the source's next dispatch either way.
    """
    budget = settings.reference_survey_pass_file_budget
    if not unsplit_leaves(session, source, budget):
        return False  # re-checked: repaired since it was detected
    clone = clone_path_for(parse_repo_url(source.url))
    if not clone.is_dir():
        logger.warning(
            "Map of %s not re-split: no local clone at %s; the next dispatch re-maps it",
            source.name,
            clone,
        )
        return False
    head = run_git(["rev-parse", "HEAD"], cwd=clone, capture_output=True, text=True, check=False)
    if head.returncode != 0:
        logger.warning(
            "Map of %s not re-split: cannot read the clone's HEAD (%s)",
            source.name,
            head.stderr.strip(),
        )
        return False
    if head.stdout.strip() != source.mapped_commit:
        logger.warning(
            "Map of %s not re-split: the clone is at %s, the map at %s; the next "
            "dispatch re-maps it at the new commit",
            source.name,
            head.stdout.strip()[:12],
            source.mapped_commit[:12],
        )
        return False
    store_map(session, source, map_tree(clone, split_over=budget), source.mapped_commit)
    return True
