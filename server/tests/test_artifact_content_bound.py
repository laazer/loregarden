"""The feed's per-row content bound, as an algorithm over the parsed JSON.

lg-milestone-that-1424 AC2. `bound_feed_content` is handed the *parsed* content
of an artifact row and returns a value that re-encodes within the limit, plus
whether anything was dropped.

Why an algorithm and not a text slice: slicing the stored JSON *text* hands the
client the `{"_raw": ...}` arm of `artifact_service`'s decode, `reportVerdict`
returns null, and the verdict pill disappears from the row — which is invisible
on today's data (zero of the 2,139 currently-oversized rows carry top-level
`status`+`stage_key`) and breaks exactly the land-verification receipt this
ticket exists for. The four rules are ordered, and each has a test below:

  (a) keep what is small          — every top-level value <= SMALL_VALUE_LIMIT
  (b) salvage scalars             — from a top-level object dropped by (a)
  (c) leave a prefix              — of the largest dropped value, as text
  (d) guarantee the bound         — drop, then halve the prefix, then the floor

Why per-field trimming is not enough, measured read-only on the live database
2026-10-29: truncating every top-level string to 2 KiB brings only 336 of the
2,139 oversized rows (15.7%) under 16 KiB, and the median oversized row holds
just 0.3% of its bytes in its largest top-level string — `context` and `plan`
rows are large from many small fields and nested structure, not from one string.
`test_a_row_large_from_many_small_fields_comes_back_under_the_limit` is the test
that forbids weakening the policy to per-field trimming.
"""

from __future__ import annotations

import json
from typing import Any

from loregarden.services.artifact_content_bound import (
    ARTIFACT_FEED_CONTENT_LIMIT,
    PREFIX_LIMIT,
    SMALL_VALUE_LIMIT,
    bound_feed_content,
)

#: The per-member cap rule (b) applies when salvaging scalars from a dropped
#: object. AC2 states it as a literal rather than a named constant.
SCALAR_SALVAGE_LIMIT = 256


def encoded(value: Any) -> int:
    """The size the bound is measured in: `json.dumps(..., ensure_ascii=False)` as UTF-8."""
    return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))


def text_of(size: int, fill: str = "a") -> str:
    """A string whose JSON encoding is exactly `size` bytes (two for the quotes)."""
    assert size >= 2
    return fill * (size - 2)


def test_the_constants_are_the_stated_thresholds():
    """The thresholds are module constants, not literals at the call site (AC1, AC2)."""
    assert ARTIFACT_FEED_CONTENT_LIMIT == 16384
    assert SMALL_VALUE_LIMIT == 2048
    assert PREFIX_LIMIT == 8192


def test_content_within_the_limit_is_returned_verbatim():
    """The algorithm applies only when the row is over the limit."""
    content = {
        "status": "pass",
        "stage_key": "gate",
        "confidence": 0.91,
        "nested": {"a": [1, 2, 3], "b": {"c": "d"}},
        "output": text_of(3000),
    }
    assert encoded(content) <= ARTIFACT_FEED_CONTENT_LIMIT

    bounded, dropped = bound_feed_content(content)

    assert bounded == content
    assert dropped is False


def test_a_non_object_within_the_limit_is_returned_verbatim():
    """A small list or bare string is not rewritten into `{}` for being the wrong shape."""
    assert bound_feed_content([1, 2, 3]) == ([1, 2, 3], False)
    assert bound_feed_content("a short note") == ("a short note", False)
    assert bound_feed_content(None) == (None, False)


# --- (a) keep what is small -------------------------------------------------


def test_rule_a_keeps_every_small_top_level_value_verbatim():
    """A top-level value re-encoding to <= 2048 bytes survives, nested containers included."""
    small_object = {"required_items_met": 5, "total_required_items": 7, "notes": ["a", "b"]}
    small_list = [{"k": i} for i in range(20)]
    content = {
        "status": "failed",
        "stage_key": "implement",
        "confidence": 0.4,
        "small_string": text_of(SMALL_VALUE_LIMIT),
        "small_object": small_object,
        "small_list": small_list,
        "number": 17,
        "flag": True,
        "nothing": None,
        # The one value that pushes the row over, and the only one that may go.
        "output": text_of(1_048_576),
    }
    assert encoded(content["small_string"]) == SMALL_VALUE_LIMIT

    bounded, dropped = bound_feed_content(content)

    assert dropped is True
    for key in (
        "status",
        "stage_key",
        "confidence",
        "small_string",
        "small_object",
        "small_list",
        "number",
        "flag",
        "nothing",
    ):
        assert bounded[key] == content[key], f"{key} was small enough to keep verbatim"


def test_rule_a_drops_a_top_level_value_one_byte_over_the_small_limit():
    """The boundary is inclusive: 2048 is small, 2049 is not."""
    content = {
        "status": "pass",
        "stage_key": "gate",
        "keep": text_of(SMALL_VALUE_LIMIT),
        "drop": text_of(SMALL_VALUE_LIMIT + 1),
        "output": text_of(1_048_576),
    }

    bounded, _ = bound_feed_content(content)

    assert bounded["keep"] == content["keep"]
    # `drop` is a dropped string, but `output` is the LARGEST dropped value, so
    # `output` is the one rule (c) puts a prefix back for.
    assert "drop" not in bounded


# --- (b) salvage scalars from a dropped object ------------------------------


