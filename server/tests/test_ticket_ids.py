"""The shareable ticket id: how it is spelled, and what still resolves to it."""

from __future__ import annotations

import pytest
from loregarden.db.migrations_ticket_ids import m_structured_ticket_ids
from loregarden.models.domain import Ticket, WorkItemType, Workspace
from loregarden.services.ticket_ids import (
    NO_MILESTONE_SEGMENT,
    derive_milestone_code,
    derive_workspace_prefix,
    next_ticket_number,
    parse_ticket_number,
    reissue_in_workspace,
    resolve,
    respell_external_id,
    spell_external_id,
)
from loregarden.services.ticket_service import TicketService
from sqlalchemy import text
from sqlmodel import Session, select
from tests.factories import make_ticket, make_workspace


class TestSpelling:
    def test_prefix_is_the_first_letters_of_the_slug(self):
        assert derive_workspace_prefix("loregarden", taken=frozenset()) == "lor"

    def test_prefix_extends_rather_than_collides(self):
        assert derive_workspace_prefix("loregarden", taken=frozenset({"lor"})) == "lore"

    def test_prefix_falls_back_when_the_slug_has_no_letters(self):
        assert derive_workspace_prefix("---", taken=frozenset()) == "ws"

    def test_prefix_appends_a_digit_when_the_whole_slug_is_taken(self):
        taken = frozenset({"abc", "abcd"})
        assert derive_workspace_prefix("abcd", taken=taken) == "abc2"

    def test_the_seeded_workspace_uses_its_chosen_prefix_not_the_derived_one(
        self, db_session: Session
    ):
        """`lg` is a choice, not a derivation — `derive_workspace_prefix` would
        say `lor`. It is set on the workspace so a fresh install and a migrated
        one spell their ids identically."""
        workspace = db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).first()
        assert workspace is not None
        assert workspace.ticket_prefix == "lg"

    @pytest.mark.parametrize(
        ("title", "expected"),
        [
            ("MCP Gateway completion", "mcp-gateway"),
            ("Queue execution correctness", "queue-execution"),
            # Leading ordinals order a milestone, they do not name it.
            ("M01 — Bootstrap vertical slice", "bootstrap-vertical"),
            ("01_milestone_bootstrap", "bootstrap"),
            ("Track A — MCP Gateway", "mcp-gateway"),
            # Stop words pad a code without narrowing it.
            ("The Hive Workplace Upgrade", "hive-workplace"),
        ],
    )
    def test_milestone_code_drops_ordering_noise(self, title, expected):
        assert derive_milestone_code(title, taken=frozenset()) == expected

    def test_milestone_code_never_derives_the_reserved_segment(self):
        assert derive_milestone_code(NO_MILESTONE_SEGMENT, taken=frozenset()) == "milestone"

    def test_milestone_code_deduplicates(self):
        assert derive_milestone_code("MCP Gateway", taken=frozenset({"mcp-gateway"})) == (
            "mcp-gateway-2"
        )

    def test_spelling_round_trips_through_the_parser(self):
        spelled = spell_external_id(prefix="lor", milestone_code="mcp-gateway", number=142)
        assert spelled == "lor-mcp-gateway-142"
        assert parse_ticket_number(spelled) == 142

    def test_a_missing_milestone_is_stated_not_dropped(self):
        assert spell_external_id(prefix="lor", milestone_code="", number=7) == "lor-none-7"

    def test_a_legacy_id_does_not_parse_as_structured(self):
        assert parse_ticket_number("456-one-dispatch-decision-instead-of-three") is None


