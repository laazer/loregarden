"""The licence classifier, and the gate that runs it before anything else is read."""

from __future__ import annotations

import os
from pathlib import Path
from unittest import mock

import pytest
from loregarden.models.domain import Approval, Ticket
from loregarden.models.domain.survey_enums import (
    LicenceBasis,
    LicenceKind,
    LicenceUse,
    SurveyPassMode,
    SurveyPassStatus,
    SurveySourceKind,
    SurveySourceStatus,
)
from loregarden.models.domain.survey_tables import SurveyCoverage, SurveyPass, SurveySource
from loregarden.services.reference_survey import feeder
from loregarden.services.reference_survey.licence import (
    MAX_LICENCE_BYTES,
    classify_tree,
    expression_reading,
    recorded_verdict,
)
from sqlmodel import Session, select
from tests.survey_support import MIT_TEXT, build_world, write_tree

GPL3 = "GNU GENERAL PUBLIC LICENSE\nVersion 3, 29 June 2007\n" + "terms " * 50
AGPL3 = "GNU AFFERO GENERAL PUBLIC LICENSE\nVersion 3\n" + "GNU General Public License " * 5
POLYFORM_NC = "# PolyForm Noncommercial License 1.0.0\n" + "terms " * 50
FSL = "Functional Source License, Version 1.1, MIT Future License\n" + "terms " * 50


@pytest.mark.parametrize(
    "files, kind, use, basis",
    [
        ({"LICENSE": MIT_TEXT}, LicenceKind.OPEN, LicenceUse.FULL, LicenceBasis.FILE_AT_COMMIT),
        (
            {"COPYING": GPL3},
            LicenceKind.LIMITED,
            LicenceUse.PATTERNS_ONLY,
            LicenceBasis.FILE_AT_COMMIT,
        ),
        (
            {"LICENSE.md": AGPL3},
            LicenceKind.LIMITED,
            LicenceUse.PATTERNS_ONLY,
            LicenceBasis.FILE_AT_COMMIT,
        ),
        (
            {"LICENSE": POLYFORM_NC},
            LicenceKind.LIMITED,
            LicenceUse.EXCLUDED,
            LicenceBasis.FILE_AT_COMMIT,
        ),
        ({"LICENSE": FSL}, LicenceKind.LIMITED, LicenceUse.EXCLUDED, LicenceBasis.FILE_AT_COMMIT),
        (
            {"licence.txt": "All yours, sort of. Ask me first.\n"},
            LicenceKind.CUSTOM,
            LicenceUse.EXCLUDED,
            LicenceBasis.FILE_AT_COMMIT,
        ),
        # The `tau` case: a manifest claims MIT and no licence file exists.
        (
            {"package.json": '{"license": "MIT"}'},
            LicenceKind.OPEN,
            LicenceUse.PATTERNS_ONLY,
            LicenceBasis.MANIFEST,
        ),
        ({"README.md": "hi"}, LicenceKind.NONE, LicenceUse.PATTERNS_ONLY, None),
        (
            {"LICENSES/MIT.txt": MIT_TEXT, "LICENSE": GPL3},
            LicenceKind.LIMITED,
            LicenceUse.PATTERNS_ONLY,
            LicenceBasis.FILE_AT_COMMIT,
        ),
        (
            {
                "src/x.py": "# SPDX-License-Identifier: MIT",
                "LICENSE": "SPDX-License-Identifier: MIT OR Apache-2.0\n",
            },
            LicenceKind.OPEN,
            LicenceUse.FULL,
            LicenceBasis.FILE_AT_COMMIT,
        ),
    ],
)
def test_classifies_trees(tmp_path, files, kind, use, basis):
    verdict = classify_tree(write_tree(tmp_path, files))
    assert (verdict.kind, verdict.use, verdict.basis) == (kind, use, basis)
    assert verdict.constraint


