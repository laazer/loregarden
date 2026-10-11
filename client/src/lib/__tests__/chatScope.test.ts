import {
  baxterChatArchiveQuery,
  baxterChatSessionsPath,
  chatScopeInitiativeId,
  chatScopeWorkspaceSlug,
  GLOBAL_CHAT_SCOPE,
  initiativeChatScope,
  parseChatScope,
} from "../chatScope";
import { useUiStore } from "../../state/uiStore";

it("reads a bare slug as that workspace, so persisted state keeps its meaning", () => {
  expect(parseChatScope("loregarden")).toEqual({ kind: "workspace", slug: "loregarden" });
  expect(baxterChatSessionsPath("loregarden")).toBe(
    "/api/workspaces/loregarden/baxter-chat/sessions",
  );
  expect(baxterChatArchiveQuery("loregarden")).toBe("");
});

it("routes the global scopes to one archive, split by the initiative query", () => {
  const focused = initiativeChatScope("i 1");

  expect(parseChatScope(GLOBAL_CHAT_SCOPE)).toEqual({ kind: "global", initiativeId: null });
  expect(parseChatScope(focused)).toEqual({ kind: "global", initiativeId: "i 1" });
  expect(baxterChatSessionsPath(focused)).toBe("/api/baxter-chat/global/sessions");
  expect(baxterChatArchiveQuery(GLOBAL_CHAT_SCOPE)).toBe("");
  expect(baxterChatArchiveQuery(focused)).toBe("?initiative_id=i%201");
  expect(chatScopeWorkspaceSlug(focused)).toBe("");
  expect(chatScopeInitiativeId(focused)).toBe("i 1");
});

it("treats nothing selected, and a focus with no id, as no scope", () => {
  expect(parseChatScope("")).toBeNull();
  expect(parseChatScope("@initiative:")).toBeNull();
  expect(() => baxterChatSessionsPath("")).toThrow("No chat scope selected");
});

it("carries a persisted chat workspace into the chat scope", () => {
  const migrate = useUiStore.persist.getOptions().migrate!;

  const migrated = migrate({ chatWorkspaceSlug: "blobert" }, 12) as Record<string, unknown>;

  expect(migrated.chatScope).toBe("blobert");
  expect(migrated).not.toHaveProperty("chatWorkspaceSlug");
  expect((migrate({}, 10) as Record<string, unknown>).chatScope).toBe("");
});

it("closes the open thread when the scope changes, but not when it is first pinned", () => {
  useUiStore.setState({ chatScope: "", baxterChatSessionId: "t1" });

  useUiStore.getState().setChatScope("loregarden");
  expect(useUiStore.getState().baxterChatSessionId).toBe("t1");

  useUiStore.getState().setChatScope(GLOBAL_CHAT_SCOPE);
  expect(useUiStore.getState().chatScope).toBe(GLOBAL_CHAT_SCOPE);
  expect(useUiStore.getState().baxterChatSessionId).toBe("");
});
