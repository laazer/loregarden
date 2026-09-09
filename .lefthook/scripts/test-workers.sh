#!/usr/bin/env bash
# How many test workers a pre-push run may take, and at what priority.
#
# `pytest -n auto` and bare `npm test` each size themselves to the whole
# machine: on a 10-core box that is 10 pytest workers and 9 jest workers. That
# arithmetic assumes the box is otherwise idle, and here it never is — the same
# machine serves the MCP gateway the agents talk to, and several agent sessions
# work in sibling worktrees at once, each able to start its own hook.
#
# Observed on 2026-09-09: load average 158, with port 8000 accepting TCP while
# /health returned nothing for 30s or more. Stage agents finished their work and
# then timed out writing it back. A hook meant to protect the branch was taking
# the control plane down, which is a bad trade at any test count.
#
# Two levers, both modest:
#   - a worker budget that leaves cores for everything else, and collapses to a
#     minimum when the box is ALREADY loaded (the concurrent-hook case, which is
#     the one that actually hurt);
#   - `nice`, so that when the box is oversubscribed anyway the gateway wins the
#     scheduler against the test run rather than losing to it.
#
# LOREGARDEN_TEST_WORKERS overrides the budget outright, for a machine where the
# operator knows better than this heuristic.

_tw_cores() {
  if command -v sysctl >/dev/null 2>&1; then
    sysctl -n hw.ncpu 2>/dev/null && return
  fi
  if command -v nproc >/dev/null 2>&1; then
    nproc 2>/dev/null && return
  fi
  echo 4
}

_tw_load() {
  # 1-minute load average, integer part. Unavailable is treated as 0 rather than
  # as "busy": refusing to parallelise because we could not measure would be a
  # silent, permanent slowdown.
  uptime 2>/dev/null | sed 's/.*load averages*: //' | awk '{printf "%d", $1}' || echo 0
}

#: Cores left for the gateway, the editor and the agent processes. Half the box
#: is a deliberate over-allocation to everything else: the tests are the thing
#: that can wait, and a hook that finishes slightly slower is invisible next to
#: a control plane that stops answering.
TEST_WORKERS="${LOREGARDEN_TEST_WORKERS:-}"
if [ -z "$TEST_WORKERS" ]; then
  _cores="$(_tw_cores)"
  _load="$(_tw_load)"
  TEST_WORKERS=$(( _cores / 2 ))
  [ "$TEST_WORKERS" -lt 2 ] && TEST_WORKERS=2
  if [ "$_load" -ge "$_cores" ]; then
    # Already oversubscribed — usually a sibling worktree's hook, or a fleet of
    # agents. Adding half a box of workers to a saturated box is how the 158
    # happened. Take the floor and get out of the way.
    TEST_WORKERS=2
    echo "pre-push: load ${_load} on ${_cores} cores — capping tests at ${TEST_WORKERS} workers" >&2
  fi
fi
export TEST_WORKERS

#: Prefix for the actual test process. The gateway is latency-sensitive and the
#: test run is not, so the test run yields.
# Not exported: bash cannot export an array. This file is SOURCED by the
# runners, so the array reaches them directly — an `export` here would look like
# it travelled further than it does.
TEST_NICE=()
if command -v nice >/dev/null 2>&1; then
  TEST_NICE=(nice -n 10)
fi
