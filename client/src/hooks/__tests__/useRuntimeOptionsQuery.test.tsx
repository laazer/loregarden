/**
 * The one place that decides when runtime-options is re-fetched. Criterion U8.
 *
 * Discovery now answers before it has an answer: `model_discovery[p].status` is
 * `"probing"` while a probe runs on the server's background thread, and the
 * catalogue arrives on a later response. Something has to ask again, and there
 * are seven `api.runtimeOptions` call sites — four passing `?workspace=`, two
 * passing nothing, one passing `?ticket=`. A refetch rule copied into seven
 * files is seven rules; this hook is the only one.
 *
 * The last two tests are the criterion itself, and they read the tree: a
 * second copy of the interval policy, or a call site that bypasses the hook,
 * fails U8 however well it behaves.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import type { ReactNode } from "react";

import { api } from "../../api/client";
import type { RuntimeOptions } from "../../api/client";
import {
  RUNTIME_OPTIONS_PROBING_REFETCH_MS,
  useRuntimeOptionsQuery,
} from "../useRuntimeOptionsQuery";

jest.mock("../../api/client", () => ({
  api: { runtimeOptions: jest.fn() },
}));

const mockRuntimeOptions = api.runtimeOptions as jest.MockedFunction<typeof api.runtimeOptions>;

function payload(
  discovery?: RuntimeOptions["model_discovery"],
  models: RuntimeOptions["opencode_models"] = [{ id: "", label: "Default (OpenCode profile)" }],
): RuntimeOptions {
  return {
    cli_adapters: [{ id: "opencode", label: "OpenCode" }],
    claude_models: [],
    cursor_models: [],
    opencode_models: models,
    model_discovery: discovery,
  };
}

const PROBING = {
  codex: { status: "fresh" as const, detail: "", target: "/bin/codex" },
  lmstudio: { status: "fresh" as const, detail: "", target: "http://127.0.0.1:1234/v1" },
  opencode: { status: "probing" as const, detail: "", target: "opencode" },
};

const SETTLED = {
  ...PROBING,
  opencode: { status: "fresh" as const, detail: "", target: "/bin/opencode" },
};

let client: QueryClient;

function wrapper({ children }: { children: ReactNode }) {
  // One client per test, created in beforeEach — building it inside the wrapper
  // makes every re-render a fresh cache, which silently defeats the refetch
  // tests below.
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

beforeEach(() => {
  jest.clearAllMocks();
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
});

test("the interval is a named constant, not a number spread through the tree", () => {
  // 1500ms against a 7.42s measured opencode probe is roughly five polls. The
  // number is tunable with evidence; it being in one place is not.
  expect(RUNTIME_OPTIONS_PROBING_REFETCH_MS).toBe(1500);
});

test("it passes the caller's params straight through to the endpoint", async () => {
  mockRuntimeOptions.mockResolvedValue(payload(SETTLED));

  const { result } = renderHook(
    () =>
      useRuntimeOptionsQuery({
        queryKey: ["runtime-options", "loregarden"],
        params: { workspace: "loregarden" },
      }),
    { wrapper },
  );

  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  expect(mockRuntimeOptions).toHaveBeenCalledWith({ workspace: "loregarden" });
});

test("a site that passes no params asks for no params", async () => {
  // BranchTriageOverviewPanel and StudioPage both do; a hook that assumed a
  // workspace would quietly change which LM Studio server they probe.
  mockRuntimeOptions.mockResolvedValue(payload(SETTLED));

  const { result } = renderHook(
    () => useRuntimeOptionsQuery({ queryKey: ["runtime-options"] }),
    { wrapper },
  );

  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  expect(mockRuntimeOptions).toHaveBeenCalledWith(undefined);
});

test("a site that passes a ticket keeps passing a ticket", async () => {
  mockRuntimeOptions.mockResolvedValue(payload(SETTLED));

  const { result } = renderHook(
    () =>
      useRuntimeOptionsQuery({
        queryKey: ["runtime-options", "ticket", "t-1"],
        params: { ticket: "t-1" },
      }),
    { wrapper },
  );

  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  expect(mockRuntimeOptions).toHaveBeenCalledWith({ ticket: "t-1" });
});

test("enabled: false is preserved, so a closed modal fetches nothing", async () => {
  mockRuntimeOptions.mockResolvedValue(payload(SETTLED));

  renderHook(
    () =>
      useRuntimeOptionsQuery({
        queryKey: ["runtime-options", "loregarden"],
        params: { workspace: "loregarden" },
        enabled: false,
      }),
    { wrapper },
  );

  await Promise.resolve();
  expect(mockRuntimeOptions).not.toHaveBeenCalled();
});

test("while a provider is probing it asks again, and stops once none is", async () => {
  jest.useFakeTimers();
  try {
    mockRuntimeOptions.mockResolvedValue(payload(PROBING));

    const { result } = renderHook(
      () => useRuntimeOptionsQuery({ queryKey: ["runtime-options"] }),
      { wrapper },
    );
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(mockRuntimeOptions).toHaveBeenCalledTimes(1);

    // The probe is still running server-side: the catalogue only arrives if
    // somebody asks again.
    mockRuntimeOptions.mockResolvedValue(
      payload(SETTLED, [
        { id: "", label: "Default (OpenCode profile)" },
        { id: "opencode/a", label: "opencode/a" },
      ]),
    );
    await act(async () => {
      // A hair past the interval, so the response resolves inside the advance.
      await jest.advanceTimersByTimeAsync(RUNTIME_OPTIONS_PROBING_REFETCH_MS + 10);
    });
    expect(mockRuntimeOptions).toHaveBeenCalledTimes(2);
    // Read the cache rather than the hook's last rendered snapshot: under fake
    // timers the commit can lag the resolve, and what the interval policy reads
    // is the cache.
    expect(
      client.getQueryData<RuntimeOptions>(["runtime-options"])?.model_discovery?.opencode.status,
    ).toBe("fresh");

    // Nothing is probing now, so the poll must stop rather than hammer the
    // endpoint every 1.5s for as long as the app is open.
    await act(async () => {
      await jest.advanceTimersByTimeAsync(RUNTIME_OPTIONS_PROBING_REFETCH_MS * 4);
    });
    expect(mockRuntimeOptions).toHaveBeenCalledTimes(2);
  } finally {
    jest.useRealTimers();
  }
});

test("a payload with no model_discovery block does not poll", async () => {
  // A cached 200 from a pre-deploy server has no such key, and a missing block
  // is treated as `fresh` everywhere.
  jest.useFakeTimers();
  try {
    mockRuntimeOptions.mockResolvedValue(payload(undefined));

    const { result } = renderHook(
      () => useRuntimeOptionsQuery({ queryKey: ["runtime-options"] }),
      { wrapper },
    );
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    await act(async () => {
      await jest.advanceTimersByTimeAsync(RUNTIME_OPTIONS_PROBING_REFETCH_MS * 4);
    });
    expect(mockRuntimeOptions).toHaveBeenCalledTimes(1);
  } finally {
    jest.useRealTimers();
  }
});

// ---------------------------------------------------------------------------
// U8's actual criterion: one policy, and every call site through it.
// ---------------------------------------------------------------------------

const SRC = join(__dirname, "..", "..");
const HOOK = join(SRC, "hooks", "useRuntimeOptionsQuery.ts");

function sourceFiles(dir: string, found: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    const path = join(dir, entry);
    if (statSync(path).isDirectory()) {
      if (entry === "__tests__" || entry === "test") continue;
      sourceFiles(path, found);
    } else if (/\.tsx?$/.test(entry) && !/\.test\.tsx?$/.test(entry)) {
      found.push(path);
    }
  }
  return found;
}

test("every runtime-options call site goes through the hook", () => {
  const callers = sourceFiles(SRC)
    .filter((path) => path !== HOOK && path !== join(SRC, "api", "client.ts"))
    .filter((path) => /\bapi\.runtimeOptions\s*\(/.test(readFileSync(path, "utf8")));

  expect(callers.map((p) => p.slice(SRC.length + 1))).toEqual([]);
});

test("the refetch policy exists in exactly one file", () => {
  const owners = sourceFiles(SRC).filter((path) => {
    const text = readFileSync(path, "utf8");
    return text.includes("refetchInterval") && text.includes("model_discovery");
  });

  expect(owners.map((p) => p.slice(SRC.length + 1))).toEqual([
    join("hooks", "useRuntimeOptionsQuery.ts"),
  ]);
});

// ===========================================================================
// Adversarial coverage (test-break stage).
//
// This hook is a timer that the server's own vocabulary drives, mounted at
// seven call sites and, through AppLayout, for as long as the app is open. The
// failure that matters is not "it did not poll" — it is "it never stops", which
// is this ticket's defect (a client generating its own load) wearing the
// opposite hat. Each test below drives a payload the server can really send and
// asserts the timer stops.
// ===========================================================================

const DISCOVERY_SETTLED = {
  codex: { status: "fresh" as const, detail: "", target: "/bin/codex" },
  lmstudio: {
    status: "unavailable" as const,
    detail: "Could not reach LM Studio at http://127.0.0.1:1234/v1 — connection refused.",
    target: "http://127.0.0.1:1234/v1",
  },
  opencode: { status: "stale" as const, detail: "", target: "/bin/opencode" },
};

async function pollsAfter(discovery: RuntimeOptions["model_discovery"]): Promise<number> {
  mockRuntimeOptions.mockResolvedValue(payload(discovery));
  const { result } = renderHook(() => useRuntimeOptionsQuery({ queryKey: ["runtime-options"] }), {
    wrapper,
  });
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  const afterFirst = mockRuntimeOptions.mock.calls.length;
  await act(async () => {
    await jest.advanceTimersByTimeAsync(RUNTIME_OPTIONS_PROBING_REFETCH_MS * 4);
  });
  return mockRuntimeOptions.mock.calls.length - afterFirst;
}

test("a settled payload with no probing provider never asks again", async () => {
  // `stale` and `unavailable` are both terminal from the client's side: the
  // server's own failure TTL decides when a retry is worth making. A policy
  // written as "poll unless everything is fresh" turns a machine with no LM
  // Studio into 40 requests a minute, for every open tab, forever.
  jest.useFakeTimers();
  try {
    expect(await pollsAfter(DISCOVERY_SETTLED)).toBe(0);
  } finally {
    jest.useRealTimers();
  }
});

test("a status this client has never heard of does not start a poll loop", async () => {
  // The server's vocabulary is a StrEnum that may grow (`disabled`, `skipped`).
  // An older client must treat what it does not recognize the way it treats a
  // missing block — as settled — rather than polling 1.5s forever against a
  // value that will never become "fresh".
  jest.useFakeTimers();
  try {
    const polls = await pollsAfter({
      codex: { status: "fresh", detail: "", target: "/bin/codex" },
      lmstudio: { status: "fresh", detail: "", target: "http://127.0.0.1:1234/v1" },
      // @ts-expect-error — deliberately outside the union the client knows
      opencode: { status: "quiesced", detail: "", target: "/bin/opencode" },
    });
    expect(polls).toBe(0);
  } finally {
    jest.useRealTimers();
  }
});

test("an empty discovery block does not poll", async () => {
  // Not the same as a missing block: a server that probed nothing sends `{}`.
  jest.useFakeTimers();
  try {
    expect(await pollsAfter({} as RuntimeOptions["model_discovery"])).toBe(0);
  } finally {
    jest.useRealTimers();
  }
});

test("one probing provider among settled ones is still enough to ask again", async () => {
  // The inverse mistake: a policy keyed on "every provider is probing", or on
  // one hardcoded provider, leaves the slow one (opencode, measured 7.42s) as
  // the only state that never resolves on screen.
  jest.useFakeTimers();
  try {
    const polls = await pollsAfter({
      ...DISCOVERY_SETTLED,
      codex: { status: "probing", detail: "", target: "/bin/codex" },
    });
    expect(polls).toBeGreaterThanOrEqual(1);
  } finally {
    jest.useRealTimers();
  }
});

test("a disabled query does not poll even while a provider is probing", async () => {
  // `enabled: false` is how a closed modal stops fetching (QueueAddToLaneModal,
  // TriageModelModal). A refetch interval that outlives it is a background poll
  // nobody can see and nobody asked for.
  jest.useFakeTimers();
  try {
    mockRuntimeOptions.mockResolvedValue(payload(PROBING));
    client.setQueryData(["runtime-options"], payload(PROBING));

    renderHook(
      () => useRuntimeOptionsQuery({ queryKey: ["runtime-options"], enabled: false }),
      { wrapper },
    );
    await act(async () => {
      await jest.advanceTimersByTimeAsync(RUNTIME_OPTIONS_PROBING_REFETCH_MS * 4);
    });

    expect(mockRuntimeOptions).not.toHaveBeenCalled();
  } finally {
    jest.useRealTimers();
  }
});

test("a failing request does not turn into a retry loop", async () => {
  // runtime-options can 500 (a bad workspace slug, a migration mid-deploy). The
  // modal's own `role="alert"` body covers that, and the interval must not turn
  // one failure into a request every 1.5s: there is no `model_discovery` to read
  // on an error, and "no data" must mean "do not poll".
  jest.useFakeTimers();
  try {
    mockRuntimeOptions.mockRejectedValue(new Error("boom"));

    const { result } = renderHook(
      () => useRuntimeOptionsQuery({ queryKey: ["runtime-options"] }),
      { wrapper },
    );
    await waitFor(() => expect(result.current.isError).toBe(true));
    const afterFirst = mockRuntimeOptions.mock.calls.length;

    await act(async () => {
      await jest.advanceTimersByTimeAsync(RUNTIME_OPTIONS_PROBING_REFETCH_MS * 4);
    });

    expect(mockRuntimeOptions.mock.calls.length - afterFirst).toBe(0);
  } finally {
    jest.useRealTimers();
  }
});
