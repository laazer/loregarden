import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { LazyMotion, MotionConfig, domAnimation } from "motion/react";

import { pushToast, useToastStore } from "../../state/toastStore";
import { ToastHost } from "../ToastHost";

beforeEach(() => {
  useToastStore.getState().clear();
});

it("shows no toast until something fails", () => {
  render(<ToastHost />);
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(screen.queryByRole("status")).not.toBeInTheDocument();
});

it("removes a dismissed toast once its exit has played, under the app's motion providers", async () => {
  const user = userEvent.setup();
  render(
    <LazyMotion features={domAnimation} strict>
      <MotionConfig reducedMotion="user">
        <ToastHost />
      </MotionConfig>
    </LazyMotion>,
  );

  act(() => {
    pushToast({ tone: "error", title: "Save file failed" });
  });
  await user.click(screen.getByRole("button", { name: "Dismiss: Save file failed" }));

  await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
});

it("announces a failure as an alert", () => {
  render(<ToastHost />);

  act(() => {
    pushToast({ tone: "error", title: "Start run failed", message: "worktree is dirty" });
  });

  const alert = screen.getByRole("alert");
  expect(alert).toHaveTextContent("Start run failed");
  expect(alert).toHaveTextContent("worktree is dirty");
});

it("dismisses on the close button", async () => {
  const user = userEvent.setup();
  render(<ToastHost />);

  act(() => {
    pushToast({ tone: "error", title: "Save file failed" });
  });

  await user.click(screen.getByRole("button", { name: "Dismiss: Save file failed" }));

  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(useToastStore.getState().toasts).toHaveLength(0);
});

it("auto-dismisses after its duration", () => {
  jest.useFakeTimers();
  try {
    render(<ToastHost />);
    act(() => {
      pushToast({ tone: "info", title: "Queued", duration: 2000 });
    });
    expect(screen.getByRole("status")).toBeInTheDocument();

    act(() => {
      jest.advanceTimersByTime(2000);
    });
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  } finally {
    jest.useRealTimers();
  }
});

it("keeps a zero-duration toast up until it is dismissed", () => {
  jest.useFakeTimers();
  try {
    render(<ToastHost />);
    act(() => {
      pushToast({ tone: "error", title: "Reload failed", duration: 0 });
    });

    act(() => {
      jest.advanceTimersByTime(60_000);
    });
    expect(screen.getByRole("alert")).toBeInTheDocument();
  } finally {
    jest.useRealTimers();
  }
});

it("S4.3 — a repeat of the same failure refreshes the node rather than replacing it", () => {
  // UX13. A fresh id would make React key a new element, and the node would go
  // out from under a pointer mid-selection — which is the whole defect this
  // ticket is about, re-introduced by the dedupe that was meant to help.
  render(<ToastHost />);
  act(() => {
    pushToast({ tone: "error", title: "Start run failed", message: "worktree is dirty" });
  });
  const before = screen.getByRole("alert");

  act(() => {
    pushToast({ tone: "error", title: "Start run failed", message: "worktree is dirty" });
  });

  const after = screen.getByRole("alert");
  expect(after).toBe(before);
  expect(screen.getAllByRole("alert")).toHaveLength(1);
});

it("S4.3 — a different failure does get its own node", () => {
  render(<ToastHost />);
  act(() => {
    pushToast({ tone: "error", title: "Start run failed", message: "worktree is dirty" });
  });
  const first = screen.getByRole("alert");

  act(() => {
    pushToast({ tone: "error", title: "Delete session failed", message: "a path" });
  });

  const alerts = screen.getAllByRole("alert");
  expect(alerts).toHaveLength(2);
  expect(alerts[0]).toBe(first);
});

it("S13 — the same failure raised twice still closes on ONE press of the close button", () => {
  // A regression pin for the shape lg-external-harness-1209 rejected. The
  // alternative to per-call claim ids was a refcount consulted by `dismiss`,
  // and `ToastHost` binds one `dismiss` for both the close button and the
  // auto-dismiss deadline — so a twice-raised failure would have needed two
  // presses to go away. That is a WCAG 2.2.1 regression on the ticket that
  // exists to satisfy 2.2.1, and it would ship with every stated criterion
  // green. This passes today and must keep passing.
  render(<ToastHost />);
  act(() => {
    pushToast({ tone: "error", title: "Start run failed", message: "worktree is dirty" });
    pushToast({ tone: "error", title: "Start run failed", message: "worktree is dirty" });
  });
  expect(screen.getAllByRole("alert")).toHaveLength(1);

  act(() => {
    screen.getByRole("button", { name: "Dismiss: Start run failed" }).click();
  });

  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(useToastStore.getState().toasts).toHaveLength(0);
});

it("test-break — an overlapping push adds nothing to what the toast announces", () => {
  // UX5 forbids a "(2)", a counter, or a live claim count rendered anywhere,
  // and it is only observable here: every store-level assertion for it reads
  // `tone`/`title`/`message`, which a `ToastItem` appending the claim count to
  // what it renders leaves untouched. The toast is also in an `aria-live`
  // region, so text that grows on a second push is re-announced to a screen
  // reader for an event the operator cannot act on.
  render(<ToastHost />);
  act(() => {
    pushToast({ tone: "info", title: "Preparing terminal command", message: "its worktree", duration: 0 });
  });
  const announced = screen.getByRole("status").textContent;

  act(() => {
    pushToast({ tone: "info", title: "Preparing terminal command", message: "its worktree", duration: 0 });
  });

  expect(screen.getAllByRole("status")).toHaveLength(1);
  expect(screen.getByRole("status").textContent).toBe(announced);
  expect(
    screen.getByRole("button", { name: "Dismiss: Preparing terminal command" }),
  ).toBeEnabled();
});
