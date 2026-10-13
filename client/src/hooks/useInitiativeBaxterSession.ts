import { useCallback } from "react";

import { initiativeChatScope } from "../lib/chatScope";
import { useInitiativeAgentStore } from "../state/initiativeAgentStore";
import { useBaxterChatSessionAt, type BaxterChatSessionBinding } from "./useBaxterChatSession";

/**
 * Baxter, focused on one initiative: the global thread that answers across
 * every workspace the initiative's milestones live in, with its plan in view.
 *
 * Holds its own thread id per initiative (`initiativeAgentStore`) rather than
 * the chat page's, so the two never switch each other's conversation.
 *
 * An empty `initiativeId` issues no requests, so a caller above `<Routes>` can
 * call it on every screen.
 */
export function useInitiativeBaxterSession(initiativeId: string): BaxterChatSessionBinding {
  const sessionId = useInitiativeAgentStore((s) =>
    initiativeId ? s.baxterSessionByInitiative[initiativeId] ?? "" : "",
  );
  const setBaxterSession = useInitiativeAgentStore((s) => s.setBaxterSession);
  const setSessionId = useCallback(
    (id: string) => {
      if (initiativeId) setBaxterSession(initiativeId, id);
    },
    [initiativeId, setBaxterSession],
  );
  return useBaxterChatSessionAt(initiativeId ? initiativeChatScope(initiativeId) : "", sessionId, setSessionId);
}
