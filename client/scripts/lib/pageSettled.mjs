/**
 * When a page of the app has finished loading, for scripts that screenshot it
 * (`visual-qa.mjs`, `review-capture.mjs`). Pass TRACK_FETCHES to
 * `page.addInitScript` and wait on SETTLED with `page.waitForFunction`.
 */

// A surface is settled when the app has stopped asking its API for anything and
// stopped adding or removing elements (no fetch in flight and no structural DOM
// change for QUIET_MS; text-only updates such as a ticking clock do not count),
// and nothing on it says it is still loading (`aria-busy="true"`, which
// PaneSkeleton and the pending panes set). The DOM half closes the gap between a
// pane mounting and its query's effect firing the fetch. That is the
// app's own signal, where `networkidle` waits on every request the page makes:
// under machine load it timed out after 30s on whichever surfaces were slowest
// that run, and each such timeout used to fail the run as if the page were broken.
export const QUIET_MS = 500;
export const TRACK_FETCHES = `(() => {
  const state = { inflight: 0, last: performance.now() };
  window.__visualQa = state;
  const fetch = window.fetch.bind(window);
  window.fetch = (...args) => {
    state.inflight += 1;
    state.last = performance.now();
    return fetch(...args).finally(() => {
      state.inflight -= 1;
      state.last = performance.now();
    });
  };
  new MutationObserver(() => {
    state.last = performance.now();
  }).observe(document, { childList: true, subtree: true });
})()`;
export const SETTLED = `(() => {
  const s = window.__visualQa;
  const root = document.getElementById("root");
  return document.readyState === "complete" && root !== null && root.childElementCount > 0 &&
    s.inflight === 0 && performance.now() - s.last >= ${QUIET_MS} &&
    document.querySelector('[aria-busy="true"]') === null;
})()`;
