"""Adversarial / edge-case tests for initiative external ids (shareable init-*).

Extends the AC happy-path suite in ``test_initiative_external_ids.py`` and the
helper pins in ``test_ticket_ids.py``. Each case targets a real regression seam
the design suite leaves open — especially global uniqueness (workspace-scoped
``_reject_taken`` is insufficient), pool lag, case/whitespace resolve INCLUDE,
and false-green paths around list parent lookup.

Expected red until implement merges 732 and lands R1–R6.
"""

from __future__ import annotations

import json
import re
import threading
from typing import Any

import pytest
from loregarden.mcp.tools import execute_tool, normalize_tool_arguments
from loregarden.models.domain import Ticket, WorkItemType, Workspace
from loregarden.services.ticket_discovery import list_tickets_mcp
from loregarden.services.ticket_ids import resolve
from loregarden.services.ticket_service import TicketService
from sqlalchemy import text
from sqlmodel import Session, select
from tests.factories import make_ticket, make_workspace

_INIT_ID_RE = re.compile(r"^init-[a-z0-9-]+-\d+$")


def _initiative() -> WorkItemType:
    try:
        return WorkItemType.INITIATIVE
    except AttributeError as exc:
        raise AssertionError(
            "WorkItemType.INITIATIVE missing — merge loregarden/lg-initiatives-cross-732 first (R0/AC1)"
        ) from exc


def _helpers():
    from loregarden.services import ticket_ids as tid

    missing = [
        name
        for name in (
            "spell_initiative_external_id",
            "derive_initiative_slug",
            "next_initiative_number",
            "assign_initiative_external_id",
        )
        if not hasattr(tid, name)
    ]
    if missing:
        raise AssertionError(
            f"ticket_ids missing {missing} — implement R1–R3 initiative id helpers"
        )
    return tid


def _call(session: Session, name: str, args: dict[str, Any]) -> dict[str, Any]:
    return json.loads(execute_tool(session, name, normalize_tool_arguments(name, args)))


def _create_initiative(
    session: Session,
    *,
    title: str = "Adversarial Initiative",
    external_id: str = "",
) -> Ticket:
    return TicketService(session).create_ticket(
        workspace_slug=None,
        title=title,
        work_item_type=_initiative(),
        external_id=external_id,
    )


def _seeded_ws(session: Session) -> Workspace:
    ws = session.exec(select(Workspace).where(Workspace.slug == "loregarden")).first()
    assert ws is not None
    return ws


# --- global uniqueness (the plan's sharpest false-green trap) ----------------


class TestGlobalUniquenessVsWorkspaceTickets:
    """AC3 / R3 — uniqueness is on spelling globally, not ``workspace_id IS NULL``.

    A workspace-scoped duplicate check (732's ``_reject_taken_supplied_id``) or
    a SQLite UNIQUE on ``(workspace_id, ticket_number)`` both green-pass collisions
    that leave two rows answering to the same shareable id.
    """

    def test_supplied_id_matching_workspace_ticket_external_id_is_rejected(
        self, db_session: Session
    ):
        ws = _seeded_ws(db_session)
        existing = make_ticket(
            db_session,
            workspace_id=ws.id,
            external_id="shared-canonical-key",
            title="Workspace row that already owns the key",
        )
        before = len(db_session.exec(select(Ticket)).all())
        with pytest.raises(ValueError, match="(?i)external_id|already|taken|duplicate"):
            _create_initiative(
                db_session,
                title="Must Not Shadow Workspace Ticket",
                external_id=existing.external_id,
            )
        assert len(db_session.exec(select(Ticket)).all()) == before

    def test_supplied_id_matching_workspace_ticket_legacy_id_is_rejected(self, db_session: Session):
        ws = _seeded_ws(db_session)
        existing = make_ticket(
            db_session,
            workspace_id=ws.id,
            external_id="ws-owned-spelling-1",
            title="Workspace row with legacy",
        )
        existing.legacy_external_id = "imported-legacy-key"
        db_session.add(existing)
        db_session.commit()

        before = len(db_session.exec(select(Ticket)).all())
        with pytest.raises(ValueError, match="(?i)external_id|already|taken|duplicate"):
            _create_initiative(
                db_session,
                title="Must Not Shadow Workspace Legacy",
                external_id="imported-legacy-key",
            )
        assert len(db_session.exec(select(Ticket)).all()) == before

    def test_spelled_init_id_cannot_collide_with_existing_workspace_external_id(
        self, db_session: Session
    ):
        """Even without a supplied id — if a workspace ticket already holds the
        spelling the counter would issue, create must refuse rather than ship a
        duplicate shareable id."""
        tid = _helpers()
        ws = _seeded_ws(db_session)
        n = tid.next_initiative_number(db_session)
        # Roll the pool back one so the next create re-issues ``n`` — otherwise
        # this only catches collisions when the pool and the planted row agree.
        db_session.execute(
            text("UPDATE initiative_number_pool SET last_initiative_number = :n WHERE id='global'"),
            {"n": n - 1},
        )
        db_session.commit()

        slug = tid.derive_initiative_slug("Forced Collision Title", taken=frozenset())
        planted = tid.spell_initiative_external_id(slug, n)
        make_ticket(
            db_session,
            workspace_id=ws.id,
            external_id=planted,
            title="Planted workspace collision",
        )
        planted_row = db_session.exec(select(Ticket).where(Ticket.external_id == planted)).first()
        assert planted_row is not None

        before = len(db_session.exec(select(Ticket)).all())
        with pytest.raises(ValueError, match="(?i)external_id|already|taken|duplicate"):
            _create_initiative(db_session, title="Forced Collision Title")
        assert len(db_session.exec(select(Ticket)).all()) == before