def test_rule_b_salvages_the_scalar_members_of_a_dropped_object():
    """`handoff.required_items_met` survives an oversized handoff row (AC3, UX3).

    The object is too big to keep whole because of its `checklist` array; the two
    numbers `handoffScore` reads are scalars, and they come back.
    """
    content = {
        "status": "needs_rework",
        "stage_key": "test-design",
        "handoff": {
            "from_agent": "spec",
            "to_agent": "test_designer",
            "required_items_met": 5,
            "total_required_items": 7,
            "validated": True,
            "note": None,
            "checklist": [{"item_key": f"item_{i}", "evidence": text_of(400)} for i in range(40)],
        },
        "output": text_of(1_048_576),
    }
    assert encoded(content["handoff"]) > SMALL_VALUE_LIMIT

    bounded, dropped = bound_feed_content(content)

    assert dropped is True
    assert bounded["handoff"] == {
        "from_agent": "spec",
        "to_agent": "test_designer",
        "required_items_met": 5,
        "total_required_items": 7,
        "validated": True,
        "note": None,
    }


def test_rule_b_drops_a_scalar_member_over_its_own_cap():
    """A salvaged member is itself capped: a 300-byte string is not a scalar worth keeping."""
    content = {
        "status": "failed",
        "stage_key": "gate",
        "report": {
            "verdict": "fail",
            "essay": text_of(SCALAR_SALVAGE_LIMIT + 50),
            "lines": [text_of(400) for _ in range(10)],
        },
        "output": text_of(1_048_576),
    }

    bounded, _ = bound_feed_content(content)

    assert bounded["report"] == {"verdict": "fail"}


def test_rule_b_drops_a_reduced_object_that_would_still_be_too_large():
    """Scalars alone can exceed 2048 bytes; then the reduced object goes too."""
    content = {
        "status": "failed",
        "stage_key": "gate",
        "wide": {f"f{i}": text_of(200) for i in range(30)},
        "output": text_of(1_048_576),
    }
    scalars_only = {f"f{i}": text_of(200) for i in range(30)}
    assert encoded(scalars_only) > SMALL_VALUE_LIMIT

    bounded, _ = bound_feed_content(content)

    assert "wide" not in bounded


def test_rule_b_does_not_apply_to_a_dropped_list():
    """(b) salvages from a dropped *object*. A dropped array has no scalar members to name."""
    content = {
        "status": "pass",
        "stage_key": "gate",
        "rows": [text_of(300) for _ in range(50)],
        "output": text_of(1_048_576),
    }

    bounded, _ = bound_feed_content(content)

    assert "rows" not in bounded


# --- (c) leave a prefix -----------------------------------------------------


def test_rule_c_leaves_a_prefix_of_the_largest_dropped_string_at_its_own_key():
    """The reader opens on the head of the log, which is where a reader starts (UX4)."""
    output = "L" * 1_048_576
    content = {
        "status": "failed",
        "stage_key": "done",
        "output": output,
        # Also dropped by (a), but smaller — so it gets no prefix of its own.
        "detail": "D" * 5000,
    }

    bounded, _ = bound_feed_content(content)

    assert bounded["output"] == output[: len(bounded["output"])]
    assert len(bounded["output"].encode("utf-8")) == PREFIX_LIMIT
    assert "detail" not in bounded


def test_rule_c_never_splits_a_character():
    """8192 bytes does not land on a boundary for a 3-byte character."""
    output = "✓" * 7000  # 21,000 bytes of U+2713 CHECK MARK
    content = {"status": "failed", "stage_key": "done", "output": output}
    assert encoded(content) > ARTIFACT_FEED_CONTENT_LIMIT

    bounded, _ = bound_feed_content(content)

    prefix = bounded["output"]
    assert prefix == output[: len(prefix)]
    # The longest whole-character slice: 2730 characters, 8190 bytes.
    assert len(prefix.encode("utf-8")) == 8190
    assert len(prefix) == 2730
    assert prefix.encode("utf-8").decode("utf-8") == prefix


def test_rule_c_leaves_a_prefix_of_the_largest_dropped_value_when_none_was_a_string():
    """Rule (c) takes the largest dropped *value*, whatever its JSON shape (AC2, round 5).

    This replaces `test_rule_c_leaves_no_prefix_when_nothing_dropped_was_a_string`,
    which pinned the superseded rule — a prefix only for the largest dropped JSON
    *string*. The three kinds that dominate the oversized rows hold their bulk in
    structure instead: a `context` row in a nested object, a `diff` row in a
    `sections` array, a `log` row in a `lines` array. The old rule found no string
    to slice in any of them, so the feed row carried no head of the log and the
    reader opened on an empty body, contradicting AC7. Measured read-only over the
    live database (24,753 rows, 2,136 oversized), 1,689 of the bounded rows came
    back blank or near-blank under it, 806 of them literally `{}`.

    The prefix is a JSON *string* standing at a key that held an object or an
    array. That is deliberate, and it is what the `_raw` arm of this same rule
    already does.
    """
    cases = {
        "sections": {f"s{i}": {"body": text_of(400)} for i in range(60)},
        "lines": [text_of(400) for _ in range(60)],
    }
    for key, value in cases.items():
        content: dict[str, Any] = {"status": "pass", "stage_key": "gate", key: value}
        assert encoded(content) > ARTIFACT_FEED_CONTENT_LIMIT

        bounded, dropped = bound_feed_content(content)

        assert dropped is True
        assert encoded(bounded) <= ARTIFACT_FEED_CONTENT_LIMIT, f"{key} broke the bound"
        assert bounded["status"] == "pass"
        assert bounded["stage_key"] == "gate"
        prefix = bounded[key]
        # `.encode` and `.startswith` are what make this an assertion about a
        # string: the value at `sections`/`lines` is now text, not the container.
        assert prefix, f"{key} came back with no prefix"
        whole = json.dumps(value, ensure_ascii=False)
        assert whole.startswith(prefix), f"{key}'s prefix is not a leading slice of its text"
        assert len(prefix.encode("utf-8")) <= PREFIX_LIMIT


