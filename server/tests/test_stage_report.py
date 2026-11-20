import ast
import inspect
import json
from pathlib import Path

import pytest
from loregarden.models.domain.provenance import Provenance
from loregarden.models.domain.schemas import WorkflowStageDef
from loregarden.models.domain.tables import AgentRun
from loregarden.services import finding_bar
from loregarden.services.finding_bar import finding_record
from loregarden.services.stage_report import (
    MAX_NEXT_STAGE_NOTES_CHARS,
    Finding,
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


# --------------------------------------------------------------------------- #
# Review findings carry provenance (lg-code-knowledge-1084, AC10)
#
# An agent can honestly answer what inferred a finding and how sure it is. It
# cannot answer where that inferrer is WIRED — so `registered_at` is never
# parsed from the payload; `finding_bar.finding_record` stamps it at admission
# from facts the control plane already holds: the run's agent, and the stage's
# registered agent/skill entry.
# --------------------------------------------------------------------------- #


def _finding_payload(**extra) -> str:
    finding = {
        "summary": "the resolver returns an empty list when git fails",
        "blocking": True,
        "trigger": "an unresolvable --base",
        "path": "resolve_gate_scope -> run_git",
        "impact": "a pass is reported over a scope nothing read",
    }
    finding.update(extra)
    return json.dumps({"status": "needs_rework", "confidence": 0.7, "findings": [finding]})


def _only_finding(payload: str) -> Finding:
    report = parse_stage_report(_wrap(payload))
    assert report is not None
    (finding,) = report.findings
    return finding


def _run() -> AgentRun:
    return AgentRun(
        run_code="run_test",
        workspace_id="ws",
        agent_id="python_reviewer",
        stage_key="review-static",
    )


def _stage() -> WorkflowStageDef:
    return WorkflowStageDef(
        key="review-static",
        name="Static review",
        agent_id="python_reviewer",
        skill_name="review",
    )


def test_a_payload_carrying_the_two_honest_fields_round_trips_onto_the_finding():
    finding = _only_finding(_finding_payload(inferred_by="python_reviewer", confidence=0.4))

    assert finding.provenance is not None
    assert finding.provenance.inferred_by == "python_reviewer"
    assert finding.provenance.confidence == 0.4


def test_a_payload_carrying_only_inferred_by_records_the_omission():
    """It must not invent a site for a field the agent was never offered."""
    finding = _only_finding(_finding_payload(inferred_by="python_reviewer"))

    assert finding.provenance is not None
    assert finding.provenance.registered_at == ""
    assert finding.provenance.confidence is None


def test_a_payload_claiming_a_registration_site_does_not_get_one_from_the_parser():
    """`registered_at` is not a field of the payload; `extra='ignore'` drops it."""
    finding = _only_finding(
        _finding_payload(inferred_by="python_reviewer", registered_at="totally.made.up")
    )

    assert finding.provenance is not None
    assert finding.provenance.registered_at == ""


def test_a_payload_with_neither_field_carries_no_provenance_at_all():
    """Absent is honest; an empty triple would read as a recorded inference."""
    assert _only_finding(_finding_payload()).provenance is None


def test_an_unparseable_confidence_does_not_lose_the_finding():
    """Every payload field is optional text; a finding with a bad part is still
    a finding, recorded as such."""
    report = parse_stage_report(_wrap(_finding_payload(confidence="very")))
    assert report is not None
    assert len(report.findings) == 1


def test_finding_record_stamps_the_registration_site_at_admission():
    finding = _only_finding(_finding_payload(inferred_by="python_reviewer", confidence=0.4))
    record = finding_record(finding, _run(), _stage())

    site = record["provenance"]["registered_at"]
    assert site, "the control plane holds this fact; it must reach the history"
    assert "review-static" in site
    assert "python_reviewer" in site


def test_finding_record_keeps_the_two_fields_the_agent_answered():
    finding = _only_finding(_finding_payload(inferred_by="a_reviewer", confidence=0.4))
    record = finding_record(finding, _run(), _stage())

    assert record["provenance"]["inferred_by"] == "a_reviewer"
    assert record["provenance"]["confidence"] == 0.4


def test_finding_record_falls_back_to_the_run_s_agent_when_the_payload_said_nothing():
    finding = _only_finding(_finding_payload())
    record = finding_record(finding, _run(), _stage())

    assert record["provenance"]["inferred_by"] == "python_reviewer"
    assert record["provenance"]["registered_at"]


def test_the_registry_wins_over_a_site_the_finding_already_carries():
    """The rule, at its sharpest: a value that reached the dataclass by any
    route is replaced by the registry's answer, not merged with it."""
    finding = Finding(
        summary="a finding that named its own wiring",
        provenance=Provenance(inferred_by="someone", registered_at="not.where.it.is.wired"),
    )
    record = finding_record(finding, _run(), _stage())

    assert record["provenance"]["registered_at"] != "not.where.it.is.wired"
    assert "review-static" in record["provenance"]["registered_at"]


def test_an_unregistered_stage_records_the_omission_rather_than_inventing_a_site():
    finding = _only_finding(_finding_payload(inferred_by="python_reviewer"))
    record = finding_record(finding, _run(), None)

    assert record["provenance"]["registered_at"] == ""


def test_the_record_still_carries_everything_it_did_before():
    finding = _only_finding(_finding_payload(inferred_by="python_reviewer"))
    record = finding_record(finding, _run(), _stage())

    assert record["summary"] == "the resolver returns an empty list when git fails"
    assert record["blocking"] is True
    assert record["trigger"] == "an unresolvable --base"
    assert record["disposition"] is None


# --------------------------------------------------------------------------- #
# AC10 — "no overload and no shim", asserted rather than assumed
#
# Every test above passes against `finding_record(finding, run=None,
# stage=None)`, which is exactly the compatibility shim the internal posture
# forbids: the old three call sites keep working, nothing stamps a site, and the
# criterion reads as met. The signature and the call sites are the only places
# that distinguish a migration from a shim.
# --------------------------------------------------------------------------- #


def test_finding_record_takes_the_run_and_the_stage_as_required_arguments():
    signature = inspect.signature(finding_record)
    required = [
        name
        for name, parameter in signature.parameters.items()
        if parameter.default is inspect.Parameter.empty
    ]
    assert required == ["finding", "run", "stage"], signature


def test_calling_finding_record_the_old_way_is_a_failure_not_a_default():
    """A shim would silently record a finding with no provenance at all."""
    finding = _only_finding(_finding_payload(inferred_by="python_reviewer"))
    with pytest.raises(TypeError):
        finding_record(finding)


def test_every_call_site_in_finding_bar_passes_all_three():
    """AC10 names the four sites and requires them migrated in this change. A
    site left on the old shape is a finding recorded with no site, reached
    through the ordinary path nobody re-reads.
    """
    source = Path(finding_bar.__file__).read_text(encoding="utf-8")
    calls = [
        node
        for node in ast.walk(ast.parse(source))
        if type(node) is ast.Call
        and (getattr(node.func, "id", "") or getattr(node.func, "attr", "")) == "finding_record"
    ]

    assert calls, "the call sites moved; this test is measuring the wrong module"
    for call in calls:
        passed = len(call.args) + len(call.keywords)
        assert passed == 3, f"finding_bar.py:{call.lineno} passes {passed} arguments"


def test_the_stamped_site_names_the_registered_agent_not_the_run_s_agent():
    """`registered_at` is the STAGE's registered entry. A run dispatched to a
    pinned agent that differs from the stage's own registration must still
    resolve to the registration, or the field records who ran rather than where
    the classifier is wired — which is the thing the producer cannot answer.
    """
    run = AgentRun(
        run_code="run_test",
        workspace_id="ws",
        agent_id="a_pinned_reviewer",
        stage_key="review-static",
    )
    record = finding_record(_only_finding(_finding_payload()), run, _stage())

    assert "python_reviewer" in record["provenance"]["registered_at"]
    assert "a_pinned_reviewer" not in record["provenance"]["registered_at"]


def test_the_record_is_json_serialisable_with_the_triple_on_it():
    """It is written to the stage history; a dataclass left nested in the dict
    fails at the writer, after the stage has already reported success."""
    record = finding_record(
        _only_finding(_finding_payload(inferred_by="python_reviewer", confidence=0.4)),
        _run(),
        _stage(),
    )
    assert json.loads(json.dumps(record))["provenance"]["confidence"] == 0.4


def test_notes_for_the_next_stage_are_parsed_and_capped():
    note = "  see server/a.py:10  "
    report = parse_stage_report(
        _wrap(json.dumps({"status": "pass", "confidence": 0.8, "notes_for_next_stage": note}))
    )
    assert report is not None and report.notes_for_next_stage == "see server/a.py:10"

    long = parse_stage_report(
        _wrap(json.dumps({"status": "pass", "confidence": 0.8, "notes_for_next_stage": "n" * 5000}))
    )
    assert long is not None and len(long.notes_for_next_stage) == MAX_NEXT_STAGE_NOTES_CHARS


def test_unusable_notes_cost_the_report_nothing_but_the_notes():
    report = parse_stage_report(
        _wrap(json.dumps({"status": "pass", "confidence": 0.8, "notes_for_next_stage": ["a"]}))
    )
    assert report is not None and report.status == "pass" and report.notes_for_next_stage == ""