@pytest.mark.parametrize(
    "expr, kind, excluded",
    [
        ("MIT OR GPL-3.0-only", LicenceKind.OPEN, False),
        ("MIT AND GPL-3.0-only", LicenceKind.LIMITED, False),
        ("(Apache-2.0 OR MIT) AND PolyForm-Noncommercial-1.0.0", LicenceKind.LIMITED, True),
        ("GPL-2.0-or-later WITH Classpath-exception-2.0", LicenceKind.LIMITED, False),
        ("LicenseRef-Proprietary", LicenceKind.CUSTOM, False),
        ("GPL", LicenceKind.LIMITED, False),
        ("APACHE-2.0", LicenceKind.OPEN, False),
        ("**MIT**", LicenceKind.OPEN, False),
        ("AGPL", LicenceKind.LIMITED, False),
        ("((MIT))", LicenceKind.OPEN, False),
    ],
)
def test_expressions_are_resolved_not_string_matched(expr, kind, excluded):
    reading = expression_reading(expr)
    assert reading.kind is kind
    assert reading.excluding is excluded


def test_a_permissive_looking_text_with_terms_of_its_own_fails_closed(tmp_path):
    hostile = MIT_TEXT + "\nThe Software may not be used for commercial purposes.\n"
    verdict = classify_tree(write_tree(tmp_path, {"LICENSE": hostile}))
    assert verdict.kind is LicenceKind.CUSTOM
    assert verdict.use is LicenceUse.EXCLUDED


def test_an_oversized_licence_file_is_refused_and_named(tmp_path):
    write_tree(tmp_path, {"LICENSE": "x" * (MAX_LICENCE_BYTES + 1)})
    verdict = classify_tree(tmp_path)
    assert verdict.kind is LicenceKind.CUSTOM
    assert verdict.files_read == ()
    assert any("over" in r for r in verdict.refused)


def test_a_licence_file_that_escapes_the_tree_is_refused(tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text(MIT_TEXT)
    tree = tmp_path / "tree"
    tree.mkdir()
    os.symlink(outside, tree / "LICENSE")
    verdict = classify_tree(tree)
    assert verdict.kind is LicenceKind.CUSTOM
    assert verdict.use is LicenceUse.EXCLUDED
    assert verdict.files_read == ()
    assert verdict.refused == ("LICENSE (resolves outside the tree)",)


def test_a_hand_recorded_licence_is_the_weakest_basis():
    verdict = recorded_verdict("GPL-3.0-only")
    assert (verdict.basis, verdict.use) == (LicenceBasis.RECORDED_BY_HAND, LicenceUse.PATTERNS_ONLY)
    assert recorded_verdict("none").kind is LicenceKind.NONE


def test_the_gate_refuses_a_non_permissive_source_before_any_other_file_is_read(
    isolated_db, tmp_path
):
    """Only the licence file is opened; the tree is never mapped; no carrier exists."""
    tree = write_tree(
        tmp_path / "clone",
        {"LICENSE": POLYFORM_NC, "src/secret.py": "print('x')", "docs/a.md": "a", "README.md": "r"},
    )
    with Session(isolated_db) as session:
        world = build_world(session)
        source = SurveySource(
            workspace_id=world.workspace_id, kind=SurveySourceKind.REPO, url="https://github.com/acme/nc",
            name="nc", question="q", queued_reason="r", status=SurveySourceStatus.ACTIVE,
        )  # fmt: skip
        session.add(source)
        session.commit()
        session.add(
            SurveyPass(source_id=source.id, ordinal=1, mode=SurveyPassMode.INITIAL, question="q")
        )
        session.commit()
        tickets_before = len(session.exec(select(Ticket)).all())

        opened: list[str] = []
        real_open = Path.open

        def spy_open(self, *args, **kwargs):
            opened.append(str(self))
            return real_open(self, *args, **kwargs)

        with (
            mock.patch.object(feeder, "clone_or_refresh", return_value=(tree, "main", "f" * 40)),
            mock.patch.object(feeder, "map_tree") as map_tree,
            mock.patch.object(Path, "open", spy_open),
        ):
            result = feeder.feed_once(session)

        assert result.refused is not None
        map_tree.assert_not_called()
        assert [Path(p).name for p in opened] == ["LICENSE"]
        session.expire_all()
        refused_pass = session.exec(
            select(SurveyPass).where(SurveyPass.source_id == source.id)
        ).one()
        assert refused_pass.status is SurveyPassStatus.REFUSED
        assert "PolyForm" in refused_pass.refused_reason
        assert session.get(SurveySource, source.id).status is SurveySourceStatus.REFUSED
        assert session.exec(select(SurveyCoverage)).all() == []
        assert len(session.exec(select(Ticket)).all()) == tickets_before
        cards = session.exec(
            select(Approval).where(Approval.title.contains("refused by licence"))
        ).all()
        assert len(cards) == 1