def test_an_oversized_row_never_comes_back_empty():
    """No over-limit row is reduced to nothing (AC2, round 5).

    The three shapes are the ones the superseded rule failed on, in the
    proportions it failed in: `context` 806 blank + 44 near-blank of 853, `diff`
    423 of 423, `log` 413 of 416. `diff` and `log` are not system kinds, so those
    rows are visible at the Outputs table's default `showSystem=false` — an
    operator opening one met a header, a subtitle and nothing.

    AC2 guarantees non-empty, and these three are checked at >= 1,024 re-encoded
    bytes, which is the measured floor on real data with room under it (the
    amended rule's worst live row comes back at 8,261). It is not a promise of a
    byte floor in general: enough objects reduced by rule (b) can crowd (d)'s
    halving loop until the prefix key goes, and AC2 deliberately does not claim
    a minimum size there.
    """
    context_row: dict[str, Any] = {
        "status": "pass",
        "stage_key": "plan",
        "sections": {
            f"section_{i}": {"title": f"s{i}", "body": {"text": text_of(600)}} for i in range(40)
        },
    }
    diff_row: dict[str, Any] = {
        "kind": "diff",
        "sections": [
            {"path": f"server/loregarden/services/mod_{i}.py", "hunk": text_of(700)}
            for i in range(40)
        ],
    }
    log_row: dict[str, Any] = {
        "stage_key": "implement",
        "lines": [f"{i:04d} " + text_of(500) for i in range(60)],
    }

    for label, content in (("context", context_row), ("diff", diff_row), ("log", log_row)):
        assert encoded(content) > ARTIFACT_FEED_CONTENT_LIMIT

        bounded, dropped = bound_feed_content(content)

        assert dropped is True
        assert bounded, f"an oversized {label} row came back empty"
        assert encoded(bounded) >= 1024, f"an oversized {label} row came back near-blank"
        assert encoded(bounded) <= ARTIFACT_FEED_CONTENT_LIMIT


# --- (d) guarantee the bound ------------------------------------------------


def _field_sizes(content: dict[str, Any], prefix: str) -> dict[str, int]:
    return {k: encoded(v) for k, v in content.items() if k.startswith(prefix)}


def test_rule_d_drops_remaining_keys_in_descending_size_until_it_fits():
    """Ten values each small by (a) still exceed the limit together."""
    content: dict[str, Any] = {"status": "pass", "stage_key": "gate", "confidence": 0.9}
    for i in range(10):
        content[f"f{i:02d}"] = text_of(2000 - i)
    assert encoded(content) > ARTIFACT_FEED_CONTENT_LIMIT

    bounded, dropped = bound_feed_content(content)

    assert dropped is True
    assert encoded(bounded) <= ARTIFACT_FEED_CONTENT_LIMIT
    sizes = _field_sizes(content, "f")
    kept = {k for k in bounded if k.startswith("f")}
    gone = set(sizes) - kept
    assert kept and gone, "some fields survived and some were dropped"
    assert max(sizes[k] for k in kept) <= min(sizes[k] for k in gone), (
        "the largest remaining keys go first"
    )


def test_rule_d_never_drops_the_fields_the_row_renders_from():
    """`status`, `stage_key`, `confidence`, the reduced objects and the prefix are protected.

    This is AC3's gatekeeper expressed over the algorithm: the Outputs row and
    the timeline row must still colour their pill from the feed row alone.
    """
    content: dict[str, Any] = {
        "status": "failed",
        "stage_key": "done",
        "confidence": 0.12,
        "handoff": {
            "required_items_met": 3,
            "total_required_items": 7,
            "checklist": [{"e": text_of(400)} for _ in range(40)],
        },
        "output": "Z" * 1_048_576,
    }
    for i in range(10):
        content[f"f{i:02d}"] = text_of(2000 - i)

    bounded, _ = bound_feed_content(content)

    assert encoded(bounded) <= ARTIFACT_FEED_CONTENT_LIMIT
    assert bounded["status"] == "failed"
    assert bounded["stage_key"] == "done"
    assert bounded["confidence"] == 0.12
    assert bounded["handoff"]["required_items_met"] == 3
    assert bounded["handoff"]["total_required_items"] == 7
    assert bounded["output"], "the prefix key is never dropped to make room"
    assert bounded["output"] == content["output"][: len(bounded["output"])]
    # The unprotected fields are what paid for it.
    assert len([k for k in bounded if k.startswith("f")]) < 10


