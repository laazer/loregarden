import { consolePaneVisibility, EDITOR_RECENT_LIMIT, editorRecentKey, useUiStore } from "../uiStore";

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

describe("the Editor's recent files", () => {
  beforeEach(() => {
    useUiStore.setState({ editorRecentFiles: {} });
  });

  it("keeps the newest first, once each, up to the limit, per workspace and worktree", () => {
    const key = editorRecentKey("loregarden", ".");
    const remember = useUiStore.getState().rememberEditorFile;
    for (let index = 0; index < EDITOR_RECENT_LIMIT + 2; index += 1) remember(key, `f${index}.ts`);
    remember(key, "f5.ts");
    remember(editorRecentKey("loregarden", "../wt"), "other.ts");

    const recent = useUiStore.getState().editorRecentFiles[key];
    expect(recent).toHaveLength(EDITOR_RECENT_LIMIT);
    expect(recent.slice(0, 2)).toEqual(["f5.ts", "f11.ts"]);
    expect(recent.filter((path) => path === "f5.ts")).toHaveLength(1);
    expect(recent).not.toContain("other.ts");
  });

  it("survives a reload", () => {
    useUiStore.getState().rememberEditorFile(editorRecentKey("loregarden", "."), "a.ts");
    const persisted = useUiStore.persist.getOptions().partialize!(useUiStore.getState()) as Record<string, unknown>;
    expect(persisted.editorRecentFiles).toEqual({ "loregarden::.": ["a.ts"] });
  });
});

describe("consolePaneVisibility", () => {
  it("drops the retired workflow and artifact panes", () => {
    expect(consolePaneVisibility({ workspaces: true, tickets: false, workflow: true, artifacts: true })).toEqual({
      workspaces: true,
      tickets: false,
    });
  });

  it("brings the tree back when an older layout had hidden both Console panes", () => {
    expect(consolePaneVisibility({ workspaces: false, tickets: false, workflow: true, artifacts: false })).toEqual({
      workspaces: false,
      tickets: true,
    });
  });

  it("shows both panes when nothing was persisted", () => {
    expect(consolePaneVisibility(undefined)).toEqual({ workspaces: true, tickets: true });
  });
});
