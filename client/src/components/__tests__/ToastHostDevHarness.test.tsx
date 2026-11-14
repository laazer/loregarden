/**
 * lg-milestone-that-1316 AC30 — the dev-only browser harness, when it is on.
 *
 * `ToastHost` assigns `window.__loregardenPushToast` in a dev build and deletes
 * it on unmount. AC5's only route to a real render is that handle —
 * `client/scripts/visual-qa.mjs` has no toast trigger and nothing else exposes
 * the store on `window` — so a harness that silently stopped assigning would
 * take the browser measurement AC41 requires with it, and the implementer would
 * find out by having no way to render a 269-character message.
 *
 * It needs its own file. `IS_DEV` is read through `src/api/viteEnv` (AC29: the
 * single place `import.meta` is touched), `jest.config.cjs` maps that module to
 * `src/test/viteEnvMock.ts` where it is `false`, and a `jest.mock` factory is
 * hoisted to module scope — so IS_DEV cannot be true for one case and false for
 * another inside one suite. `jest.resetModules` is not a way around it either:
 * re-requiring `ToastHost` after a reset pulls a second copy of React and the
 * hook dispatcher no longer matches the react-dom Testing Library is holding.
 *
 * Added by test-break, reported under AC56.
 */

jest.mock("../../api/viteEnv", () => ({
  VITE_API_BASE: "http://127.0.0.1:8000",
  IS_DEV: true,
}));

import { act, render, screen } from "@testing-library/react";

import { pushToast, useToastStore } from "../../state/toastStore";
import { ToastHost } from "../ToastHost";

import { CLASS_B_MESSAGE } from "./toastFixtures";

beforeEach(() => {
  useToastStore.getState().clear();
  Reflect.deleteProperty(window, "__loregardenPushToast");
});

it("hands the real pushToast to the window in a dev build", () => {
  render(<ToastHost />);

  // The store's own function, not a wrapper: a Playwright script calls it with
  // a ToastInput and expects the store's return value, the toast's id.
  expect(window.__loregardenPushToast).toBe(pushToast);
});

it("drives a real toast through the handle, which is what it exists for (AC5)", () => {
  render(<ToastHost />);

  act(() => {
    window.__loregardenPushToast?.({
      tone: "error",
      title: "Refresh from target failed",
      message: CLASS_B_MESSAGE,
      duration: 0,
    });
  });

  expect(screen.getByRole("alert")).toHaveTextContent("Refresh from target failed");
  expect(document.querySelector(".toast__message")?.textContent).toBe(CLASS_B_MESSAGE);
});

it("takes the handle away again on unmount", () => {
  const { unmount } = render(<ToastHost />);
  expect(window.__loregardenPushToast).toBeDefined();

  unmount();

  // Without the delete, a dev session accumulates a stale handle across
  // remounts and a harness script drives a host that is no longer on screen.
  expect(window.__loregardenPushToast).toBeUndefined();
  expect("__loregardenPushToast" in window).toBe(false);
});

it("is the only thing the harness puts on the window", () => {
  const before = new Set(Object.keys(window));
  const { unmount } = render(<ToastHost />);

  const added = Object.keys(window).filter((key) => !before.has(key));
  expect(added).toEqual(["__loregardenPushToast"]);

  unmount();
});