def test_rule_d_halves_the_prefix_when_the_protected_fields_crowd_it():
    """With nothing droppable left, the prefix shrinks rather than the verdict."""
    content: dict[str, Any] = {"status": "failed", "stage_key": "done"}
    for i in range(5):
        # Oversized objects whose scalars salvage to just under 2048 bytes each:
        # five of those plus a full 8192-byte prefix does not fit.
        content[f"obj{i}"] = {
            **{f"s{j}": text_of(240) for j in range(8)},
            "bulk": [text_of(400) for _ in range(10)],
        }
    content["output"] = "Y" * 1_048_576

    bounded, _ = bound_feed_content(content)

    assert encoded(bounded) <= ARTIFACT_FEED_CONTENT_LIMIT
    for i in range(5):
        assert bounded[f"obj{i}"]["s0"] == text_of(240), f"obj{i} keeps its salvaged scalars"
        assert "bulk" not in bounded[f"obj{i}"]
    shown = len(bounded["output"].encode("utf-8"))
    assert 0 < shown <= PREFIX_LIMIT // 2, "the prefix was halved, not emptied"
    assert bounded["output"] == content["output"][: len(bounded["output"])]


def test_rule_d_last_resort_keeps_the_floor_and_no_prefix():
    """When an empty prefix still does not fit, the floor is what is left.

    The true floor is `status`, `stage_key` and `confidence` — a few dozen bytes,
    and the only thing the row's pill renders from (AC3) — so the prefix key is
    absent entirely rather than present and empty, and the bound still holds.
    The reduced objects are not part of that floor: rule (b) caps each one at
    SMALL_VALUE_LIMIT individually and nothing caps their sum, so twelve of them
    come to ~24 KB with no prefix at all. AC1 is unconditional, so they go too,
    largest first, and what survives keeps every salvaged scalar it had.
    """
    content: dict[str, Any] = {"status": "failed", "stage_key": "done", "confidence": 0.0}
    for i in range(12):
        content[f"obj{i:02d}"] = {
            **{f"s{j}": text_of(240) for j in range(8)},
            "bulk": [text_of(400) for _ in range(10)],
        }
    content["output"] = "X" * 1_048_576

    bounded, dropped = bound_feed_content(content)

    assert dropped is True
    assert "output" not in bounded
    assert encoded(bounded) <= ARTIFACT_FEED_CONTENT_LIMIT
    assert bounded["status"] == "failed"
    assert bounded["stage_key"] == "done"
    assert bounded["confidence"] == 0.0
    survivors = [key for key in bounded if key.startswith("obj")]
    assert survivors, "the reduced objects are dropped only as far as the bound needs"
    for key in survivors:
        assert bounded[key] == {f"s{j}": text_of(240) for j in range(8)}


# --- the non-object cases ---------------------------------------------------


def test_an_oversized_bare_string_becomes_a_raw_prefix():
    content = "S" * 1_048_576

    bounded, dropped = bound_feed_content(content)

    assert dropped is True
    assert set(bounded) == {"_raw"}
    assert bounded["_raw"] == content[: len(bounded["_raw"])]
    assert len(bounded["_raw"].encode("utf-8")) <= PREFIX_LIMIT
    assert encoded(bounded) <= ARTIFACT_FEED_CONTENT_LIMIT


def test_an_oversized_list_becomes_a_raw_prefix():
    content = [{"n": i, "pad": text_of(400)} for i in range(200)]

    bounded, dropped = bound_feed_content(content)

    assert dropped is True
    assert set(bounded) == {"_raw"}
    assert isinstance(bounded["_raw"], str)
    assert bounded["_raw"]
    assert len(bounded["_raw"].encode("utf-8")) <= PREFIX_LIMIT
    assert encoded(bounded) <= ARTIFACT_FEED_CONTENT_LIMIT
    assert json.dumps(content, ensure_ascii=False).startswith(bounded["_raw"])


def test_the_unparseable_raw_arm_keeps_its_key_and_gets_a_prefix():
    """`artifact_service` hands unparseable content over as `{"_raw": text}`."""
    raw = "not json " * 200_000
    content = {"_raw": raw}

    bounded, dropped = bound_feed_content(content)

    assert dropped is True
    assert set(bounded) == {"_raw"}
    assert bounded["_raw"] == raw[: len(bounded["_raw"])]
    assert len(bounded["_raw"].encode("utf-8")) <= PREFIX_LIMIT


def test_a_bare_string_of_escapes_is_bounded_on_its_encoded_size():
    """`_prefix` budgets raw bytes; the bound is measured on the JSON encoding.

    Every test above fills with `"x"`, where the two units coincide, so a
    PREFIX_LIMIT-byte prefix of one encodes to PREFIX_LIMIT bytes. A string of
    quote characters does not: each one escapes to `\\"`, so 8192 of them encode
    to 16384 bytes and the envelope puts the row over the limit. Rule (d)'s
    halving is what reconciles the two units, and it has to run on this arm too.
    """
    content = '"' * 20_000
    assert encoded(content) > ARTIFACT_FEED_CONTENT_LIMIT

    bounded, dropped = bound_feed_content(content)

    assert dropped is True
    assert encoded(bounded) <= ARTIFACT_FEED_CONTENT_LIMIT
    assert set(bounded) == {"_raw"}
    assert bounded["_raw"] == content[: len(bounded["_raw"])]


# --- the policy may not be weakened to per-field trimming -------------------


