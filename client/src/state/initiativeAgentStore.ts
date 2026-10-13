import { create } from "zustand";
import { persist } from "zustand/middleware";

/** Who answers in an initiative's agent panel: the planner, or Baxter (triage). */
export type InitiativeAgent = "planner" | "baxter";

interface InitiativeAgentState {
  /**
   * The agent the operator last chose. One choice for every initiative: it is
   * a preference about how they work, not a property of one plan.
   */
  agent: InitiativeAgent;
  /**
   * The Baxter thread open on each initiative, by initiative id; absent for a
   * fresh one. Separate from the chat page's `baxterChatSessionId` so opening
   * an initiative does not move the conversation `/chat` is showing.
   */
  baxterSessionByInitiative: Record<string, string>;
  setAgent: (agent: InitiativeAgent) => void;
  setBaxterSession: (initiativeId: string, sessionId: string) => void;
}

/**
 * Persisted: both threads live on the server, and coming back to an initiative
 * should show the conversation the operator was in, with the agent they chose.
 * The map is bounded by the number of initiatives, and an emptied entry is
 * dropped rather than kept as "".
 */
export const useInitiativeAgentStore = create<InitiativeAgentState>()(
  persist(
    (set) => ({
      agent: "planner",
      baxterSessionByInitiative: {},
      setAgent: (agent) => set({ agent }),
      setBaxterSession: (initiativeId, sessionId) =>
        set((state) => {
          const next = { ...state.baxterSessionByInitiative };
          if (sessionId) next[initiativeId] = sessionId;
          else delete next[initiativeId];
          return { baxterSessionByInitiative: next };
        }),
    }),
    {
      name: "loregarden-initiative-agent",
      version: 1,
      partialize: (s) => ({ agent: s.agent, baxterSessionByInitiative: s.baxterSessionByInitiative }),
    },
  ),
);
