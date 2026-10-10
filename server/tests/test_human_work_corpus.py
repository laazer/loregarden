"""Which block messages hand work to a person, pinned against real phrasings.

A request for a person files a HUMAN_ACTION card; a mention of one must not.
Every HUMAN_ACTION card filed before lg-workflow-integrity-914 was the second
kind — all three triggered by "a human" in descriptive prose — so both lists are
checked whole whenever the predicate changes.
"""

import pytest
from loregarden.services.block_classification import looks_like_human_work

#: Real messages that asked for a person: test fixtures and agent output.
ASKS_FOR_A_PERSON = (
    "I need a human to look at this.",
    "Needs a person to plug the device in.",
    "Needs a person to choose: ship behind the flag, or hold the release.",
    "a human/operator must capture real display-backed Godot editor GPU profiler data",
    "Blocked: needs a human decision on asset licensing.",
    "This has to be done by hand in the Godot editor.",
    "Requires a person at the device to approve the pairing prompt.",
    "An operator must rotate the deploy key before this can continue.",
)

#: Real messages that only mentioned one — the three HUMAN_ACTION cards ever filed.
MENTIONS_A_PERSON = (
    "Rerouted 4x from 'script_review' without passing. Paused for a human — see "
    "the accumulated rework feedback before deciding.",
    "A slow review stage can trip a false STUCK and block the ticket for a human on "
    "its first rejection. Fix: claim the reconcile.",
    "Both transports run the identical wrapper, so output must still reach the log "
    "for a human who is not there.",
)


@pytest.mark.parametrize("message", ASKS_FOR_A_PERSON)
def test_a_request_for_a_person_is_human_work(message):
    assert looks_like_human_work(message)


@pytest.mark.parametrize("message", MENTIONS_A_PERSON)
def test_a_mention_of_a_person_is_not(message):
    assert not looks_like_human_work(message)