def test_a_row_large_from_many_small_fields_comes_back_under_the_limit():
    """A `context`-shaped row: a hundred small fields plus one large nested structure.

    No top-level string here is large, so trimming every top-level string to
    2 KiB changes nothing — which is the measured shape of the live data (the
    median oversized row holds 0.3% of its bytes in its largest top-level
    string). The bound must still hold, and must not collapse the row to its
    floor: a hundred small fields is most of what the operator came to read.
    """
    content: dict[str, Any] = {"status": "pass", "stage_key": "plan"}
    for i in range(100):
        content[f"field_{i:03d}"] = text_of(200 + i)
    content["sections"] = {
        f"section_{i}": {"title": f"s{i}", "body": [text_of(500) for _ in range(5)]}
        for i in range(60)
    }
    assert encoded(content) > ARTIFACT_FEED_CONTENT_LIMIT
    trimmed = {k: (v[:2048] if isinstance(v, str) else v) for k, v in content.items()}
    assert encoded(trimmed) > ARTIFACT_FEED_CONTENT_LIMIT, (
        "per-field trimming must not be able to satisfy this row"
    )

    bounded, dropped = bound_feed_content(content)

    assert dropped is True
    assert encoded(bounded) <= ARTIFACT_FEED_CONTENT_LIMIT
    assert bounded["status"] == "pass"
    assert bounded["stage_key"] == "plan"
    kept = [k for k in bounded if k.startswith("field_")]
    # Was `>= 50` before round 5's amendment to rule (c). `sections` is now the
    # largest dropped *value* and gets a prefix of up to PREFIX_LIMIT bytes, so
    # half the budget is spent before the small fields are counted and 34 of the
    # 100 survive. The assertion's job is unchanged — the row must not collapse
    # to its floor — and the count it can promise is smaller.
    assert len(kept) >= 25, f"only {len(kept)} of 100 small fields survived"
    for key in kept:
        assert bounded[key] == content[key], "a kept field is kept verbatim, not trimmed"


def test_a_row_of_many_nested_objects_cannot_outgrow_the_bound_in_aggregate():
    """Rule (b)'s reduced objects are capped one by one, never in aggregate.

    Fourteen top-level objects, each over SMALL_VALUE_LIMIT as a whole (a 9 KB
    blob) and each salvaging ~1.8 KB of scalars, come to ~24.9 KB reduced — and
    the row grows by another ~1.8 KB per nested object, ~178 KB at a hundred.
    That is the `context`/`plan` shape AC2's own rationale names as the common
    oversized row, and AC1 is unconditional, so the sum has to be enforced.
    """
    content: dict[str, Any] = {"status": "failed", "stage_key": "implement"}
    for i in range(14):
        content[f"nested_{i:02d}"] = {
            **{f"m{j:02d}": text_of(60) for j in range(25)},
            "blob": text_of(9000),
        }
    assert encoded(content) > ARTIFACT_FEED_CONTENT_LIMIT

    bounded, dropped = bound_feed_content(content)

    assert dropped is True
    assert encoded(bounded) <= ARTIFACT_FEED_CONTENT_LIMIT
    assert bounded["status"] == "failed"
    assert bounded["stage_key"] == "implement"
    survivors = [key for key in bounded if key.startswith("nested_")]
    assert survivors, "reduced objects are dropped only as far as the bound needs"
    for key in survivors:
        assert "blob" not in bounded[key], "rule (b) keeps the scalars, not the bulk"
        assert bounded[key]["m00"] == text_of(60)


# --- the flag ---------------------------------------------------------------


def test_the_flag_says_whether_anything_was_dropped():
    """The second element is what marks the row, so it must not be a constant."""
    _, untouched = bound_feed_content({"status": "pass", "stage_key": "gate"})
    _, shortened = bound_feed_content({"status": "pass", "output": "Q" * 1_048_576})

    assert untouched is False
    assert shortened is True


def test_an_explicit_limit_overrides_the_default():
    """`limit` is a keyword argument, so a caller (and this suite) can bound smaller."""
    content = {"status": "pass", "stage_key": "gate", "output": text_of(5000)}

    bounded, dropped = bound_feed_content(content, limit=1024)

    assert dropped is True
    assert encoded(bounded) <= 1024
    assert bounded["status"] == "pass"
    assert bounded["stage_key"] == "gate"


# ===========================================================================
# Adversarial cases (test-break stage).
#
# Everything above exercises the four rules on well-shaped input. These are the
# ways the bound can be implemented so that all of the above passes and the
# feed is still wrong: measured in the wrong unit, decided on the wrong
# boundary, unstable between two polls of the same row, or raising on content
# the database already holds. Each one names the mutation it kills.
# ===========================================================================


def _exactly(size: int) -> dict[str, Any]:
    """A receipt-shaped row whose encoding is exactly `size` bytes."""
    content: dict[str, Any] = {"status": "failed", "stage_key": "done", "output": ""}
    pad = size - encoded(content)
    assert pad >= 0
    content["output"] = "a" * pad
    assert encoded(content) == size
    return content


def test_the_limit_boundary_is_inclusive():
    """A row at exactly 16384 bytes is not shortened; one byte more is.

    Kills `>=` where the spec says "exceeds", which would mark and shorten a row
    that is already within the bound — and, through AC4, would put the two
    marker keys on a row nothing happened to.
    """
    at_limit = _exactly(ARTIFACT_FEED_CONTENT_LIMIT)
    over = _exactly(ARTIFACT_FEED_CONTENT_LIMIT + 1)

    assert bound_feed_content(at_limit) == (at_limit, False)

    bounded, dropped = bound_feed_content(over)
    assert dropped is True
    assert encoded(bounded) <= ARTIFACT_FEED_CONTENT_LIMIT


