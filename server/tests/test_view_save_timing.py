"""A slow view save says where its time went.

Live view PATCHes took 6 to 11 seconds and nothing recorded whether they waited
for a worker thread, for SQLite's write lock, or on the commit. These pin that a
slow save reports each phase, with the outcome it answered with, and that a
quiet save stays out of the log.

The threshold is patched to zero rather than the save made slow: what is under
test is that the phases are measured and reported, and a sleep would only make
the suite slower at proving it.
"""

import logging
from unittest import mock

import pytest
from fastapi.testclient import TestClient
from loregarden.models.domain import ViewSavePhase
from loregarden.services import view_save_timing

VIEWS = "/api/workspaces/loregarden/views"
TIMING_LOGGER = view_save_timing.logger.name


def _canvas(client: TestClient) -> str:
    res = client.post(
        VIEWS,
        json={
            "title": "Canvas",
            "icon": "",
            "layout": {"kind": "canvas", "containers": {}, "items": []},
        },
    )
    assert res.status_code == 201, res.text
    return res.json()["id"]


def _save_reports(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == TIMING_LOGGER and hasattr(r, "view_save_phases")]


def test_a_slow_save_reports_every_phase_and_its_outcome(
    client: TestClient, caplog: pytest.LogCaptureFixture
):
    view_id = _canvas(client)

    with (
        mock.patch.object(view_save_timing, "SLOW_SAVE_SECONDS", 0.0),
        caplog.at_level(logging.WARNING),
    ):
        res = client.patch(
            f"{VIEWS}/{view_id}", json={"viewport": {"pan_x": 1.0, "pan_y": 2.0, "zoom": 1.0}}
        )

    assert res.status_code == 200, res.text
    [report] = _save_reports(caplog)
    assert report.levelno == logging.WARNING
    assert set(report.view_save_phases) == {phase.value for phase in ViewSavePhase}
    assert all(seconds >= 0 for seconds in report.view_save_phases.values())
    assert report.view_save_seconds >= sum(report.view_save_phases.values()) - 0.001
    assert report.args[0] == view_id
    assert report.args[2] == 200


def test_rounded_phases_never_sum_past_the_rounded_total(caplog: pytest.LogCaptureFixture):
    """CI's numbers: phases of 0.51, 1.51 and 6.51ms each round up, to 0.010s
    in all, inside a 8.6ms save that rounds to 0.009s."""
    timer = view_save_timing.ViewSaveTimer(received_at=0.0)
    clock = [0.00051, 0.00202, 0.00853, 0.0086]  # three phase ends, then the report
    with (
        mock.patch.object(view_save_timing.time, "perf_counter", side_effect=clock),
        mock.patch.object(view_save_timing, "SLOW_SAVE_SECONDS", 0.0),
        caplog.at_level(logging.WARNING),
    ):
        for phase in (ViewSavePhase.THREAD_WAIT, ViewSavePhase.READ, ViewSavePhase.WRITE):
            timer.mark(phase)
        timer.report("view-1", 200)

    [report] = _save_reports(caplog)
    assert report.view_save_seconds == 0.009
    assert sum(report.view_save_phases.values()) <= report.view_save_seconds + 1e-9


def test_a_slow_refusal_is_reported_as_the_refusal_it_was(
    client: TestClient, caplog: pytest.LogCaptureFixture
):
    """A 404 that took seconds is still worth knowing about, and must not read as a slow success."""
    with (
        mock.patch.object(view_save_timing, "SLOW_SAVE_SECONDS", 0.0),
        caplog.at_level(logging.WARNING),
    ):
        res = client.patch(f"{VIEWS}/no-such-view", json={"title": "Renamed"})

    assert res.status_code == 404
    [report] = _save_reports(caplog)
    assert report.args[2] == 404


def test_a_quiet_save_stays_out_of_the_log(client: TestClient, caplog: pytest.LogCaptureFixture):
    view_id = _canvas(client)

    with caplog.at_level(logging.DEBUG):
        res = client.patch(f"{VIEWS}/{view_id}", json={"title": "Renamed"})

    assert res.status_code == 200, res.text
    assert _save_reports(caplog) == []
