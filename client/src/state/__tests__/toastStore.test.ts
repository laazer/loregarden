import { ApiError } from "../../api/client";
import { describeError, toastActionFailed, useToastStore } from "../toastStore";

beforeEach(() => {
  useToastStore.getState().clear();
});

it("reports an action failure with the server's detail", () => {
  toastActionFailed("Delete ticket", new ApiError(409, "ticket has an active run"));

  const [toast] = useToastStore.getState().toasts;
  expect(toast.tone).toBe("error");
  expect(toast.title).toBe("Delete ticket failed");
  expect(toast.message).toBe("ticket has an active run");
});

it("falls back to the status when the server sends no detail", () => {
  expect(describeError(new ApiError(500, ""))).toBe("Request failed (500)");
});

it("describes a thrown non-error rather than rendering nothing", () => {
  expect(describeError(undefined)).toBe("Unexpected error");
  expect(describeError("offline")).toBe("offline");
});

it("replaces a repeat of the same failure instead of stacking copies", () => {
  const first = toastActionFailed("Start run", new Error("boom"));
  const second = toastActionFailed("Start run", new Error("boom"));

  const { toasts } = useToastStore.getState();
  expect(toasts).toHaveLength(1);
  // A new id, so the auto-dismiss timer restarts: the failure is still current.
  expect(toasts[0].id).toBe(second);
  expect(second).not.toBe(first);
});

it("keeps distinct failures side by side, capped at four", () => {
  for (const n of [1, 2, 3, 4, 5]) {
    toastActionFailed(`Action ${n}`, new Error("boom"));
  }

  const titles = useToastStore.getState().toasts.map((t) => t.title);
  expect(titles).toEqual(["Action 2 failed", "Action 3 failed", "Action 4 failed", "Action 5 failed"]);
});

it("dismisses only the toast asked for", () => {
  const keep = useToastStore.getState().push({ title: "Keep" });
  const drop = useToastStore.getState().push({ title: "Drop" });

  useToastStore.getState().dismiss(drop);

  expect(useToastStore.getState().toasts.map((t) => t.id)).toEqual([keep]);
});