def test_the_bound_is_measured_in_utf8_bytes_and_not_in_characters():
    """16,000 check marks encode to 16,055 JSON *characters* and 48,055 *bytes*.

    AC1 says "measured as UTF-8". An implementation that measures
    `len(json.dumps(content))` lets this row — three times the limit — through
    untouched, and every test above passes because they are all ASCII. This is
    the mutation an ASCII-only suite cannot see.
    """
    content = {"status": "failed", "stage_key": "done", "output": "✓" * 16_000}
    serialized = json.dumps(content, ensure_ascii=False)
    assert len(serialized) <= ARTIFACT_FEED_CONTENT_LIMIT, "under the limit by characters"
    assert len(serialized.encode("utf-8")) > ARTIFACT_FEED_CONTENT_LIMIT, "over it by bytes"

    bounded, dropped = bound_feed_content(content)

    assert dropped is True
    assert encoded(bounded) <= ARTIFACT_FEED_CONTENT_LIMIT


def test_a_row_under_the_limit_is_not_shortened_by_ascii_escaping():
    """The measurement is `ensure_ascii=False`, as AC1 spells it.

    The same 5,000 check marks are 15,051 bytes as UTF-8 and 30,051 with
    `\\uXXXX` escapes. Measuring the escaped form shortens a row that is within
    the bound and marks it as partial, so the operator is told the rest is
    elsewhere when the whole of it was already here.
    """
    content = {"status": "pass", "stage_key": "gate", "note": "✓" * 5_000}
    assert len(json.dumps(content).encode("utf-8")) > ARTIFACT_FEED_CONTENT_LIMIT
    assert encoded(content) <= ARTIFACT_FEED_CONTENT_LIMIT

    assert bound_feed_content(content) == (content, False)


def test_bounding_is_stable_across_two_polls_of_the_same_row():
    """The feed is polled every 2 s; the same row must bound to the same bytes.

    Kills a tie-break by set iteration or by `dict` identity: a prefix that
    moves from one key to another between polls reflows the table and the
    reader under the operator, and `content_shown_bytes` stops meaning anything.
    """
    content: dict[str, Any] = {"status": "failed", "stage_key": "done"}
    # Two dropped strings of exactly equal encoded size: nothing but the rule
    # itself can decide which one gets the prefix.
    content["alpha"] = text_of(40_000)
    content["omega"] = text_of(40_000)
    for i in range(6):
        content[f"f{i}"] = text_of(2000)

    first, _ = bound_feed_content(content)
    second, _ = bound_feed_content(dict(content))

    assert first == second
    prefixes = [k for k in ("alpha", "omega") if k in first]
    assert len(prefixes) == 1, "exactly one of the two tied strings gets the prefix"


def test_bounding_an_already_bounded_row_changes_nothing():
    """Idempotent on the receipt shape: the second pass has nothing left to do.

    A bound that is not idempotent is a bound that depends on how many times it
    has run, which on a 2 s poll is a row that keeps shrinking.
    """
    content = {
        "status": "failed",
        "stage_key": "done",
        "exit_code": 1,
        "output": "FAILED one\n" * 100_000,
    }

    once, dropped_once = bound_feed_content(content)
    twice, dropped_twice = bound_feed_content(once)

    assert dropped_once is True
    assert twice == once
    assert dropped_twice is False


def test_the_caller_s_parsed_content_is_not_mutated():
    """`list_ticket_artifacts` measures `content_bytes` from the stored text, but
    the row it returns and the value it measured must not be the same object
    half-emptied. An in-place `del` here also corrupts the detail route if the
    two ever share a parse.
    """
    content: dict[str, Any] = {
        "status": "failed",
        "stage_key": "done",
        "handoff": {
            "required_items_met": 2,
            "total_required_items": 7,
            "checklist": [{"e": text_of(400)} for _ in range(40)],
        },
        "output": "L" * 1_048_576,
    }
    before = json.dumps(content, ensure_ascii=False, sort_keys=True)

    bound_feed_content(content)

    assert json.dumps(content, ensure_ascii=False, sort_keys=True) == before
    assert "checklist" in content["handoff"], "the input's nested object is intact"
    assert len(content["output"]) == 1_048_576


def test_empty_and_falsy_contents_are_returned_verbatim():
    """`{}`, `0`, `False`, `[]` and `""` are content, not absence.

    Kills `if not content:` where `if content is None` was meant — the reading
    that turns an empty object into `{"_raw": ""}` and a `False` into nothing.
    """
    for value in ({}, [], "", 0, 0.0, False, None):
        assert bound_feed_content(value) == (value, False), f"{value!r} was rewritten"


def test_rule_c_returns_a_short_dropped_string_whole():
    """A 3,000-byte string is too big for (a) and smaller than the prefix budget.

    It comes back whole — not padded, not re-sliced to 8192, and never longer
    than it started. The row is still marked shortened, because other keys went.
    """
    detail = text_of(3000)
    content: dict[str, Any] = {"status": "failed", "stage_key": "done", "detail": detail}
    for i in range(10):
        content[f"f{i:02d}"] = text_of(2000)
    assert encoded(content) > ARTIFACT_FEED_CONTENT_LIMIT

    bounded, dropped = bound_feed_content(content)

    assert dropped is True
    assert bounded["detail"] == detail
    assert len(bounded["detail"]) == len(detail)


def test_an_oversized_protected_field_cannot_break_the_bound():
    """A producer bug makes `status` itself a megabyte.

    AC2 (d) says the algorithm never drops top-level `status` — read naively
    with a `status` this size, nothing can satisfy AC1. The bound still has to
    hold, because the whole point of it is a response size the event loop can
    serve; whatever is left of `status` must be a leading slice of it.
    """
    content = {"status": "F" * 1_048_576, "stage_key": "done", "exit_code": 1}

    bounded, dropped = bound_feed_content(content)

    assert dropped is True
    assert encoded(bounded) <= ARTIFACT_FEED_CONTENT_LIMIT
    if isinstance(bounded.get("status"), str):
        assert content["status"].startswith(bounded["status"])


