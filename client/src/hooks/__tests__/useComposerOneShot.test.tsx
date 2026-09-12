import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";

import { useComposerCommands } from "../useComposerCommands";
import {
  AGENT_PLAN_REQUEST_PREFIX,
  agentPlanRequestTask,
} from "../../components/chat/primitives/agentPlan";
import { matchCommands, BUILTIN_COMMANDS } from "../../lib/composerCommands";

jest.mock("../../api/composerApi", () => ({
  composerApi: {
    notes: jest.fn().mockResolvedValue([]),
    editorSearch: jest.fn().mockResolvedValue([]),
    createNote: jest.fn(),
    updateNote: jest.fn(),
    deleteNote: jest.fn(),
  },
}));

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

function harness(value: string, onSend: jest.Mock) {
  return renderHook(
    () =>
      useComposerCommands({
        value,
        onChange: jest.fn(),
        workspaceSlug: "loregarden",
        queueKey: null,
        isBusy: false,
        onSend,
      }),
    { wrapper },
  );
}

describe("/oneshot", () => {
  it("is offered by the menu, and by its alias", () => {
    expect(matchCommands(BUILTIN_COMMANDS, "oneshot").map((c) => c.name)).toContain("oneshot");
    expect(matchCommands(BUILTIN_COMMANDS, "plan").map((c) => c.name)).toContain("oneshot");
  });

  it("sends a plan request carrying the task", () => {
    const onSend = jest.fn();
    const { result } = harness("/oneshot Add a history API", onSend);
    act(() => {
      expect(result.current.submit()).toBe(true);
    });
    expect(onSend).toHaveBeenCalledTimes(1);
    const [content, skill] = onSend.mock.calls[0];
    expect(content.startsWith(AGENT_PLAN_REQUEST_PREFIX)).toBe(true);
    expect(agentPlanRequestTask(content)).toBe("Add a history API");
    expect(skill).toBe("");
  });

  it("sends nothing for a bare /oneshot — there is no task to plan yet", () => {
    const onSend = jest.fn();
    const { result } = harness("/oneshot", onSend);
    act(() => {
      expect(result.current.submit()).toBe(true);
    });
    expect(onSend).not.toHaveBeenCalled();
  });

  it("leaves an ordinary message alone", () => {
    const onSend = jest.fn();
    const { result } = harness("Add a history API", onSend);
    act(() => {
      expect(result.current.submit()).toBe(false);
    });
    expect(onSend).not.toHaveBeenCalled();
  });
});
