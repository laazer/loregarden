import {
  clampCopilotWidth,
  DEFAULT_COPILOT_WIDTH,
  normalizeUtilityDockEdge,
  useUiStore,
} from "../uiStore";

describe("utility dock edge + width", () => {
  beforeEach(() => {
    useUiStore.setState({
      utilityDockEdge: "bottom",
      copilotWidth: DEFAULT_COPILOT_WIDTH,
    });
  });

  it("defaults utilityDockEdge to bottom", () => {
    expect(useUiStore.getState().utilityDockEdge).toBe("bottom");
  });

  it("setUtilityDockEdge toggles to right and back", () => {
    useUiStore.getState().setUtilityDockEdge("right");
    expect(useUiStore.getState().utilityDockEdge).toBe("right");
    useUiStore.getState().setUtilityDockEdge("bottom");
    expect(useUiStore.getState().utilityDockEdge).toBe("bottom");
  });

  it("normalizeUtilityDockEdge rejects unknown values", () => {
    expect(normalizeUtilityDockEdge("side")).toBe("bottom");
    expect(normalizeUtilityDockEdge("right")).toBe("right");
  });

  it("clampCopilotWidth keeps values in range", () => {
    expect(clampCopilotWidth(100)).toBe(280);
    expect(clampCopilotWidth(900)).toBe(640);
    expect(clampCopilotWidth(400)).toBe(400);
    expect(clampCopilotWidth("nope")).toBe(DEFAULT_COPILOT_WIDTH);
  });

  it("persists utilityDockEdge and copilotWidth", () => {
    useUiStore.getState().setUtilityDockEdge("right");
    useUiStore.getState().setCopilotWidth(420);
    const partialize = useUiStore.persist.getOptions().partialize;
    expect(partialize).toBeDefined();
    const persisted = partialize!(useUiStore.getState()) as Record<string, unknown>;
    expect(persisted.utilityDockEdge).toBe("right");
    expect(persisted.copilotWidth).toBe(420);
  });
});

describe("Baxter chat chrome state", () => {
  beforeEach(() => {
    useUiStore.setState({
      baxterHistoryOpen: false,
      baxterChatResetNonce: 0,
    });
  });

  it("toggles the history drawer", () => {
    useUiStore.getState().toggleBaxterHistory();
    expect(useUiStore.getState().baxterHistoryOpen).toBe(true);
    useUiStore.getState().toggleBaxterHistory();
    expect(useUiStore.getState().baxterHistoryOpen).toBe(false);
  });

  it("new chat closes history and bumps the reset nonce", () => {
    useUiStore.getState().setBaxterHistoryOpen(true);
    useUiStore.getState().requestBaxterChatReset();
    expect(useUiStore.getState().baxterHistoryOpen).toBe(false);
    expect(useUiStore.getState().baxterChatResetNonce).toBe(1);
  });
});