def test_a_lone_surrogate_in_stored_content_does_not_raise():
    """`json.loads('"\\ud800"')` yields a lone surrogate, which UTF-8 cannot encode.

    Agents write content through `json.dumps` (escaped, so it stores fine) and
    the feed reads it back parsed. Nothing re-encoded a row before this change,
    so this input was harmless; measuring the row in UTF-8 bytes introduces the
    first place it can raise. A `UnicodeEncodeError` here is a 500 on a feed
    polled every 2 s, for a row already in the database.
    """
    content = json.loads(json.dumps({"status": "failed", "stage_key": "done"}))
    content["output"] = "\ud800" + "x" * 1_048_576

    bounded, dropped = bound_feed_content(content)

    assert dropped is True
    assert bounded["status"] == "failed"
    assert bounded["stage_key"] == "done"


def test_deep_nesting_does_not_break_the_walk():
    """A 120-deep nested object, small at the top level and oversized as a whole.

    Rule (a) is stated over *top-level* keys, so nothing here needs to recurse;
    a walk that does, and does it without a depth guard, dies on real `context`
    rows that nest agent payloads several layers down.
    """
    deep: Any = {"leaf": text_of(2000)}
    for i in range(120):
        deep = {f"level_{i}": deep, "pad": text_of(120)}
    content = {"status": "pass", "stage_key": "plan", "tree": deep}
    assert encoded(content) > ARTIFACT_FEED_CONTENT_LIMIT

    bounded, dropped = bound_feed_content(content)

    assert dropped is True
    assert encoded(bounded) <= ARTIFACT_FEED_CONTENT_LIMIT
    assert bounded["status"] == "pass"


def test_a_tiny_limit_still_returns_the_verdict_fields():
    """`limit=0` is the degenerate caller, and the floor is what it gets.

    Kills an implementation that loops forever halving a prefix toward a limit
    it can never reach, and one that returns `{}` — the row would lose its pill
    on a surface that asked for a smaller bound.
    """
    content = {"status": "failed", "stage_key": "done", "confidence": 0.5, "output": "x" * 100_000}

    bounded, dropped = bound_feed_content(content, limit=0)

    assert dropped is True
    assert bounded["status"] == "failed"
    assert bounded["stage_key"] == "done"
    assert bounded["confidence"] == 0.5


# --- test-break: the corners the amended rule (c) newly reaches ----------------
#
# Round 5 widened rule (c) from "the largest dropped *string*" to "the largest
# dropped *value*". That is a one-clause change with a large blast radius: the
# prefix path now runs over shapes it never ran over before (objects, arrays,
# numbers), and its output is a JSON *string* standing where a container stood.
# Each test below is a corner that clause opened, or a guarantee AC2 states in
# words that nothing above measures.


def test_the_never_empty_guarantee_holds_at_the_smallest_stated_limit():
    """AC2 states the guarantee "for any `limit >= 4096`", and 4096 is where it bites.

    Everything above takes the default 16,384, where an 8,192-byte prefix has
    room to spare. At 4096 the prefix cannot fit as taken and rule (d) has to
    halve it down — four times for a `log` row — without ever halving it to
    nothing. The three shapes are the ones the superseded rule returned blank
    for on live data: a `context` row whose bulk is nested objects, a `diff`
    row whose bulk is a `sections` array, a `log` row whose bulk is `lines`.
    """
    context_row: dict[str, Any] = {"status": "pass", "stage_key": "plan"}
    for i in range(12):
        context_row[f"payload_{i}"] = {"notes": text_of(3000), "meta": {"a": text_of(500)}}
    diff_row = {
        "status": "pass",
        "stage_key": "implement",
        "sections": [{"path": f"f{i}.py", "hunk": text_of(900)} for i in range(40)],
    }
    log_row = {
        "status": "failed",
        "stage_key": "test",
        "lines": [text_of(300) for _ in range(200)],
    }

    for label, content in (("context", context_row), ("diff", diff_row), ("log", log_row)):
        bounded, dropped = bound_feed_content(content, limit=4096)

        assert dropped is True
        assert bounded, f"an oversized {label} row came back empty at limit=4096"
        assert encoded(bounded) <= 4096, f"the {label} row broke the bound it was given"


def test_a_row_whose_every_dropped_value_is_reduced_by_rule_b_is_still_non_empty():
    """The one arm AC2 allows to carry no prefix at all, so (d) is the whole guarantee.

    Rule (c) only runs on values rule (b) did not reduce. When (b) reduces every
    one of them there is no prefix key, and AC2's never-empty promise rests
    entirely on (d)'s terminal arm stopping as soon as the row fits rather than
    emptying the bag. Each object here is oversized because of one member (b)
    will not salvage, and small in the members it will.
    """
    content: dict[str, Any] = {"status": "pass", "stage_key": "plan"}
    for i in range(30):
        content[f"obj_{i}"] = {"n": i, "blob": text_of(4000), "label": text_of(100)}
    assert encoded(content) > ARTIFACT_FEED_CONTENT_LIMIT

    for limit in (4096, ARTIFACT_FEED_CONTENT_LIMIT):
        bounded, dropped = bound_feed_content(content, limit=limit)

        assert dropped is True
        assert encoded(bounded) <= limit
        survivors = [key for key in bounded if key.startswith("obj_")]
        assert survivors, f"limit={limit} dropped every reduced object and left the bare floor"
        assert bounded[survivors[0]] == {"n": int(survivors[0][4:]), "label": text_of(100)}


