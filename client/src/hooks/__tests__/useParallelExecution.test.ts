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

  it("queries the shared queue endpoint", async () => {
    renderHook(() => useParallelExecution(5000));
    await waitFor(() => expect(global.fetch).toHaveBeenCalled());
    // Absolute, via API_BASE. A relative URL only resolves behind the Vite dev
    // proxy and 404s in a packaged Tauri build. No workspace in the path: the
    // slot pool is shared, so there is only one queue to ask about.
    expect((global.fetch as jest.Mock).mock.calls[0][0]).toBe(
      `${API_BASE}/api/parallel/status`,
    );
  });

  it("does not query while disabled", () => {
    renderHook(() => useParallelExecution(5000, false));
    jest.advanceTimersByTime(20000);
    expect(global.fetch).not.toHaveBeenCalled();
  });

  it("stops loading rather than hanging while disabled", () => {
    const { result } = renderHook(() => useParallelExecution(5000, false));
    expect(result.current.loading).toBe(false);
  });
});
