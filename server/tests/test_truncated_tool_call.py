"""A tool call that lost arguments must fail, not persist what survived.

Six tickets reached the database with their acceptance criteria stored as prose
at the end of `description` and their `acceptance_criteria` field empty. The
cause: a literal closing tag for the parameter it appeared inside terminated that
parameter early, so every argument after it — including the criteria — was
swallowed into the text.

That reads to the next agent as a ticket with NO criteria, and the known failure
from there is that it invents some and writes them into test docstrings, where
they steer every later stage of the run.

The trap propagates through the act of reporting it: filing a ticket that quotes
one of those descriptions produces another one. That is how the seventh instance
was created.
"""

import pytest
from loregarden.mcp.ticket_edit_tools import normalize_update_ticket_args
from loregarden.mcp.tool_args import (
    TRUNCATION_MARKERS,
    coerce_optional_int,
    coerce_string,
    coerce_string_list,
    reject_truncated_call,
)

#: Shaped like the payload that produced the six, rather than a synthetic string:
#: prose, then the stray closing tag, then the criteria that were lost.
REAL_SHAPE = (
    "Add a gate self-test covering the `return Model()` shape alongside the\n"
    "existing inert-body cases, so the rule cannot regress.</description>\n"
    '<parameter name="acceptance_criteria">["A broad except Exception whose body"'
)


def _update(**args):
    return normalize_update_ticket_args(
        {"ticket_id": "t1", **args},
        coerce_string=coerce_string,
        coerce_string_list=coerce_string_list,
        coerce_int=coerce_optional_int,
    )


def test_the_real_payload_shape_is_refused():
    with pytest.raises(ValueError, match="truncated"):
        _update(description=REAL_SHAPE)


@pytest.mark.parametrize("marker", TRUNCATION_MARKERS)
def test_every_marker_is_refused(marker: str):
    with pytest.raises(ValueError, match="truncated"):
        reject_truncated_call(f"Some prose {marker} more text", field="description")


def test_the_message_names_the_marker_and_says_what_to_do():
    """An error that does not say how to proceed gets retried verbatim."""
    with pytest.raises(ValueError) as excinfo:
        reject_truncated_call("prose </description> tail", field="description")
    message = str(excinfo.value)
    assert "</description>" in message
    assert "describing the tag rather than writing it literally" in message


def test_ordinary_prose_passes():
    """The control. A guard that rejected normal descriptions would be found
    immediately; one that rejects nothing would not be found at all."""
    text = "Angle brackets are fine: a < b, x > y, and <em>markup</em> in prose."
    assert reject_truncated_call(text, field="description") == text
    assert _update(description=text)["description"] == text


def test_a_description_that_merely_discusses_the_tags_still_passes():
    """The tags are refused as literals, not as a topic. A ticket about this
    defect has to be writable, or the fix makes its own bug unreportable."""
    text = (
        "The call is truncated by a literal closing description tag appearing "
        "inside the description text, which swallows every later argument."
    )
    assert reject_truncated_call(text, field="description") == text


def test_other_fields_are_untouched():
    """Deliberately narrow. `description` is where the damage was observed and is
    durable; broadening later is easy, and a guard that rejects legitimate work
    in unrelated fields is worse than the defect."""
    payload = _update(title="A title mentioning </description> oddly")
    assert payload["title"] == "A title mentioning </description> oddly"
