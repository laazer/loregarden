import json

import pytest
from loregarden.services.stage_report import (
    final_agent_message,
    parse_stage_report,
    stage_report_artifact_content,
)


def _wrap(payload: str) -> str:
    return f"Some narrative output from the agent.\n<<<LOREGARDEN_STAGE_REPORT>>>\n{payload}\n<<<END_STAGE_REPORT>>>\n"


def _stream_json(payload: str) -> str:
    """Reproduce how the CLI adapters actually store stdout: raw
    `--output-format stream-json` lines, where the report block is JSON-escaped
    inside the `result`/assistant-text fields rather than sitting in the buffer
    as literal text.
    """
    text = _wrap(payload)
    return "\n".join(
        [
            json.dumps({"type": "system", "subtype": "init", "session_id": "s1"}),
            json.dumps(
                {
                    "type": "assistant",
                    "message": {"content": [{"type": "text", "text": text}]},
                }
            ),
            json.dumps({"type": "result", "subtype": "success", "result": text}),
        ]
    )


def test_parse_stage_report_valid_pass():
    stdout = _wrap('{"status": "pass", "confidence": 0.95}')
    report = parse_stage_report(stdout)
    assert report is not None
    assert report.status == "pass"
    assert report.confidence == 0.95
    assert report.reroute_to_stage is None
    assert report.reroute_context == ""


def test_parse_stage_report_valid_fail_with_reroute():
    stdout = _wrap(
        '{"status": "fail", "confidence": 0.8, "reroute_to_stage": "implement", '
        '"reroute_context": "missing edge case coverage"}'
    )
    report = parse_stage_report(stdout)
    assert report is not None
    assert report.status == "fail"
    assert report.reroute_to_stage == "implement"
    assert report.reroute_context == "missing edge case coverage"


def test_parse_stage_report_missing_sentinel_returns_none():
    assert parse_stage_report("Just narrative output, no report block.") is None


def test_parse_stage_report_empty_stdout_returns_none():
    assert parse_stage_report("") is None


def test_parse_stage_report_malformed_json_returns_none():
    stdout = _wrap('{"status": "pass", "confidence": }')  # invalid JSON
    assert parse_stage_report(stdout) is None


def test_parse_stage_report_invalid_status_returns_none():
    stdout = _wrap('{"status": "maybe", "confidence": 0.5}')
    assert parse_stage_report(stdout) is None


def test_parse_stage_report_valid_blocked():
    stdout = _wrap(
        '{"status": "blocked", "confidence": 0.9, "reroute_context": "needs prod credentials"}'
    )
    report = parse_stage_report(stdout)
    assert report is not None
    assert report.status == "blocked"
    assert report.reroute_context == "needs prod credentials"


def test_parse_stage_report_confidence_clamped_to_unit_range():
    stdout = _wrap('{"status": "pass", "confidence": 5.0}')
    report = parse_stage_report(stdout)
    assert report is not None
    assert report.confidence == 1.0

    stdout_negative = _wrap('{"status": "pass", "confidence": -2.0}')
    report_negative = parse_stage_report(stdout_negative)
    assert report_negative is not None
    assert report_negative.confidence == 0.0


def test_parse_stage_report_takes_last_block_when_multiple():
    stdout = (
        _wrap('{"status": "fail", "confidence": 0.4, "reroute_to_stage": "spec"}')
        + "\nMore output after the first block.\n"
        + _wrap('{"status": "pass", "confidence": 0.99}')
    )
    report = parse_stage_report(stdout)
    assert report is not None
    assert report.status == "pass"
    assert report.confidence == 0.99


def test_stage_report_artifact_content_shape():
    report = parse_stage_report(
        _wrap(
            '{"status": "fail", "confidence": 0.7, "reroute_to_stage": "implement", '
            '"reroute_context": "regression in acid weak point"}'
        )
    )
    assert report is not None
    content = stage_report_artifact_content("script_review", report)
    assert content["stage_key"] == "script_review"
    assert content["status"] == "fail"
    assert content["reroute_to_stage"] == "implement"
    row_keys = {row["k"] for row in content["rows"]}
    assert row_keys == {
        "status",
        "confidence",
        "reroute_to_stage",
        "reroute_context",
        "reroute_to_agent",
    }


