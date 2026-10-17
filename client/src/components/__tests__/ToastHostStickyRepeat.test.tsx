/**
 * UD1 (lg-external-harness-1212) — the operator's half.
 *
 * Bounding the claim set must not shorten the toast's life or change how it
 * leaves. A1 and A6 are both satisfiable by making the sticky `queue_error`
 * toast expire, auto-dismiss or evict itself after K repeats, and the store's
 * `DEFAULT_DURATION` comment pins error stickiness to WCAG 2.2.1 (Timing
 * Adjustable, Level A) in those words. The store-side half of UD1 lives in
 * `state/__tests__/toastStore.test.ts`; what the operator can actually reach
 * is only observable through `ToastHost`, which is why it is asserted here.
 */
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { pushToast, useToastStore } from "../../state/toastStore";
import { ToastHost } from "../ToastHost";

const QUEUE_ERROR = {
  tone: "error" as const,
  title: "Queue error",
  message: "The queue reported a failure.",
  duration: 0,
};

// Large enough to falsify the post-lg-external-harness-1209 pre-fix behaviour
// (which reaches one claim per push) and the same order of magnitude A1 uses.
const REPEATS = 1000;

beforeEach(() => {
  useToastStore.getState().clear();
});

it("UD1 — a server-driven repeat is still one alert, still sticky, after N pushes", () => {
  render(<ToastHost />);

  act(() => {
    for (let i = 0; i < REPEATS; i += 1) pushToast({ ...QUEUE_ERROR });
  });

  const alerts = screen.getAllByRole("alert");
  expect(alerts).toHaveLength(1);
  expect(alerts[0]).toHaveTextContent("Queue error");
  expect(alerts[0]).toHaveTextContent("The queue reported a failure.");
  expect(useToastStore.getState().toasts[0].duration).toBe(0);
});

it("UD1 — one focusable close control, named, removing it on the FIRST press", async () => {
  // Not "a control exists": the claim count must not turn the operator's close
  // route into one press per claim, which is the WCAG 2.2.1 regression a
  // claim-consulting `dismiss` would be — on the ticket that exists to satisfy
  // 2.2.1.
  const user = userEvent.setup();
  render(<ToastHost />);

  act(() => {
    for (let i = 0; i < REPEATS; i += 1) pushToast({ ...QUEUE_ERROR });
  });

  const close = screen.getAllByRole("button", { name: "Dismiss: Queue error" });
  expect(close).toHaveLength(1);
  close[0].focus();
  expect(close[0]).toHaveFocus();

  await user.click(close[0]);

  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(useToastStore.getState().toasts).toHaveLength(0);
});