# --- assign edge mutations ---------------------------------------------------


class TestAssignEdgeMutations:
    """AC2/AC3 — mutations around supplied_id and spelling."""

    def test_supplied_init_shaped_id_still_becomes_legacy(self, db_session: Session):
        """Caller pasting an old ``init-*`` must not keep it as canonical —
        the system always re-spells (plan-synthesis / R3)."""
        initiative = _create_initiative(
            db_session,
            title="Respell Supplied Init",
            external_id="init-old-shape-99",
        )
        assert initiative.legacy_external_id == "init-old-shape-99"
        assert _INIT_ID_RE.match(initiative.external_id)
        assert initiative.external_id != "init-old-shape-99"
        assert initiative.ticket_number == int(initiative.external_id.rsplit("-", 1)[-1])

    def test_whitespace_only_supplied_id_leaves_legacy_empty(self, db_session: Session):
        initiative = _create_initiative(
            db_session,
            title="Whitespace Supplied",
            external_id="   \t  ",
        )
        assert (
            initiative.legacy_external_id in ("", None)
            or not str(initiative.legacy_external_id).strip()
        )
        assert _INIT_ID_RE.match(initiative.external_id)

    def test_same_title_twice_yields_distinct_slugs(self, db_session: Session):
        """``taken`` must be initiative middle segments — two identical titles
        must not collide on spelling."""
        first = _create_initiative(db_session, title="Duplicate Title Alpha")
        second = _create_initiative(db_session, title="Duplicate Title Alpha")
        assert first.external_id != second.external_id
        assert _INIT_ID_RE.match(first.external_id)
        assert _INIT_ID_RE.match(second.external_id)


# --- resolve INCLUDE edges ---------------------------------------------------