def test_parse_stage_report_from_stream_json_stdout():
    """Regression: the CLI adapters store raw stream-json, so the report block
    arrives JSON-escaped and the sentinel regex never matched the raw buffer —
    every agent verdict was silently dropped and the stage looked like a pass."""
    stdout = _stream_json(
        '{"status": "needs_rework", "confidence": 0.95, "reroute_to_stage": "implement", '
        '"reroute_context": "receptionist NPC is not implemented"}'
    )
    report = parse_stage_report(stdout)
    assert report is not None
    assert report.status == "needs_rework"
    assert report.reroute_to_stage == "implement"
    assert report.reroute_context == "receptionist NPC is not implemented"


def test_parse_stage_report_from_stream_json_ignores_contract_doc_echo():
    """The prompt's own contract example gets echoed back in stdout; its
    placeholder status must not be mistaken for a real verdict."""
    doc_echo = _wrap(
        '{"status": "pass|fail|needs_rework|blocked", "confidence": 0.0-1.0, '
        '"reroute_to_stage": "<stage_key>|null", "reroute_context": "<what it missed>"}'
    )
    stdout = "\n".join(
        [
            json.dumps({"type": "user", "message": {"content": [{"text": doc_echo}]}}),
            _stream_json('{"status": "pass", "confidence": 0.9}'),
        ]
    )
    report = parse_stage_report(stdout)
    assert report is not None
    assert report.status == "pass"


def test_parse_stage_report_stream_json_takes_last_report():
    stdout = "\n".join(
        [
            _stream_json('{"status": "pass", "confidence": 0.5}'),
            _stream_json('{"status": "fail", "confidence": 0.9}'),
        ]
    )
    report = parse_stage_report(stdout)
    assert report is not None
    assert report.status == "fail"


# --- the grounds for a rejection, not just the complaint ----------------------
#
# The contract has told rejecting agents to name the acceptance criteria their
# finding makes false since the reject-only-for-an-unmet-criterion rule landed.
# Nothing read them. The field was absent from the parser, absent from
# `StageReport`, and absent from the JSON schema the contract shows agents —
# while the prose two paragraphs below instructed them to send it. So an agent
# that did as it was told had its evidence dropped, and the stage it rerouted to
# received the complaint without the grounds (lg-workflow-integrity-205).


def test_unmet_criteria_are_parsed():
    report = parse_stage_report(
        "<<<LOREGARDEN_STAGE_REPORT>>>\n"
        '{"status": "needs_rework", "confidence": 0.9, "reroute_to_stage": "implement",'
        ' "reroute_context": "the retry path is untested",'
        ' "unmet_criteria": ["AC2: a failed run is retried once", "AC4: retries are logged"]}\n'
        "<<<END_STAGE_REPORT>>>"
    )
    assert report is not None
    assert report.unmet_criteria == [
        "AC2: a failed run is retried once",
        "AC4: retries are logged",
    ]


def test_a_bare_string_is_not_a_criteria_list():
    """The contract shows a list and Pydantic holds that line.

    Accepting a bare string would mean a hand-written type check in the parser —
    exactly what the organization gate asks us to model instead. What matters is
    that the verdict survives a field the agent got wrong.
    """
    report = parse_stage_report(
        "<<<LOREGARDEN_STAGE_REPORT>>>\n"
        '{"status": "fail", "confidence": 1.0, "reroute_to_stage": null,'
        ' "reroute_context": "x", "unmet_criteria": "AC1: the gate blocks a bad key"}\n'
        "<<<END_STAGE_REPORT>>>"
    )
    assert report is not None
    assert report.status == "fail"
    assert report.unmet_criteria == []


def test_waivers_are_parsed():
    """136 R5. Producer waiver sidecar is StageReport.waivers, parsed like unmet_criteria."""
    report = parse_stage_report(
        "<<<LOREGARDEN_STAGE_REPORT>>>\n"
        '{"status": "pass", "confidence": 0.9,'
        ' "waivers": [{"name": "handoff:test_suite_complete incomplete",'
        ' "reason": "accepted the remaining gap"}]}\n'
        "<<<END_STAGE_REPORT>>>"
    )
    assert report is not None
    assert report.status == "pass"
    assert len(report.waivers) == 1
    assert report.waivers[0].name == "handoff:test_suite_complete incomplete"
    assert report.waivers[0].reason == "accepted the remaining gap"