class TestNumbering:
    def test_numbers_are_never_reused_after_a_delete(self, db_session: Session):
        """Deleting the newest ticket must not hand its number to the next one —
        a link shared to the deleted ticket would then resolve to unrelated work."""
        workspace = make_workspace(db_session, slug="numbers")
        ticket = make_ticket(db_session, workspace_id=workspace.id, external_id="num-9")
        ticket.ticket_number = next_ticket_number(db_session, workspace)
        db_session.add(ticket)
        db_session.commit()

        db_session.delete(ticket)
        db_session.commit()
        assert next_ticket_number(db_session, workspace) > ticket.ticket_number

    def test_numbering_survives_rows_edited_underneath_the_counter(self, db_session: Session):
        workspace = make_workspace(db_session, slug="restored")
        ticket = make_ticket(db_session, workspace_id=workspace.id, external_id="res-40")
        ticket.ticket_number = 40
        db_session.add(ticket)
        db_session.commit()

        # The counter says 0, the rows say 40. Issuing 1 would duplicate.
        assert workspace.last_ticket_number == 0
        assert next_ticket_number(db_session, workspace) == 41

    def test_numbering_is_per_workspace(self, db_session: Session):
        one = make_workspace(db_session, slug="alpha")
        two = make_workspace(db_session, slug="beta")
        first = make_ticket(db_session, workspace_id=one.id, external_id="a-1")
        first.ticket_number = 40
        db_session.add(first)
        db_session.commit()

        assert next_ticket_number(db_session, one) == 41
        assert next_ticket_number(db_session, two) == 1


class TestCreation:
    def _milestone(self, session: Session, title: str) -> Ticket:
        return TicketService(session).create_ticket(
            workspace_slug="loregarden",
            title=title,
            work_item_type=WorkItemType.MILESTONE,
        )

    def test_a_milestone_carries_its_own_code(self, db_session: Session):
        milestone = self._milestone(db_session, "MCP Gateway completion")
        assert milestone.milestone_code == "mcp-gateway"
        assert milestone.external_id.startswith("lg-mcp-gateway-")

    def test_a_child_inherits_the_milestone_s_code(self, db_session: Session):
        milestone = self._milestone(db_session, "Queue execution correctness")
        feature = TicketService(db_session).create_ticket(
            workspace_slug="loregarden",
            title="Claim the lane before dispatching",
            work_item_type=WorkItemType.FEATURE,
            parent_ticket_id=milestone.id,
        )
        assert feature.external_id == f"lg-queue-execution-{feature.ticket_number}"
        assert feature.ticket_number > milestone.ticket_number

    def test_a_grandchild_reaches_past_its_feature(self, db_session: Session):
        milestone = self._milestone(db_session, "Precise code review")
        feature = TicketService(db_session).create_ticket(
            workspace_slug="loregarden",
            title="A finding carries its evidence",
            work_item_type=WorkItemType.FEATURE,
            parent_ticket_id=milestone.id,
        )
        capability = TicketService(db_session).create_ticket(
            workspace_slug="loregarden",
            title="Attach the diff hunk",
            work_item_type=WorkItemType.CAPABILITY,
            parent_ticket_id=feature.id,
        )
        assert capability.external_id == f"lg-precise-code-{capability.ticket_number}"

    def test_a_supplied_id_becomes_the_legacy_id(self, db_session: Session):
        """An id from somewhere else is kept and stays resolvable, but it does not
        get to be the ticket's id — otherwise an import could still land tickets
        nobody can share."""
        ticket = TicketService(db_session).create_ticket(
            workspace_slug="loregarden",
            title="Imported milestone",
            work_item_type=WorkItemType.MILESTONE,
            external_id="imported-01",
        )
        assert ticket.legacy_external_id == "imported-01"
        assert ticket.external_id == f"lg-imported-milestone-{ticket.ticket_number}"
        assert ticket.ticket_number > 0
        found = resolve(db_session, "imported-01", workspace_id=ticket.workspace_id)
        assert found is not None and found.id == ticket.id

    def test_a_duplicate_explicit_id_is_refused(self, db_session: Session):
        TicketService(db_session).create_ticket(
            workspace_slug="loregarden",
            title="First",
            work_item_type=WorkItemType.MILESTONE,
            external_id="taken-01",
        )
        with pytest.raises(ValueError, match="already exists"):
            TicketService(db_session).create_ticket(
                workspace_slug="loregarden",
                title="Second",
                work_item_type=WorkItemType.MILESTONE,
                external_id="taken-01",
            )


