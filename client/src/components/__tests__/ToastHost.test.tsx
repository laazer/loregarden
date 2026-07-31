import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { pushToast, useToastStore } from "../../state/toastStore";
import { ToastHost } from "../ToastHost";

beforeEach(() => {
  useToastStore.getState().clear();
});

it("renders nothing until something fails", () => {
  const { container } = render(<ToastHost />);
  expect(container).toBeEmptyDOMElement();
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