def test_empty_reason_waiver_items_never_reach_the_parsed_list():
    """136 R5. Empty/whitespace reason items are dropped at parse, so they never reach waive()."""
    report = parse_stage_report(
        "<<<LOREGARDEN_STAGE_REPORT>>>\n"
        '{"status": "pass", "confidence": 0.9,'
        ' "waivers": [{"name": "handoff:X incomplete", "reason": "  "},'
        ' {"name": "handoff:Y incomplete", "reason": ""}]}\n'
        "<<<END_STAGE_REPORT>>>"
    )
    assert report is not None
    assert report.status == "pass"
    assert report.waivers == []


def test_a_malformed_waivers_field_does_not_discard_report_status():
    """136 R5 / R11. A sidecar the agent got wrong must not take the status down with it."""
    report = parse_stage_report(
        "<<<LOREGARDEN_STAGE_REPORT>>>\n"
        '{"status": "fail", "confidence": 1.0, "reroute_to_stage": null,'
        ' "reroute_context": "x", "waivers": "not a list"}\n'
        "<<<END_STAGE_REPORT>>>"
    )
    assert report is not None
    assert report.status == "fail"
    assert report.waivers == []


def test_a_null_waivers_field_does_not_discard_report_status():
    report = parse_stage_report(
        "<<<LOREGARDEN_STAGE_REPORT>>>\n"
        '{"status": "pass", "confidence": 0.9, "waivers": null}\n'
        "<<<END_STAGE_REPORT>>>"
    )
    assert report is not None
    assert report.status == "pass"
    assert report.waivers == []


def test_waivers_as_a_list_of_strings_does_not_discard_report_status():
    report = parse_stage_report(
        "<<<LOREGARDEN_STAGE_REPORT>>>\n"
        '{"status": "fail", "confidence": 1.0,'
        ' "waivers": ["handoff:test_suite_complete incomplete"]}\n'
        "<<<END_STAGE_REPORT>>>"
    )
    assert report is not None
    assert report.status == "fail"
    assert report.waivers == []


def test_a_waiver_item_missing_reason_never_reaches_the_parsed_list():
    report = parse_stage_report(
        "<<<LOREGARDEN_STAGE_REPORT>>>\n"
        '{"status": "pass", "confidence": 0.9,'
        ' "waivers": [{"name": "handoff:test_suite_complete incomplete"}]}\n'
        "<<<END_STAGE_REPORT>>>"
    )
    assert report is not None
    assert report.status == "pass"
    assert report.waivers == []


def test_mixed_valid_and_malformed_waiver_items_keep_the_valid_ones():
    report = parse_stage_report(
        "<<<LOREGARDEN_STAGE_REPORT>>>\n"
        '{"status": "pass", "confidence": 0.9,'
        ' "waivers": ['
        '{"name": "handoff:test_suite_complete incomplete", "reason": "catalog item retired"},'
        '{"name": "handoff:test_all_runnable incomplete"},'
        '"not an object"'
        "]}\n"
        "<<<END_STAGE_REPORT>>>"
    )
    assert report is not None
    assert report.status == "pass"
    assert len(report.waivers) == 1
    assert report.waivers[0].name == "handoff:test_suite_complete incomplete"
    assert report.waivers[0].reason == "catalog item retired"


def test_a_malformed_criteria_field_costs_the_report_nothing():
    """The verdict is the one thing this parser exists to recover. A field an
    agent got wrong must not take the status down with it."""
    report = parse_stage_report(
        "<<<LOREGARDEN_STAGE_REPORT>>>\n"
        '{"status": "pass", "confidence": 1.0, "reroute_to_stage": null,'
        ' "reroute_context": "", "unmet_criteria": {"not": "a list"}}\n'
        "<<<END_STAGE_REPORT>>>"
    )
    assert report is not None
    assert report.status == "pass"
    assert report.unmet_criteria == []


def test_a_report_without_the_field_still_parses():
    """Every report emitted before today omits it."""
    report = parse_stage_report(
        "<<<LOREGARDEN_STAGE_REPORT>>>\n"
        '{"status": "pass", "confidence": 1.0, "reroute_to_stage": null, "reroute_context": ""}\n'
        "<<<END_STAGE_REPORT>>>"
    )
    assert report is not None
    assert report.unmet_criteria == []


