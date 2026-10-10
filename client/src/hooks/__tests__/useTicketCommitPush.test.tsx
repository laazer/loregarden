import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";

import { ApiError, api } from "../../api/client";
import { useUiStore } from "../../state/uiStore";
import { useToastStore } from "../../state/toastStore";
import { commitPushFailurePrompt, useTicketCommitPush } from "../useTicketCommitPush";

jest.mock("../../api/client", () => {
  const actual = jest.requireActual("../../api/client");
  return {
    ...actual,
    api: { commitPush: jest.fn(), sendTriageMessage: jest.fn() },
  };
});

const commitPushMock = api.commitPush as jest.MockedFunction<typeof api.commitPush>;
const sendMock = api.sendTriageMessage as jest.MockedFunction<typeof api.sendTriageMessage>;

function Wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

beforeEach(() => {
  jest.clearAllMocks();
  useUiStore.getState().setCopilotOpen(false);
  useToastStore.setState({ toasts: [] });
});

test("a failed commit opens the dock and hands the error to the ticket's triage chat", async () => {
  const error = new ApiError(409, "Nothing to commit on branch ticket/abc");
  commitPushMock.mockRejectedValue(error);
  sendMock.mockResolvedValue({} as Awaited<ReturnType<typeof api.sendTriageMessage>>);

  const { result } = renderHook(() => useTicketCommitPush(), { wrapper: Wrapper });
  act(() => result.current.mutate("ticket-1"));

  await waitFor(() => expect(sendMock).toHaveBeenCalledTimes(1));
  expect(sendMock).toHaveBeenCalledWith("ticket-1", commitPushFailurePrompt(error));
  expect(sendMock.mock.calls[0][1]).toContain("Nothing to commit on branch ticket/abc");
  expect(useUiStore.getState().copilotOpen).toBe(true);
});

test("a failed hand-off is toasted, not swallowed", async () => {
  commitPushMock.mockRejectedValue(new ApiError(400, "push rejected"));
  sendMock.mockRejectedValue(new ApiError(500, "triage down"));

  const { result } = renderHook(() => useTicketCommitPush(), { wrapper: Wrapper });
  act(() => result.current.mutate("ticket-1"));

  await waitFor(() =>
    expect(useToastStore.getState().toasts.some((t) => t.title === "Hand off to triage chat failed")).toBe(true),
  );
});

test("a successful commit does not touch the triage chat", async () => {
  commitPushMock.mockResolvedValue({} as Awaited<ReturnType<typeof api.commitPush>>);

  const { result } = renderHook(() => useTicketCommitPush(), { wrapper: Wrapper });
  act(() => result.current.mutate("ticket-1"));

  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  expect(sendMock).not.toHaveBeenCalled();
  expect(useUiStore.getState().copilotOpen).toBe(false);
});
