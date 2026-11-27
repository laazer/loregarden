"""The scenario's initiatives: several at once, owning milestones and tracking work.

`prod_shape` carried one initiative because live had none at its calibration.
By 2026-11-26 live had six in flight, and the Initiatives page that rendered
fine over one stacked six full cards into five screens: the scenario could not
show it. Measured 2026-11-26 (read-only, `/api/initiatives`): 28 milestones
owned and 132 tickets tracked by membership across six initiatives, 80
milestones still unclaimed. Membership dominates (one initiative owns nothing
and tracks 97, a quarter of them done), and the members are features and bugs.

Selection is by order, not by a random stream, so seeding these does not
reshuffle every row drawn after them. Split from `prod_shape` (near its size
cap); `build_prod_shape` calls it.
"""

from __future__ import annotations

from collections.abc import Callable

from pydantic import BaseModel
from sqlmodel import Session

from loregarden.models.domain import InitiativeMember, Ticket, TicketState, WorkItemType
from loregarden.services.ticket_ids import assign_initiative_external_id
from loregarden.services.ticket_rollup import reconcile_parent
from loregarden.testing.factories import make_ticket

#: The kinds live tracks by membership: 124 of its 132 members are features or bugs.
_MEMBER_TYPES = (WorkItemType.FEATURE, WorkItemType.BUG)


class InitiativeShape(BaseModel):
    title: str
    #: Claims the milestones whose title names this theme, up to `owned`; "" owns none.
    theme: str
    owned: int
    #: Tickets it tracks by membership, by state; they keep their own milestones.
    members: dict[TicketState, int]


class InitiativeCalibration(BaseModel):
    measured: str
    initiatives: list[InitiativeShape]


#: Live's six, by their owned/member shape. The first keeps the theme and title the
#: scenario's lone initiative had, so its milestones stay the ones it always claimed.
INITIATIVE_CALIBRATION = InitiativeCalibration(
    measured="2026-11-26",
    initiatives=[
        InitiativeShape(
            title="Trustworthy gate outcomes on every stage transition",
            theme="gate outcomes on stage transitions",
            owned=14,
            members={TicketState.BACKLOG: 14},
        ),
        InitiativeShape(
            title="Make the control plane safe to run a large multi-repo initiative",
            theme="",
            owned=0,
            members={
                TicketState.BACKLOG: 70,
                TicketState.DONE: 23,
                TicketState.IN_PROGRESS: 2,
                TicketState.BLOCKED: 2,
            },
        ),
        InitiativeShape(
            title="Recall that ranks what the agent needs first",
            theme="memory recall ranking",
            owned=7,
            members={},
        ),
        InitiativeShape(
            title="Capacity leases every workspace honours",
            theme="docker capacity leases",
            owned=4,
            members={TicketState.BACKLOG: 18},
        ),
        InitiativeShape(
            title="A run log worth watching live",
            theme="the run log's live tail",
            owned=2,
            members={TicketState.BACKLOG: 2, TicketState.IN_PROGRESS: 1},
        ),
        InitiativeShape(
            title="Chat turns isolated in their own worktrees",
            theme="worktree isolation for chat turns",
            owned=1,
            members={},
        ),
    ],
)

#: The lone initiative's theme, kept public for callers that name it.
INITIATIVE_THEME = INITIATIVE_CALIBRATION.initiatives[0].theme
INITIATIVE_TITLE = INITIATIVE_CALIBRATION.initiatives[0].title


class InitiativesSummary(BaseModel):
    initiatives: int
    owned: int
    members: int


def add_initiatives(
    session: Session,
    milestones: list[tuple[str, str]],
    tickets: list,
    prod_shape_id: Callable[[str, int], str],
) -> InitiativesSummary:
    """Seed `INITIATIVE_CALIBRATION` over the scenario, spelled and rolled up by the
    real services.

    `milestones` is every (id, title) in creation order; `tickets` is the
    scenario's (slug, id, external_id, state, parent) rows. A milestone joins at
    most one initiative, and a ticket is a member of at most one, and never of
    the initiative that owns its milestone.
    """
    claimed: set[str] = set()
    tracked: set[str] = set()
    owned_total = members_total = 0
    for n, shape in enumerate(INITIATIVE_CALIBRATION.initiatives):
        initiative_id = prod_shape_id("initiative", n)
        is_new = session.get(Ticket, initiative_id) is None
        initiative = make_ticket(
            session,
            workspace_id=None,
            ticket_id=initiative_id,
            title=shape.title,
            work_item_type=WorkItemType.INITIATIVE,
            description=f"{shape.title}, across every workspace that holds a piece of it.",
        )
        if is_new:
            assign_initiative_external_id(session, initiative)

        owned = [
            ms_id
            for ms_id, title in milestones
            if shape.theme and shape.theme in title.lower() and ms_id not in claimed
        ][: shape.owned]
        for ms_id in owned:
            milestone = session.get(Ticket, ms_id)
            milestone.parent_ticket_id = initiative.id
            session.add(milestone)
        claimed.update(owned)

        members = _pick_members(session, tickets, shape.members, claimed, tracked)
        for ticket_id in members:
            if session.get(InitiativeMember, (initiative.id, ticket_id)) is None:
                session.add(
                    InitiativeMember(
                        initiative_id=initiative.id, ticket_id=ticket_id, added_by="prod-shape"
                    )
                )
        tracked.update(members)
        owned_total += len(owned)
        members_total += len(members)

        session.flush()
        reconcile_parent(session, initiative)
        session.add(initiative)
    session.commit()
    return InitiativesSummary(
        initiatives=len(INITIATIVE_CALIBRATION.initiatives),
        owned=owned_total,
        members=members_total,
    )


def _pick_members(
    session: Session,
    tickets: list,
    wanted: dict[TicketState, int],
    claimed: set[str],
    tracked: set[str],
) -> list[str]:
    """The first features and bugs of each wanted state whose milestone no initiative owns."""
    picked: list[str] = []
    for state, count in wanted.items():
        eligible = [
            ticket_id
            for _slug, ticket_id, _ext, ticket_state, parent in tickets
            if ticket_state == state
            and parent not in claimed
            and ticket_id not in tracked
            and session.get(Ticket, ticket_id).work_item_type in _MEMBER_TYPES
        ][:count]
        picked += eligible
    return picked
