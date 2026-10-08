import {
  LOG_FEED_EMPTY,
  LOG_FEED_ERROR,
  LOG_FEED_LOADING,
  LOG_FEED_RECONNECTING,
  detachedEmptyText,
  logFeedState,
} from "../logFeedState";

// lg-durable-remote-336, AC30/AC31/AC32. The run log modal and the live lane
// log are separate components rendering ONE payload, and their five states must
// stay identical — a divergence is how one pane keeps the old behaviour and
// blanks on a restart while the other does not. So the decision lives here,
// once, and both import it.
//
// The state that matters most: a failed refetch must never blank a pane that
// already has lines. Today both fall to "Could not load this run's log." at
// exactly the 10-30 seconds this ticket's whole point is that the run is still
// alive and still working.

function input(overrides: Partial<Parameters<typeof logFeedState>[0]> = {}) {
  return {
    lines: [],
    live: null,
    isPending: false,
    isError: false,
    isRunning: false,
    transport: "",
    ...overrides,
  };
}

it("shows the loading text on the first fetch", () => {
  // AC31: one indexed read, so no skeleton.
  expect(logFeedState(input({ isPending: true }))).toBe("loading");
  expect(LOG_FEED_LOADING).toBe("Loading log…");
});

it("never shows anything loading-shaped on a later poll", () => {
  // react-query keeps `data` across a refetch; `isPending` is first-fetch only.
  expect(logFeedState(input({ lines: [{}], isPending: false }))).toBe("feed");
});

it("errors to a blank pane only when there is nothing on screen", () => {
  expect(logFeedState(input({ isError: true }))).toBe("error");
  expect(LOG_FEED_ERROR).toBe("Could not load this run’s log.");
});

it("keeps the last-known feed when a refetch fails with lines present", () => {
  // AC30, the defect. The condition is `lines.length === 0 && !live`, and it is
  // written here rather than in each pane.
  expect(logFeedState(input({ isError: true, lines: [{}, {}] }))).toBe(
    "reconnecting",
  );
  expect(LOG_FEED_RECONNECTING).toBe("Reconnecting to the control plane…");
});

it("treats a live partial line as content worth keeping on an error", () => {
  // A run that has streamed only a partial message has something on screen.
  expect(
    logFeedState(input({ isError: true, live: "thinking about the…" })),
  ).toBe("reconnecting");
});

it("says a finished run recorded nothing", () => {
  // AC32. 1,346 of 2,285 runs have no log lines at all.
  expect(logFeedState(input({ isRunning: false }))).toBe("empty");
  expect(LOG_FEED_EMPTY).toBe("No log recorded for this run.");
});

it("says a running detached run has not spoken yet", () => {
  // AC32. Otherwise a detached run that has not spoken is indistinguishable
  // from a dead one — which is the reading this ticket exists to remove.
  expect(logFeedState(input({ isRunning: true, transport: "tmux" }))).toBe(
    "empty-detached",
  );
  expect(detachedEmptyText("tmux")).toBe(
    "No output yet — the agent is running detached on tmux.",
  );
  expect(detachedEmptyText("file")).toBe(
    "No output yet — the agent is running detached on file.",
  );
});

it("falls back to the finished-run wording when a running run has no transport", () => {
  // AC28's direction: never guess "file" for a row that does not say.
  expect(logFeedState(input({ isRunning: true, transport: "" }))).toBe("empty");
});

it("renders the feed whenever there is anything to render", () => {
  expect(logFeedState(input({ lines: [{}] }))).toBe("feed");
  expect(logFeedState(input({ live: "mid-sentence" }))).toBe("feed");
});

it("ranks loading above error, and error above empty", () => {
  // The first fetch of a run that errors must not read as "recorded nothing".
  expect(logFeedState(input({ isPending: true, isError: true }))).toBe(
    "loading",
  );
  expect(
    logFeedState(input({ isError: true, isRunning: true, transport: "tmux" })),
  ).toBe("error");
});

// --- lg-durable-remote-336, AC30/AC32: the whole input space, not 11 cells --
//
// The cases above pin the states that matter one at a time. The two panes read
// one payload and must stay identical, so the DECISION has to be total: every
// combination of the five inputs resolves, and the three invariants hold over
// all of them. The one that matters is the middle assertion — a pane that
// already has lines may never show the blank error state, which is the defect
// AC30 names and the one that fires during exactly the 10-30 seconds this
// ticket exists for.

const BOOLS = [false, true];
const TRANSPORTS = ["", "tmux", "file"];

it("resolves every combination of its inputs to a known state", () => {
  const states = new Set<string>();
  for (const isPending of BOOLS) {
    for (const isError of BOOLS) {
      for (const isRunning of BOOLS) {
        for (const transport of TRANSPORTS) {
          for (const lines of [[], [{}], [{}, {}]]) {
            for (const live of [null, "", "mid-sentence"]) {
              const state = logFeedState({
                lines,
                live,
                isPending,
                isError,
                isRunning,
                transport,
              });
              states.add(state);
              const where = JSON.stringify({
                isPending,
                isError,
                isRunning,
                transport,
                lines: lines.length,
                live,
              });

              expect([
                "loading",
                "error",
                "reconnecting",
                "empty",
                "empty-detached",
                "feed",
              ]).toContain(state);

              // AC30: content on screen is never replaced by the blank error.
              if (lines.length > 0 || live) {
                expect(state).not.toBe("error");
                expect(state).not.toBe("empty");
                expect(state).not.toBe("empty-detached");
              }
              // AC31: nothing loading-shaped once the first fetch is done.
              if (!isPending) {
                expect(state).not.toBe("loading");
              }
              // AC28: the detached wording is only ever used when the row says
              // which transport — never guessed for the 1,449 rows that do not.
              if (state === "empty-detached") {
                expect(transport).not.toBe("");
                expect(isRunning).toBe(true);
                expect(where).toContain('"lines":0');
              }
            }
          }
        }
      }
    }
  }

  // The sweep has to actually reach every branch, or the invariants above are
  // satisfied by a decision that only ever returns one thing.
  expect([...states].sort()).toEqual([
    "empty",
    "empty-detached",
    "error",
    "feed",
    "loading",
    "reconnecting",
  ]);
});