def test_the_contract_schema_shows_the_field_it_asks_for():
    """The schema block listed four fields while the prose told agents to send a
    fifth. Whichever changes, they must not disagree again."""
    from pathlib import Path

    module = (
        Path(__file__).resolve().parents[2]
        / "agent_context/agents/common_assets/workflow_enforcement_v1.md"
    )
    text = module.read_text(encoding="utf-8")
    schema_line = next(line for line in text.splitlines() if line.startswith('{"status":'))
    assert "unmet_criteria" in schema_line, "the schema omits a field the prose demands"


def test_the_contract_schema_shows_the_waivers_sidecar():
    """136 R5. Producer waiver is StageReport.waivers; the contract schema must name it."""
    from pathlib import Path

    module = (
        Path(__file__).resolve().parents[2]
        / "agent_context/agents/common_assets/workflow_enforcement_v1.md"
    )
    text = module.read_text(encoding="utf-8")
    schema_line = next(line for line in text.splitlines() if line.startswith('{"status":'))
    assert "waivers" in schema_line, "producer waiver sidecar is missing from the contract schema"


@pytest.mark.parametrize(
    "field_name",
    ["status", "confidence", "reroute_to_stage", "reroute_context", "unmet_criteria"],
)
def test_a_null_optional_field_does_not_cost_the_report_its_verdict(field_name):
    """An agent that fills the block symmetrically must still be heard.

    A pydantic rewrite of this parser typed `status`, `confidence` and
    `reroute_context` as non-optional, so a report carrying
    `"reroute_context": null` — the shape the contract's own `null` examples
    invite — failed validation whole. The verdict was discarded, the stage
    failed closed as "the agent emitted no parseable block", and the operator
    was told nothing was reported when a `pass` at 0.88 was sitting in the
    buffer.
    """
    payload = {
        "status": "pass",
        "confidence": 0.88,
        "reroute_to_stage": None,
        "reroute_context": None,
        "blocked_kind": None,
        "human_task": None,
        "unmet_criteria": [],
    }
    payload[field_name] = None
    report = parse_stage_report(_wrap(json.dumps(payload)))

    if field_name == "status":
        # The verdict itself is the one thing a null cannot stand in for.
        assert report is None
        return
    assert report is not None
    assert report.status == "pass"
    assert report.confidence == (0.0 if field_name == "confidence" else 0.88)
    assert report.reroute_to_stage is None
    assert report.reroute_context == ""
    assert report.unmet_criteria == []


def test_a_null_confidence_keeps_a_rejection_routable():
    """`needs_rework` with a null confidence stalls rework rather than merely
    blocking a pass, so it is worth pinning on its own."""
    report = parse_stage_report(
        _wrap(
            json.dumps(
                {
                    "status": "needs_rework",
                    "confidence": None,
                    "reroute_to_stage": "implement",
                    "reroute_context": None,
                    "unmet_criteria": ["The endpoint returns 404."],
                }
            )
        )
    )
    assert report is not None
    assert report.status == "needs_rework"
    assert report.confidence == 0.0
    assert report.reroute_to_stage == "implement"
    assert report.unmet_criteria == ["The endpoint returns 404."]


# ---- what a run said last ----------------------------------------------------


def test_the_final_message_is_read_from_the_result_envelope():
    stdout = "\n".join(
        [
            '{"type": "assistant", "message": {"content": [{"type": "text", "text": "x"}]}}',
            "plain text a CLI interleaved",
            '{"type": "result", "subtype": "success", "result": "Waiting on the suite."}',
        ]
    )
    assert final_agent_message(stdout) == "Waiting on the suite."


def test_a_run_with_no_envelope_has_no_final_message():
    assert final_agent_message('plain output\n{"result": [1, 2]}') == ""


def test_the_symmetric_closer_agents_write_is_accepted():
    stdout = (
        "<<<LOREGARDEN_STAGE_REPORT>>>\n"
        '{"status": "needs_rework", "confidence": 0.8, "reroute_to_stage": "implement"}\n'
        "<<<END_LOREGARDEN_STAGE_REPORT>>>\n"
    )
    report = parse_stage_report(stdout)
    assert report is not None
    assert report.status == "needs_rework"
