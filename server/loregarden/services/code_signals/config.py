"""The three knobs the computation takes (lg-code-knowledge-299, AC4).

Frozen and caller-supplied. No `Workspace` columns: that table already carries
about thirty, and nothing yet needs a per-workspace value. Every default below
carries the measurement that chose it.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class SignalConfig(BaseModel):
    """How far back to look, and what counts as a co-change pair."""

    model_config = ConfigDict(frozen=True)

    # A TIME window, not a commit count: measured 2026-11-16, this repository
    # holds 1027 commits and parses its whole history in 2.05s, so a count
    # would buy nothing and would silently SHORTEN the window as the repo
    # speeds up. 180 days over a 400-commit stretch parses in 0.88s.
    # Bounded, because git silently mis-answers a window it cannot express:
    # measured, `--since=25000 days ago` lands before the Unix epoch and
    # returns NO commits where 20000 returns every one of them, which the
    # package would otherwise report as NO_COMMITS_IN_WINDOW rather than as an
    # error. Non-positive values read a window that has not happened yet.
    window_days: int = Field(default=180, ge=1, le=20_000)

    # Measured: at support >= 3 this repository yields 4747 pairs with the
    # per-commit cap and 12402 without it. Below 3 a pair is one coincidence
    # and a sweep away from noise.
    min_support: int = 3

    # Measured: 62% of the pairs surviving support >= 3 exist only because of
    # commits of 174 / 148 / 143 files. The orchestrator commits the whole
    # working tree here, so sweeping commits are house style rather than an
    # anomaly, and a cap is the only thing that separates coupling from
    # co-presence. 50 sits above every hand-made commit measured.
    max_files_per_commit: int = 50


__all__ = ["SignalConfig"]