def test_rule_c_leaves_a_prefix_of_an_oversized_value_that_is_a_bare_number():
    """AC2 (c) names "number, bool, null" in its text form, not only containers.

    A top-level value can be over SMALL_VALUE_LIMIT without being a string, an
    object or an array: a 4,000-digit integer is 4,000 bytes and rule (b) does
    not apply to it. The superseded rule found no string and left nothing; the
    amended rule owes it `json.dumps(value)`'s leading slice at its own key.
    """
    number = int("9" * 4000)
    content: dict[str, Any] = {"status": "pass", "stage_key": "plan", "n": number}
    for i in range(8):
        content[f"pad_{i}"] = text_of(1900)
    assert encoded(content) > ARTIFACT_FEED_CONTENT_LIMIT
    assert encoded(number) > SMALL_VALUE_LIMIT

    bounded, dropped = bound_feed_content(content)

    assert dropped is True
    assert encoded(bounded) <= ARTIFACT_FEED_CONTENT_LIMIT
    assert isinstance(bounded["n"], str), "the prefix of a number is its text, not the number"
    assert bounded["n"], "a dropped number left no prefix at all"
    assert str(number).startswith(bounded["n"])


def test_rule_c_resolves_a_size_tie_between_two_shapes_the_same_way_every_poll():
    """The amended candidate set mixes shapes, so the tie-break spans them now.

    A string and an object of exactly equal encoded size are both eligible for
    the prefix. Nothing but the stated ordering can choose, and a choice that
    depends on which shape was seen first moves the prefix between keys from one
    2 s poll to the next — the same reflow `test_bounding_is_stable_across_two_
    polls_of_the_same_row` forbids, one shape wider.
    """
    as_string = text_of(9002)
    as_object = {"k": text_of(8995)}
    assert encoded(as_string) == encoded(as_object)

    first, _ = bound_feed_content(
        {"status": "p", "stage_key": "s", "aaa": as_string, "bbb": as_object}
    )
    reordered, _ = bound_feed_content(
        {"status": "p", "stage_key": "s", "bbb": as_object, "aaa": as_string}
    )

    prefixed = [key for key in ("aaa", "bbb") if isinstance(first.get(key), str)]
    assert len(prefixed) == 1, "exactly one of the two tied values gets the prefix"
    assert prefixed == [key for key in ("aaa", "bbb") if isinstance(reordered.get(key), str)]
    assert first[prefixed[0]] == reordered[prefixed[0]]


def test_the_prefix_of_an_escape_heavy_container_still_fits_the_bound():
    """PREFIX_LIMIT is raw UTF-8 bytes; the prefix is re-encoded as a JSON string.

    Rule (c) takes the slice against the *text* of the value, and every `"` and
    `\\` in that text costs two bytes again when the slice is put back as a JSON
    string. 8,192 bytes of a quote-heavy object re-encode to over 16,384 on
    their own, so the whole row is over the limit before any other key is
    counted. Only (d)'s halving loop reconciles the two units, and the
    superseded rule never ran this path on a container at all.
    """
    container = {"k": '"' * 40_000}
    content = {"status": "pass", "stage_key": "plan", "blob": container}

    bounded, dropped = bound_feed_content(content)

    assert dropped is True
    assert encoded(bounded) <= ARTIFACT_FEED_CONTENT_LIMIT
    assert isinstance(bounded["blob"], str) and bounded["blob"]
    assert json.dumps(container, ensure_ascii=False).startswith(bounded["blob"])


def test_bounding_a_row_already_reduced_to_a_container_prefix_changes_nothing():
    """Idempotence over the shape the amendment introduced.

    After the amendment a bounded row can carry a *string* at a key that held an
    object. Fed back in — which is what a second poll of a row the client
    cached, or a bound applied twice by a refactor, does — it must come back
    untouched and unflagged. A second pass that re-slices its own prefix is a
    row that keeps shrinking while the operator reads it.
    """
    content = {
        "status": "failed",
        "stage_key": "test",
        "sections": [{"path": f"f{i}.py", "hunk": text_of(900)} for i in range(60)],
    }

    once, dropped_once = bound_feed_content(content)
    twice, dropped_twice = bound_feed_content(once)

    assert dropped_once is True
    assert isinstance(once["sections"], str), "the array came back as its text prefix"
    assert twice == once
    assert dropped_twice is False


def test_the_bound_counts_the_row_s_keys_and_not_only_its_values():
    """Rule (a) is stated over each *value*; AC1 is stated over the whole row.

    Five hundred keys of a thousand characters each, every value one byte: rule
    (a) keeps all of them verbatim, rule (b) has no object to reduce and rule
    (c) has nothing dropped to slice. Half a megabyte of row, and only (d) is
    left to hold the bound. Kills an implementation that measures values and
    calls the row done.
    """
    content: dict[str, Any] = {"status": "pass", "stage_key": "plan"}
    for i in range(500):
        content[f"k{i:03d}" + "x" * 1000] = 1
    assert encoded(content) > 500_000

    bounded, dropped = bound_feed_content(content)

    assert dropped is True
    assert encoded(bounded) <= ARTIFACT_FEED_CONTENT_LIMIT
    assert bounded["status"] == "pass"
    assert bounded["stage_key"] == "plan"