class TestResolution:
    def _ticket(self, session: Session) -> tuple[Workspace, Ticket]:
        workspace = make_workspace(session, slug="resolveme")
        workspace.ticket_prefix = "res"
        ticket = make_ticket(session, workspace_id=workspace.id, external_id="res-inbox-12")
        ticket.ticket_number = 12
        ticket.legacy_external_id = "12-an-old-and-very-long-title-slug"
        session.add(workspace)
        session.add(ticket)
        session.commit()
        return workspace, ticket

    def test_resolves_the_current_spelling(self, db_session: Session):
        workspace, ticket = self._ticket(db_session)
        found = resolve(db_session, "res-inbox-12", workspace_id=workspace.id)
        assert found is not None and found.id == ticket.id

    def test_resolves_the_pre_restructure_id(self, db_session: Session):
        workspace, ticket = self._ticket(db_session)
        found = resolve(db_session, "12-an-old-and-very-long-title-slug", workspace_id=workspace.id)
        assert found is not None and found.id == ticket.id

    def test_a_stale_milestone_segment_still_resolves(self, db_session: Session):
        """The point of the number being authoritative: a link shared before a
        re-parent keeps working after the id is re-spelled."""
        workspace, ticket = self._ticket(db_session)
        found = resolve(db_session, "res-some-other-milestone-12", workspace_id=workspace.id)
        assert found is not None and found.id == ticket.id

    def test_the_prefix_names_the_workspace_when_none_is_given(self, db_session: Session):
        _workspace, ticket = self._ticket(db_session)
        found = resolve(db_session, "res-inbox-12")
        assert found is not None and found.id == ticket.id

    def test_an_unknown_prefix_resolves_to_nothing(self, db_session: Session):
        self._ticket(db_session)
        assert resolve(db_session, "zzz-inbox-12") is None

    def test_a_bare_number_needs_a_workspace(self, db_session: Session):
        workspace, ticket = self._ticket(db_session)
        assert resolve(db_session, "12") is None
        found = resolve(db_session, "12", workspace_id=workspace.id)
        assert found is not None and found.id == ticket.id

    def test_resolution_stays_inside_the_given_workspace(self, db_session: Session):
        workspace, _ticket = self._ticket(db_session)
        other = make_workspace(db_session, slug="elsewhere")
        assert resolve(db_session, "res-inbox-12", workspace_id=other.id) is None

    def test_an_empty_ref_resolves_to_nothing(self, db_session: Session):
        self._ticket(db_session)
        assert resolve(db_session, "   ") is None


class TestReparentAndMove:
    def test_respelling_keeps_the_number(self, db_session: Session):
        service = TicketService(db_session)
        origin = service.create_ticket(
            workspace_slug="loregarden",
            title="Queue execution correctness",
            work_item_type=WorkItemType.MILESTONE,
        )
        destination = service.create_ticket(
            workspace_slug="loregarden",
            title="Workflow integrity",
            work_item_type=WorkItemType.MILESTONE,
        )
        feature = service.create_ticket(
            workspace_slug="loregarden",
            title="Claim the lane",
            work_item_type=WorkItemType.FEATURE,
            parent_ticket_id=origin.id,
        )
        shared_link = feature.external_id
        number = feature.ticket_number

        feature.parent_ticket_id = destination.id
        db_session.add(feature)
        db_session.commit()
        workspace = db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).first()
        respell_external_id(db_session, feature, workspace)
        db_session.commit()

        assert feature.external_id == f"lg-workflow-integrity-{number}"
        assert feature.ticket_number == number
        found = resolve(db_session, shared_link)
        assert found is not None and found.id == feature.id

    def test_a_cross_workspace_move_reissues_the_number(self, db_session: Session):
        source = make_workspace(db_session, slug="source")
        source.ticket_prefix = "sou"
        destination = make_workspace(db_session, slug="dest")
        destination.ticket_prefix = "des"
        db_session.add(source)
        db_session.add(destination)

        # The number the moved ticket holds is already taken in the destination.
        squatter = make_ticket(db_session, workspace_id=destination.id, external_id="des-none-5")
        squatter.ticket_number = 5
        moving = make_ticket(db_session, workspace_id=source.id, external_id="sou-none-5")
        moving.ticket_number = 5
        db_session.add(squatter)
        db_session.add(moving)
        db_session.commit()

        moving.workspace_id = destination.id
        db_session.add(moving)
        reissue_in_workspace(db_session, [moving], destination)
        db_session.commit()

        assert moving.ticket_number != squatter.ticket_number
        assert moving.legacy_external_id == "sou-none-5"
        found = resolve(db_session, "sou-none-5")
        assert found is not None and found.id == moving.id


