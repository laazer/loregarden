import type { QueryClient } from "@tanstack/react-query";

import { ApiError } from "../client";
import { useToastStore } from "../../state/toastStore";
import { createQueryClient } from "../queryClient";

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
