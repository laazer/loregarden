import { renderHook, waitFor } from "@testing-library/react";

import { API_BASE } from "../../api/client";
import { useParallelExecution } from "../useParallelExecution";

describe("useParallelExecution", () => {
  const originalFetch = global.fetch;

  beforeEach(() => {
    jest.useFakeTimers();
    global.fetch = jest.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ active_runs: [], queued_runs: [], stats: {} }),
    }) as unknown as typeof fetch;
  });

  afterEach(() => {
    jest.useRealTimers();
    global.fetch = originalFetch;
  });

  it("does not query before a workspace is resolved", () => {
    // Callers resolve the workspace from a query, so the first render always
    // has an empty id. Querying anyway hits /api/parallel/status/ with no id.
    renderHook(() => useParallelExecution("", 5000));
    expect(global.fetch).not.toHaveBeenCalled();
  });

  it("does not keep polling with an empty workspace", () => {
    // The failure was a repeating 404, not a one-off: the poll fired forever.
    renderHook(() => useParallelExecution("", 5000));
    jest.advanceTimersByTime(20000);
    expect(global.fetch).not.toHaveBeenCalled();
  });

  it("stops loading rather than hanging when there is no workspace", () => {
    const { result } = renderHook(() => useParallelExecution("", 5000));
    expect(result.current.loading).toBe(false);
  });

  it("queries once a workspace is known", async () => {
    renderHook(() => useParallelExecution("ws-1", 5000));
    await waitFor(() => expect(global.fetch).toHaveBeenCalled());
    // Absolute, via API_BASE. A relative URL only resolves behind the Vite dev
    // proxy and 404s in a packaged Tauri build.
    expect((global.fetch as jest.Mock).mock.calls[0][0]).toBe(
      `${API_BASE}/api/parallel/status/ws-1`,
    );
  });
});