class TestBackfillMigration:
    def _rows(self, connection):
        return {
            row[0]: (row[1], row[2], row[3])
            for row in connection.execute(
                text("SELECT id, external_id, legacy_external_id, ticket_number FROM tickets")
            ).fetchall()
        }

    def test_backfill_respells_every_ticket_and_keeps_the_old_id(
        self, db_session: Session, isolated_db
    ):
        service = TicketService(db_session)
        milestone = service.create_ticket(
            workspace_slug="loregarden",
            title="Legacy milestone",
            work_item_type=WorkItemType.MILESTONE,
            external_id="01-legacy-milestone",
        )
        feature = service.create_ticket(
            workspace_slug="loregarden",
            title="Legacy feature",
            work_item_type=WorkItemType.FEATURE,
            parent_ticket_id=milestone.id,
            external_id="02-legacy-feature-with-a-very-long-title-slug",
        )
        # Put the pair back into the pre-migration shape: the old literal *is* the
        # external_id, and none of the new columns exist yet.
        with isolated_db.begin() as connection:
            connection.execute(
                text(
                    "UPDATE tickets SET ticket_number = 0, milestone_code = '', "
                    "external_id = legacy_external_id, legacy_external_id = ''"
                )
            )
            connection.execute(
                text("UPDATE workspaces SET ticket_prefix = '', last_ticket_number = 0")
            )

        with isolated_db.begin() as connection:
            m_structured_ticket_ids(connection)
            rows = self._rows(connection)

        milestone_row = rows[milestone.id]
        feature_row = rows[feature.id]
        assert milestone_row[1] == "01-legacy-milestone"
        assert feature_row[1] == "02-legacy-feature-with-a-very-long-title-slug"
        assert feature_row[0] == f"lg-legacy-milestone-{feature_row[2]}"
        assert feature_row[2] > 0

    def test_backfill_numbers_are_unique_per_workspace(self, db_session: Session, isolated_db):
        service = TicketService(db_session)
        for index in range(3):
            service.create_ticket(
                workspace_slug="loregarden",
                title=f"Milestone {index}",
                work_item_type=WorkItemType.MILESTONE,
            )
        with isolated_db.begin() as connection:
            connection.execute(text("UPDATE tickets SET ticket_number = 0"))
            m_structured_ticket_ids(connection)
            duplicates = connection.execute(
                text(
                    "SELECT workspace_id, ticket_number, COUNT(*) c FROM tickets "
                    "GROUP BY 1, 2 HAVING c > 1"
                )
            ).fetchall()
        assert duplicates == []

    def test_rerunning_the_backfill_does_not_renumber(self, db_session: Session, isolated_db):
        TicketService(db_session).create_ticket(
            workspace_slug="loregarden",
            title="Only milestone",
            work_item_type=WorkItemType.MILESTONE,
        )
        with isolated_db.begin() as connection:
            connection.execute(text("UPDATE tickets SET ticket_number = 0"))
            m_structured_ticket_ids(connection)
            first = self._rows(connection)
            m_structured_ticket_ids(connection)
            second = self._rows(connection)
        assert first == second
