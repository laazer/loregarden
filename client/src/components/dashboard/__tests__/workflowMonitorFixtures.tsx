/**
 * Shared harness for the WorkflowMonitorView suites.
 *
 * The suite outgrew one file (the 1200-line cap) when lg-milestone-that-1464
 * added the background-poll oracles, so it is split by subject — the panes and
 * their states in WorkflowMonitorView.test.tsx, everything about where focus
 * goes in WorkflowMonitorView.focus.test.tsx — over this one harness.
 *
 * Importers register the `api/client` mock themselves: the factory has to be
 * hoisted by the test module that uses it, and nothing here reads it.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";

import { WorkflowMonitorView } from "../WorkflowMonitorView";
import type { MonitorFinding } from "../../../api/types";

/**
 * All five states are pinned, because the one that matters most here is the one
 * a panel like this normally gets wrong: an error must not render as an empty
 * list. "The monitor has nothing to report" and "we could not ask the monitor"
 * are opposite facts about the pipeline, and collapsing them into one blank
 * pane is the failure this whole surface exists to stop.
 */

export const finding = (over: Partial<MonitorFinding> = {}): MonitorFinding => ({
  condition: "stage_thrash",
  ticket_id: "t1",
  stage_key: "script_review",
  summary: "script_review attempted 12x against a baseline of 2",
  evidence: {},
  occurrences: 1,
  first_seen: null,
  last_seen: null,
  ticket_title: "Fix the flaky review",
  ticket_external_id: "lor-x-1",
  ticket_state: "in_progress",
  workspace_slug: "loregarden",
  ...over,
});

/**
 * `setTicket` re-renders against the *same* QueryClient, which is the whole
 * point: the query key is the constant ["monitor-findings", "all"], so changing
 * the selected ticket starts no new read (lg-milestone-that-1435 AC6).
 *
 * `beside` renders a sibling control, for the one case that needs somewhere
 * else for focus to be (AC10). An <a> rather than a <button> because the theme
 * gate rejects a raw <button> on a changed line.
 */
export function renderView(ticketId: string | null = null, beside: ReactNode = null) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  const tree = (id: string | null) => (
    <MemoryRouter>
      <QueryClientProvider client={client}>
        {beside}
        <WorkflowMonitorView ticketId={id} />
      </QueryClientProvider>
    </MemoryRouter>
  );
  const result = render(tree(ticketId));
  return {
    ...result,
    setTicket: (id: string | null) => result.rerender(tree(id)),
    // The background poll, with no timer. `refetchInterval: 30_000` fires
    // exactly this call when it elapses, so a test that needs a second read
    // on a surface that renders no Refresh control asks for one directly
    // rather than driving jest's clock through react-query and RTL.
    poll: () =>
      act(async () => {
        await client.refetchQueries({ queryKey: ["monitor-findings", "all"] });
      }),
    // The same poll, started but not awaited — for a read the *test* settles.
    // `poll` cannot drive one: it does not return until the refetch has
    // finished, so a deferred rejection the test is holding would deadlock
    // against the suite's testTimeout. The `.catch` is load-bearing: a
    // rejection nobody awaits is an unhandled rejection, and the in-flight
    // render is the whole point of deferring, so the floating promise is
    // handed back for the test to await once it has settled it.
    startPoll: () => {
      let started: Promise<unknown> | undefined;
      act(() => {
        started = client
          .refetchQueries({ queryKey: ["monitor-findings", "all"] })
          .catch(() => {});
      });
      return started as Promise<unknown>;
    },
  };
}

/**
 * Put focus back on <body>, standing in for what a real browser does when a
 * focused <button> is disabled.
 *
 * `activeElement.blur()` cannot do it: jsdom treats a disabled button as a
 * non-focusable area, so `blur()` on one is a no-op and the element the click
 * focused stays active — an oracle built on it can never go green under any
 * implementation. `document.body.focus()` does not move it either, because
 * jsdom will not make <body> the active element on request. Focusing a
 * throwaway anchor and blurring *that* leaves activeElement on <body>, which is
 * the state a real browser leaves behind when it disables a focused control.
 */
export function dropFocus() {
  const pad = document.createElement("a");
  pad.href = "#pad";
  document.body.append(pad);
  pad.focus();
  pad.blur();
  pad.remove();
}

/** A promise the test settles, so an in-flight state can be asserted on. */
export function deferred<T>() {
  let settle!: {
    resolve: (value: T) => void;
    reject: (error: unknown) => void;
  };
  const promise = new Promise<T>((resolve, reject) => {
    settle = { resolve, reject };
  });
  return { promise, ...settle };
}

/** The one element the reason text lives in, and nothing else (AC8). */
export function reasonElement() {
  const reason = screen.getByRole("alert").querySelector(".wm-error-reason");
  if (!reason)
    throw new Error("the error pane rendered no .wm-error-reason element");
  return reason;
}