class TestResolveIncludeEdges:
    """AC4 — case, whitespace, multi-workspace INCLUDE, reserved ``init`` prefix."""

    def test_resolve_is_case_insensitive_for_initiative_spelling(self, db_session: Session):
        ws = _seeded_ws(db_session)
        initiative = _create_initiative(db_session, title="Case Fold Resolve")
        upper = initiative.external_id.upper()
        assert upper != initiative.external_id
        found = resolve(db_session, upper, workspace_id=ws.id)
        assert found is not None
        assert found.id == initiative.id

    def test_resolve_strips_surrounding_whitespace(self, db_session: Session):
        ws = _seeded_ws(db_session)
        initiative = _create_initiative(db_session, title="Whitespace Resolve")
        found = resolve(db_session, f"  {initiative.external_id}  ", workspace_id=ws.id)
        assert found is not None
        assert found.id == initiative.id

    def test_two_workspaces_both_include_the_same_null_workspace_initiative(
        self, db_session: Session
    ):
        a = make_workspace(db_session, slug="init-adv-ws-a")
        b = make_workspace(db_session, slug="init-adv-ws-b")
        initiative = _create_initiative(db_session, title="Multi Workspace Include")
        found_a = resolve(db_session, initiative.external_id, workspace_id=a.id)
        found_b = resolve(db_session, initiative.external_id, workspace_id=b.id)
        assert found_a is not None and found_a.id == initiative.id
        assert found_b is not None and found_b.id == initiative.id

    def test_workspace_prefix_init_number_path_never_returns_initiative(self, db_session: Session):
        """R2 reserved literal ``init`` — if a workspace uses ``ticket_prefix=init``,
        trailing-number fallback must still never answer with an INITIATIVE row."""
        ws = make_workspace(db_session, slug="init-prefix-ws")
        ws.ticket_prefix = "init"
        db_session.add(ws)
        db_session.commit()

        initiative = _create_initiative(db_session, title="Prefix Init Collision")
        # Spelling path still works.
        by_spell = resolve(db_session, initiative.external_id, workspace_id=ws.id)
        assert by_spell is not None and by_spell.id == initiative.id

        # Number path under the reserved prefix must not.
        trailing = resolve(
            db_session,
            f"init-noise-{initiative.ticket_number}",
            workspace_id=ws.id,
        )
        assert trailing is None or trailing.id != initiative.id
        assert trailing is None or trailing.work_item_type != _initiative()

    def test_empty_ref_returns_none(self, db_session: Session):
        ws = _seeded_ws(db_session)
        assert resolve(db_session, "", workspace_id=ws.id) is None
        assert resolve(db_session, "   ", workspace_id=ws.id) is None


# --- pool / counter edges ----------------------------------------------------


class TestPoolCounterEdges:
    """AC2 / R1 — delete-safety and lag recovery."""

    def test_pool_recovers_when_live_max_exceeds_pool_last(self, db_session: Session):
        """Mirror ``next_ticket_number``: max(pool.last, max live INITIATIVE.n)+1."""
        tid = _helpers()
        # Force the pool low, plant a live initiative with a high ticket_number.
        db_session.execute(
            text("UPDATE initiative_number_pool SET last_initiative_number = 0 WHERE id='global'")
        )
        db_session.commit()

        planted = Ticket(
            external_id=tid.spell_initiative_external_id("pool-lag", 40),
            workspace_id=None,
            title="Pool lag plant",
            work_item_type=_initiative(),
            ticket_number=40,
        )
        db_session.add(planted)
        db_session.commit()

        issued = tid.next_initiative_number(db_session)
        assert issued == 41

    def test_assign_does_not_bump_any_workspace_last_ticket_number(self, db_session: Session):
        """R1 — never ``workspaces.last_ticket_number``, including sibling workspaces."""
        other = make_workspace(db_session, slug="init-adv-counter-ws")
        other.last_ticket_number = 17
        db_session.add(other)
        db_session.commit()
        before_other = other.last_ticket_number
        before_seeded = _seeded_ws(db_session).last_ticket_number

        _create_initiative(db_session, title="No Workspace Counter Bump")

        db_session.refresh(other)
        seeded = _seeded_ws(db_session)
        assert other.last_ticket_number == before_other
        assert seeded.last_ticket_number == before_seeded


# --- surface false-green guards ----------------------------------------------


class TestSurfaceFalseGreenGuards:
    """AC5 — list parent_external_id has no unscoped fallback today."""

    def test_list_parent_external_id_with_wrong_workspace_does_not_find_children(
        self, db_session: Session
    ):
        """If implement wires an unscoped fallback into list, this goes green
        incorrectly — children of a null-workspace initiative would leak under
        any workspace_slug."""
        other = make_workspace(db_session, slug="init-adv-wrong-ws")
        other.ticket_prefix = "iaw"
        db_session.add(other)
        db_session.commit()

        initiative = _create_initiative(db_session, title="Wrong Ws List Parent")
        milestone = TicketService(db_session).create_ticket(
            workspace_slug="loregarden",
            title="Child Under Initiative",
            work_item_type=WorkItemType.MILESTONE,
            parent_ticket_id=initiative.id,
        )
        payload = list_tickets_mcp(
            db_session,
            workspace_slug=other.slug,
            parent_external_id=initiative.external_id,
        )
        ids = {row["id"] for row in payload["tickets"]}
        # Wrong workspace must not surface the loregarden child.
        assert milestone.id not in ids

    def test_get_ticket_with_unrelated_workspace_slug_still_resolves_initiative(
        self, db_session: Session
    ):
        """INCLUDE on spelling — a scoped get_ticket must still find the orphan
        initiative even when the slug names a different workspace."""
        other = make_workspace(db_session, slug="init-adv-get-ws")
        initiative = _create_initiative(db_session, title="Get Ticket Other Slug")
        payload = _call(
            db_session,
            "loregarden_get_ticket",
            {
                "ticket_id": initiative.external_id,
                "workspace_slug": other.slug,
            },
        )
        assert payload["ticket_id"] == initiative.id
        assert payload["external_id"] == initiative.external_id


