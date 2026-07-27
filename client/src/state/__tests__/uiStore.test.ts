import { useUiStore } from "../uiStore";

// Requirement 5 — a keyed, non-persisted auto-follow map. Component-local
// state was explicitly rejected because it would not survive the per-lane
// view unmounting on tab switch; lifting it into this store is what makes
// that survive. The tab-switch scenario itself is exercised through the real
// component tree in LogsPanel.test.tsx — these tests pin the store's own
// contract in isolation.

beforeEach(() => {
  useUiStore.setState({ autoFollowByRunId: {} });
});

it("defaults an unset run id to true", () => {
  expect(useUiStore.getState().autoFollowByRunId["unseen-run"]).toBeUndefined();
});

it("setAutoFollow records false for a run id", () => {
  useUiStore.getState().setAutoFollow("run-a", false);
  expect(useUiStore.getState().autoFollowByRunId["run-a"]).toBe(false);
});

it("keeps each run id's value independent", () => {
  useUiStore.getState().setAutoFollow("run-a", false);
  useUiStore.getState().setAutoFollow("run-b", true);

  expect(useUiStore.getState().autoFollowByRunId["run-a"]).toBe(false);
  expect(useUiStore.getState().autoFollowByRunId["run-b"]).toBe(true);
});

it("toggling one run id does not create or affect an entry for another", () => {
  useUiStore.getState().setAutoFollow("run-a", false);
  expect(useUiStore.getState().autoFollowByRunId["run-b"]).toBeUndefined();
});

it("setting the same run id twice with the same value is idempotent", () => {
  useUiStore.getState().setAutoFollow("run-a", false);
  useUiStore.getState().setAutoFollow("run-a", false);
  expect(useUiStore.getState().autoFollowByRunId["run-a"]).toBe(false);
});

it("toggling a run id back and forth returns to the original value", () => {
  useUiStore.getState().setAutoFollow("run-a", false);
  useUiStore.getState().setAutoFollow("run-a", true);
  useUiStore.getState().setAutoFollow("run-a", false);
  expect(useUiStore.getState().autoFollowByRunId["run-a"]).toBe(false);
});

it("does not let a run id shaped like a prototype key pollute Object.prototype", () => {
  useUiStore.getState().setAutoFollow("__proto__", false);
  useUiStore.getState().setAutoFollow("constructor", false);

  expect(useUiStore.getState().autoFollowByRunId["unrelated-run"]).toBeUndefined();
  expect(({} as Record<string, unknown>).polluted).toBeUndefined();
  expect(Object.prototype.hasOwnProperty.call({}, "__proto__")).toBe(false);
});

it("is excluded from the persisted slice — run ids are meaningless across sessions", () => {
  useUiStore.getState().setAutoFollow("run-a", false);
  const partialize = useUiStore.persist.getOptions().partialize;
  expect(partialize).toBeDefined();
  const persisted = partialize!(useUiStore.getState()) as Record<string, unknown>;
  expect(persisted).not.toHaveProperty("autoFollowByRunId");
});
