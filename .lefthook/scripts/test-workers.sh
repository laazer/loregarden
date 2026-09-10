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

#: Half the cores, always. The other half is for the gateway, the editor and the
#: agent processes.
#:
#: There WAS a rule here that collapsed to 2 workers when the box was already
#: loaded, and it was a bad trade. The pre-push suite falls back to the full
#: ~3,700 tests whenever a change cannot be mapped to an import graph - which
#: includes editing these very scripts - and that full run took 27:47 at one
#: worker per core. At 2 workers on a loaded box it does not finish: two pushes
#: were killed by their own 30-minute bounds, and the symptom (a hook that
#: prints nothing for an hour, because lefthook buffers a command's output until
#: it exits) reads exactly like a deadlock.
#:
#: A cap that turns a 28-minute suite into an unbounded one is a worse failure
#: than the contention it was preventing. `nice` is the right instrument for
#: "do not starve the gateway": a niced run yields the CPU when something
#: latency-sensitive wants it, and still uses the box when nothing does.
#: Throttling throughput as well was belt, braces, and a rope round the ankles.
TEST_WORKERS="${LOREGARDEN_TEST_WORKERS:-}"
if [ -z "$TEST_WORKERS" ]; then
  _cores="$(_tw_cores)"
  TEST_WORKERS=$(( _cores / 2 ))
  [ "$TEST_WORKERS" -lt 2 ] && TEST_WORKERS=2
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