# --- concurrency -------------------------------------------------------------


class TestConcurrentInitiativeCreates:
    """Race on the global pool / uniqueness check — same hazard as workspace creates."""

    def test_concurrent_creates_never_duplicate_external_id_or_ticket_number(self, isolated_db):
        # Fail fast with the same R0 signal as the rest of the suite when the
        # enum is absent — do not bury it inside the race outcomes as "crash".
        initiative_type = _initiative()
        outcomes: list[tuple[str, object]] = []
        barrier = threading.Barrier(2)

        def attempt(n: int) -> None:
            barrier.wait(timeout=5)
            with Session(isolated_db) as session:
                try:
                    ticket = TicketService(session).create_ticket(
                        workspace_slug=None,
                        title=f"Race Initiative {n}",
                        work_item_type=initiative_type,
                    )
                    outcomes.append(("ok", (ticket.id, ticket.external_id, ticket.ticket_number)))
                except ValueError as exc:
                    outcomes.append(("error", str(exc)))
                except Exception as exc:  # noqa: BLE001 - catch anything the race surfaces
                    outcomes.append(("crash", repr(exc)))

        threads = [threading.Thread(target=attempt, args=(i,)) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        assert len(outcomes) == 2, f"both attempts must finish: {outcomes}"
        kinds = [k for k, _ in outcomes]
        assert "crash" not in kinds, f"no unhandled crash: {outcomes}"
        # Both may succeed if titles differ (distinct slugs) — then numbers/ids must differ.
        oks = [payload for k, payload in outcomes if k == "ok"]
        assert len(oks) >= 1
        external_ids = [ext for _id, ext, _n in oks]
        numbers = [n for _id, _ext, n in oks]
        assert len(set(external_ids)) == len(external_ids), f"duplicate external_id: {oks}"
        assert len(set(numbers)) == len(numbers), f"duplicate ticket_number: {oks}"

        with Session(isolated_db) as session:
            rows = session.exec(
                select(Ticket).where(Ticket.work_item_type == initiative_type)
            ).all()
            spellings = [r.external_id for r in rows]
            assert len(spellings) == len(set(spellings))
            assert all(_INIT_ID_RE.match(s) for s in spellings)


# --- helper-level mutations (pure / counter) ---------------------------------


class TestHelperAdversarial:
    """R1/R2 — derive/spell corners that happy-path parametrize skips."""

    def test_derive_slug_strips_noise_like_milestone_codes(self):
        tid = _helpers()
        slug = tid.derive_initiative_slug("Track A — Cross Workspace", taken=frozenset())
        assert slug
        # Leading "Track A" is ordering noise for milestone codes; initiative
        # slugs reuse the same helpers, so the middle segment should name the
        # subject ("cross-workspace"), not the track label.
        assert slug.startswith("cross")
        spelled = tid.spell_initiative_external_id(slug, 3)
        assert _INIT_ID_RE.match(spelled)

    def test_spell_rejects_or_normalizes_empty_slug_segment(self):
        tid = _helpers()
        # Empty middle must not produce ``init--1`` or ``init-1``.
        try:
            spelled = tid.spell_initiative_external_id("", 1)
        except (ValueError, AssertionError):
            return
        assert _INIT_ID_RE.match(spelled), spelled
        assert "--" not in spelled

    def test_successive_next_calls_are_strictly_monotonic(self, db_session: Session):
        tid = _helpers()
        seen: list[int] = []
        for _ in range(5):
            seen.append(tid.next_initiative_number(db_session))
        assert seen == sorted(seen)
        assert len(set(seen)) == 5
        assert seen[-1] == seen[0] + 4
