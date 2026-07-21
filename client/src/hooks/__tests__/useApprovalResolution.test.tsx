import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";

import { api } from "../../api/client";
import { useApprovalResolution } from "../useApprovalResolution";

jest.mock("../../api/client", () => ({
  api: { resolveApproval: jest.fn().mockResolvedValue({ id: "a1", status: "approved" }) },
}));

const mockApi = api as jest.Mocked<typeof api>;

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

beforeEach(() => jest.clearAllMocks());

it("forwards the whole card payload, including the rework route", async () => {
  // The bug this hook replaces: the logs panel's copy named its fields
  // explicitly and omitted route_to_stage_key, so rejecting a gate with an
  // explicit target silently fell back to the default one — while the same
  // card, in the triage panel, routed correctly.
  const { result } = renderHook(() => useApprovalResolution("t1"), { wrapper });

  result.current.mutate({
    id: "a1",
    action: "reject",
    response: "not yet",
    route_to_stage_key: "implement",
  });

  await waitFor(() => expect(mockApi.resolveApproval).toHaveBeenCalled());
  expect(mockApi.resolveApproval).toHaveBeenCalledWith("a1", {
    action: "reject",
    response: "not yet",
    route_to_stage_key: "implement",
  });
});

it("carries the answers an agent question was asked for", async () => {
  const { result } = renderHook(() => useApprovalResolution("t1"), { wrapper });

  result.current.mutate({
    id: "a2",
    action: "approve",
    answers: { shape: "square" },
    always_allow: true,
  });

  await waitFor(() => expect(mockApi.resolveApproval).toHaveBeenCalled());
  expect(mockApi.resolveApproval).toHaveBeenCalledWith("a2", {
    action: "approve",
    answers: { shape: "square" },
    always_allow: true,
  });
});

it("notifies the host once the decision lands", async () => {
  const onResolved = jest.fn();
  const { result } = renderHook(() => useApprovalResolution("t1", onResolved), { wrapper });

  result.current.mutate({ id: "a3", action: "approve" });

  await waitFor(() => expect(onResolved).toHaveBeenCalled());
});
