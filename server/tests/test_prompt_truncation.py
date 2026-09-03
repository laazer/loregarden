"""A prompt cap that bites has to say so.

`titled_block` returned `body[:cap]` with no comparison and no signal, so a
document that outgrew its cap was cut mid-word and nothing anywhere reported it.
The case that produced the ticket: `gatekeeper_review_v1.md` was 15,015 bytes
against a 12,000 cap and was cut at "## Cor", severing "## Core
Responsibilities" and the gatekeeper's entire approve/reject decision contract.
The agent responsible for quality gates never received its own job description,
and the run looked normal (lg-workflow-integrity-91).

That specific file now fits. The mechanism did not change, which is why these
tests are about the mechanism.
"""

from __future__ import annotations

import logging

from loregarden.agents.prompt_blocks import PromptTruncation, titled_block


def _doc(sections: int, section_chars: int) -> str:
    return "".join(f"\n## Section {i}\n" + ("x" * section_chars) for i in range(sections))


def test_a_body_within_its_cap_is_untouched_and_reports_nothing():
    collected: list[PromptTruncation] = []
    body = "## Core Responsibilities\nshort enough"

    block = titled_block("## Agent Role", body, cap=12000, truncations=collected)

    assert block == ["", "## Agent Role", body]
    assert collected == []


def test_an_uncapped_block_is_never_cut():
    body = "x" * 50_000
    assert titled_block("## Role", body)[2] == body


def test_exceeding_a_cap_is_recorded_rather_than_silently_cut():
    """AC1. The signal is the whole point — the cut itself was never the part
    that made this dangerous."""
    collected: list[PromptTruncation] = []
    body = _doc(sections=40, section_chars=500)
    assert len(body) > 12_000

    titled_block("## Loregarden control-plane module", body, cap=12000, truncations=collected)

    assert len(collected) == 1
    record = collected[0]
    assert record.title == "## Loregarden control-plane module"
    assert record.cap == 12000
    assert record.original_length == len(body)
    assert record.dropped > 0
    assert "exceeds its 12000 cap" in record.describe()


def test_exceeding_a_cap_logs_even_without_a_collector(caplog):
    """A caller that passes no list still must not get a silent cut."""
    with caplog.at_level(logging.WARNING, logger="loregarden.agents.prompt_blocks"):
        titled_block("## Memory protocol module", _doc(30, 500), cap=8000)

    assert any("truncated" in record.message.lower() for record in caplog.records)


def test_the_cut_lands_on_a_section_boundary_not_mid_word():
    """AC2. A mid-word cut is how this stayed invisible: the prompt still read
    as prose, so nothing looked broken. Whole sections make the seam legible."""
    collected: list[PromptTruncation] = []
    body = _doc(sections=40, section_chars=500)

    kept = titled_block("## Role", body, cap=12000, truncations=collected)[2]

    assert len(kept) <= 12_000
    # Ends at the end of a section, so the last thing the agent reads is complete.
    assert not kept.endswith("## ")
    assert kept.rstrip().endswith("x")
    # And every heading that survived is whole.
    assert "## Section" in kept
    assert kept.count("\n## Section") == kept.count("## Section")


def test_a_single_oversized_section_still_gets_cut():
    """No heading before the cap means no boundary to cut at. Half a section
    beats none, and the record still says what was lost."""
    collected: list[PromptTruncation] = []
    body = "## Only section\n" + ("x" * 20_000)

    kept = titled_block("## Role", body, cap=5000, truncations=collected)[2]

    assert len(kept) == 5000
    assert collected[0].dropped == len(body) - 5000


def test_several_capped_blocks_each_report_themselves():
    """The artifact names which document overflowed; one collector, several
    records."""
    collected: list[PromptTruncation] = []
    titled_block("## A", _doc(40, 500), cap=6000, truncations=collected)
    titled_block("## B", _doc(40, 500), cap=8000, truncations=collected)

    assert [r.title for r in collected] == ["## A", "## B"]
    assert [r.cap for r in collected] == [6000, 8000]
