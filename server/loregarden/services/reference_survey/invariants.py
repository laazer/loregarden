"""The reference survey's state invariants, registered in `invariant_registry`.

Apart from `invariant_checks` (their detectors and repairs) so a check never
imports the registration that names it.
"""

from __future__ import annotations

from datetime import date

from loregarden.models.domain import InvariantReentry
from loregarden.services.invariants import Invariant
from loregarden.services.reference_survey.invariant_checks import (
    cleared_card_violations,
    close_cleared_card_violations,
    finish_carrier_violations,
    open_carrier_violations,
    remap_unsplit_violations,
    unsplit_map_violations,
)

SURVEY_CARDS_CLOSE = Invariant(
    name="survey_cards_close_with_their_cause",
    ticket="lg-reference-sources-1613",
    statement=(
        "no reference-survey exception card stays pending once a later clone or dispatch "
        "on its source cleared what it reported"
    ),
    detect=cleared_card_violations,
    repair=close_cleared_card_violations,
    reentry=InvariantReentry.DEFECT,
    since=date(2026, 10, 27),
    prevented_by=(
        "server/tests/test_survey_card_clearing.py"
        "::test_every_success_path_closes_the_cards_it_clears"
    ),
)

FINISHED_PASSES_CLOSE_THEIR_CARRIER = Invariant(
    name="finished_survey_passes_close_their_carrier",
    ticket="lg-reference-sources-2018",
    statement=(
        "no survey carrier stays open, with nothing live behind it, once its pass is "
        "finished: the pass is the carrier's only deliverable"
    ),
    detect=open_carrier_violations,
    repair=finish_carrier_violations,
    reentry=InvariantReentry.EXPECTED,
    since=date(2026, 11, 4),
    expected_because=(
        "a carrier's run can die after its agent finished the pass (a lease sweep, a server "
        "restart, a transition gate that could not run), and only a clean run completion "
        "finishes the carrier; this repair is that finish"
    ),
)

SURVEY_MAPS_SPLIT_WHAT_NO_PASS_CAN_HOLD = Invariant(
    name="survey_maps_split_what_no_pass_can_hold",
    ticket="lg-reference-sources-2283",
    statement=(
        "no admitted survey source's map keeps a directory larger than the pass budget as "
        "one leaf: no slice can take it, so no pass ever reads it"
    ),
    detect=unsplit_map_violations,
    repair=remap_unsplit_violations,
    reentry=InvariantReentry.DEFECT,
    since=date(2026, 11, 6),
    prevented_by=(
        "server/tests/test_reference_survey_slicing.py"
        "::test_every_map_the_feeder_stores_splits_what_no_pass_can_hold"
    ),
)
