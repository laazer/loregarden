import json

from loregarden.services.stage_report import parse_stage_report, stage_report_artifact_content


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
        '{"status": "fail", "confidence": 0.8, "reroute_to_stage": "implementation", '
        '"reroute_context": "missing edge case coverage"}'
    )
    report = parse_stage_report(stdout)
    assert report is not None
    assert report.status == "fail"
    assert report.reroute_to_stage == "implementation"
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
            '{"status": "fail", "confidence": 0.7, "reroute_to_stage": "implementation", '
            '"reroute_context": "regression in acid weak point"}'
        )
    )
    assert report is not None
    content = stage_report_artifact_content("script_review", report)
    assert content["stage_key"] == "script_review"
    assert content["status"] == "fail"
    assert content["reroute_to_stage"] == "implementation"
    row_keys = {row["k"] for row in content["rows"]}
    assert row_keys == {"status", "confidence", "reroute_to_stage", "reroute_context"}


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
