import { QueryObserver, type QueryClient } from "@tanstack/react-query";

import { ApiError } from "../client";
import { useToastStore } from "../../state/toastStore";
import { createQueryClient, retryUnlessRefused } from "../queryClient";

async function runFailingMutation(meta?: Record<string, unknown>) {
  const client = createQueryClient();
  const mutation = client
    .getMutationCache()
    .build(client, { mutationFn: async () => Promise.reject(new ApiError(500, "worktree is dirty")), meta });
  await mutation.execute(undefined).catch(() => {});
}

beforeEach(() => {
  useToastStore.getState().clear();
});

it("toasts any mutation that does not complete", async () => {
  await runFailingMutation({ errorTitle: "Start run" });

  const [toast] = useToastStore.getState().toasts;
  expect(toast.title).toBe("Start run failed");
  expect(toast.message).toBe("worktree is dirty");
});

it("still reports a mutation nobody named", async () => {
  await runFailingMutation();

  expect(useToastStore.getState().toasts[0].title).toBe("Action failed");
});

it("stays quiet for a mutation that renders its own failure", async () => {
  await runFailingMutation({ suppressErrorToast: true });

  expect(useToastStore.getState().toasts).toHaveLength(0);
});

describe("which failed reads are asked again", () => {
  afterEach(() => jest.useRealTimers());

  /** How many times an observed query asks before it gives up. */
  async function timesAsked(error: unknown): Promise<number> {
    jest.useFakeTimers();
    const queryFn = jest.fn(async () => Promise.reject(error));
    const client = createQueryClient();
    const stop = new QueryObserver(client, { queryKey: ["refs"], queryFn }).subscribe(() => undefined);
    await jest.advanceTimersByTimeAsync(60_000);
    expect(client.getQueryState(["refs"])?.status).toBe("error");
    stop();
    client.clear();
    return queryFn.mock.calls.length;
  }

  it.each([400, 401, 403, 404, 409, 422, 499])("asks once when the server refuses with %i", async (status) => {
    expect(await timesAsked(new ApiError(status, "refused"))).toBe(1);
  });

  it.each([408, 425, 429, 500, 502, 503])("asks four times when a %i may pass", async (status) => {
    expect(await timesAsked(new ApiError(status, "busy"))).toBe(4);
  });

  it("asks four times when the server did not answer", async () => {
    expect(await timesAsked(new TypeError("Failed to fetch"))).toBe(4);
  });

  it("does not read a status outside the refusals as one", () => {
    expect(retryUnlessRefused(0, new ApiError(399, "odd"))).toBe(true);
  });
});

async function fetchFailingQuery(
  client: QueryClient,
  queryKey: unknown[],
  meta?: Record<string, unknown>,
) {
  await client
    .fetchQuery({
      queryKey,
      queryFn: async () => Promise.reject(new ApiError(503, "database is locked")),
      retry: false,
      meta,
    })
    .catch(() => undefined); // silent-ok: the assertion is the toast; fetchQuery rethrows by design
}

it("toasts a failed query that has no cached data to fall back on", async () => {
  const client = createQueryClient();

  await fetchFailingQuery(client, ["tickets"], { errorTitle: "Load tickets" });

  const toasts = useToastStore.getState().toasts;
  expect(toasts).toHaveLength(1);
  expect(toasts[0].title).toBe("Load tickets failed");
  expect(toasts[0].message).toBe("database is locked");
});

it("stays quiet for a failed background refetch over cached data", async () => {
  const client = createQueryClient();
  await client.fetchQuery({ queryKey: ["tickets"], queryFn: async () => ["a ticket"] });

  await fetchFailingQuery(client, ["tickets"], { errorTitle: "Load tickets" });

  expect(useToastStore.getState().toasts).toHaveLength(0);
  expect(client.getQueryData(["tickets"])).toEqual(["a ticket"]);
});

it("stays quiet for a query that renders its own failure", async () => {
  const client = createQueryClient();

  await fetchFailingQuery(client, ["runs"], { suppressErrorToast: true });

  expect(useToastStore.getState().toasts).toHaveLength(0);
});

describe("S2.4 — which failures are allowed to expire", () => {
  it("bounds a failed cold-cache load at 9 seconds", async () => {
    // A background load failure carries no value to copy and re-reports itself
    // on the next poll. Left sticky, a server outage would park four permanent
    // toasts in the corner of every screen.
    const client = createQueryClient();

    await fetchFailingQuery(client, ["tickets"], { errorTitle: "Load tickets" });

    expect(useToastStore.getState().toasts[0].duration).toBe(9000);
  });

  it("leaves an operator-triggered mutation failure sticky", async () => {
    await runFailingMutation({ errorTitle: "Delete session" });

    expect(useToastStore.getState().toasts[0].duration).toBe(0);
  });
});
