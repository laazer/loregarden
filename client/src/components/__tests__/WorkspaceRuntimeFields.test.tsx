import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";

import type { DiscoveryStatus, RuntimeOptions, WorkspaceRuntimeSettings } from "../../api/client";
import { DEFAULT_RUNTIME } from "../../lib/runtimeSettings";
import { pushToast } from "../../state/toastStore";
import { WorkspaceRuntimeFields, runtimeSettingsEqual } from "../WorkspaceRuntimeFields";

// U7: a per-provider discovery failure is a field-level hint and never a toast.
// runtime-options itself returned 200, the operator may be pinning a different
// provider, and there are seven call sites that would each fire one.
jest.mock("../../state/toastStore", () => ({
  ...jest.requireActual("../../state/toastStore"),
  pushToast: jest.fn(),
}));

beforeEach(() => {
  (pushToast as jest.Mock).mockClear();
});

const OPTIONS: RuntimeOptions = {
  cli_adapters: [
    { id: "default", label: "Workspace default" },
    { id: "claude", label: "Claude Code" },
    { id: "cursor", label: "Cursor Agent" },
    { id: "codex", label: "Codex CLI" },
  ],
  claude_models: [
    { id: "", label: "Default (Claude Code profile)" },
    { id: "claude-opus-5", label: "Claude Opus 5 (pinned)" },
  ],
  cursor_models: [
    { id: "", label: "Default (Cursor profile)" },
    { id: "claude-opus-4-8", label: "Claude Opus 4.8" },
    { id: "gpt-5", label: "GPT-5" },
  ],
  codex_models: [
    { id: "", label: "Default (Codex profile)" },
    { id: "gpt-5", label: "GPT-5" },
  ],
  claude_efforts: [
    { id: "", label: "Default (Claude Code decides)" },
    { id: "high", label: "High — Claude Code default" },
    { id: "xhigh", label: "Extra high — best for coding/agentic" },
  ],
  cursor_efforts: [
    { id: "", label: "Default (Cursor decides)" },
    { id: "high", label: "High" },
  ],
  cursor_effort_models: ["claude-opus-4-8"],
};

function renderFields(runtime: Partial<WorkspaceRuntimeSettings>, options = OPTIONS) {
  const onChange = jest.fn();
  render(
    <WorkspaceRuntimeFields
      runtime={{ ...DEFAULT_RUNTIME, ...runtime }}
      options={options}
      onChange={onChange}
    />,
  );
  return onChange;
}

test("claude shows an effort select and reports the picked level", () => {
  const onChange = renderFields({ cli_adapter: "claude" });

  const effort = screen.getByLabelText("Reasoning effort");
  fireEvent.change(effort, { target: { value: "xhigh" } });

  expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ claude_effort: "xhigh" }));
});

test("adapters without an effort control render no effort step", () => {
  renderFields({ cli_adapter: "default" });

  expect(screen.queryByText(/Reasoning effort/)).not.toBeInTheDocument();
});

test("cursor effort is disabled unless the model accepts a bracket override", () => {
  renderFields({ cli_adapter: "cursor", cursor_model: "gpt-5" });

  const effort = screen.getByLabelText("Reasoning effort");
  expect(effort).toBeDisabled();
  expect(screen.getByText(/only on a parameterized model/)).toBeInTheDocument();
});

test("cursor effort is enabled for a parameterized model", () => {
  renderFields({ cli_adapter: "cursor", cursor_model: "claude-opus-4-8" });

  const effort = screen.getByLabelText("Reasoning effort");
  expect(effort).not.toBeDisabled();
});

test("codex shows its model select without an effort control", () => {
  const onChange = renderFields({ cli_adapter: "codex" });

  fireEvent.change(screen.getByLabelText("Codex model"), { target: { value: "gpt-5" } });

  expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ codex_model: "gpt-5" }));
  expect(screen.queryByText(/Reasoning effort/)).not.toBeInTheDocument();
});

test("the effective line names the resolved run, not the empty pin", () => {
  renderFields(
    { cli_adapter: "default" },
    {
      ...OPTIONS,
      effective: {
        cli_adapter: "claude",
        cli_adapter_source: "global",
        model: "claude-opus-5",
        model_source: "workspace",
        effort: "xhigh",
        effort_source: "env",
        supports_model: true,
        supports_effort: true,
      },
    },
  );

  const summary = screen.getByText(/Currently runs as:/);
  expect(summary).toHaveTextContent("claude-opus-5 (workspace default)");
  expect(summary).toHaveTextContent("xhigh (environment override)");
});

test("an adapter that takes no pins reports only the adapter", () => {
  renderFields(
    { cli_adapter: "default" },
    {
      ...OPTIONS,
      effective: {
        cli_adapter: "local",
        cli_adapter_source: "global",
        model: "",
        model_source: "cli-default",
        effort: "",
        effort_source: "cli-default",
        supports_model: false,
        supports_effort: false,
      },
    },
  );

  const summary = screen.getByText(/Currently runs as:/);
  expect(summary).toHaveTextContent("local (global settings)");
  expect(summary).not.toHaveTextContent("cli-default");
});

test("with no provider pinned, the effective line credits each stage's agent", () => {
  renderFields(
    { cli_adapter: "default" },
    {
      ...OPTIONS,
      effective: {
        cli_adapter: "",
        cli_adapter_source: "agent",
        model: "",
        model_source: "agent",
        effort: "",
        effort_source: "agent",
        supports_model: false,
        supports_effort: false,
      },
    },
  );

  const summary = screen.getByText(/Currently runs as:/);
  expect(summary).toHaveTextContent("each stage's agent");
  expect(summary).not.toHaveTextContent("global");
});

test("runtimeSettingsEqual notices an effort-only change", () => {
  expect(
    runtimeSettingsEqual(DEFAULT_RUNTIME, { ...DEFAULT_RUNTIME, claude_effort: "max" }),
  ).toBe(false);
  expect(runtimeSettingsEqual(DEFAULT_RUNTIME, { ...DEFAULT_RUNTIME })).toBe(true);
});

const OPENCODE_OPTIONS: RuntimeOptions = {
  ...OPTIONS,
  cli_adapters: [...OPTIONS.cli_adapters, { id: "opencode", label: "OpenCode" }],
  opencode_models: [
    { id: "", label: "Default (OpenCode profile)" },
    { id: "opencode/nemotron-3.5-lightning-free", label: "opencode/nemotron-3.5-lightning-free" },
  ],
  opencode_efforts: [
    { id: "", label: "Default (model's own variant)" },
    { id: "high", label: "High" },
  ],
};

test("opencode pins a discovered model", () => {
  const onChange = renderFields({ cli_adapter: "opencode" }, OPENCODE_OPTIONS);

  fireEvent.change(screen.getByLabelText("OpenCode model"), {
    target: { value: "opencode/nemotron-3.5-lightning-free" },
  });

  expect(onChange).toHaveBeenCalledWith(
    expect.objectContaining({ opencode_model: "opencode/nemotron-3.5-lightning-free" }),
  );
});

test("opencode accepts a model id the picker never discovered", () => {
  // The catalogue depends on which providers OpenCode is signed in to, so the
  // select is a shortcut rather than the set of legal values.
  const onChange = renderFields({ cli_adapter: "opencode" }, OPENCODE_OPTIONS);

  fireEvent.change(screen.getByPlaceholderText("Or type a provider/model id"), {
    target: { value: "anthropic/claude-opus-5" },
  });

  expect(onChange).toHaveBeenCalledWith(
    expect.objectContaining({ opencode_model: "anthropic/claude-opus-5" }),
  );
});

test("opencode reports its effort level as a variant pin", () => {
  const onChange = renderFields({ cli_adapter: "opencode" }, OPENCODE_OPTIONS);

  fireEvent.change(screen.getByLabelText("Reasoning effort"), { target: { value: "high" } });

  expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ opencode_effort: "high" }));
});

test("runtimeSettingsEqual notices an opencode-only change", () => {
  expect(
    runtimeSettingsEqual(DEFAULT_RUNTIME, { ...DEFAULT_RUNTIME, opencode_model: "a/b" }),
  ).toBe(false);
  expect(
    runtimeSettingsEqual(DEFAULT_RUNTIME, { ...DEFAULT_RUNTIME, opencode_effort: "max" }),
  ).toBe(false);
});

// ---------------------------------------------------------------------------
// Discovery states. Criteria U1-U10.
//
// A cold probe now answers before it has an answer, so the model Select holding
// one option means one of four different things. Until this block existed, all
// four rendered the same thing: an empty picker that asserted a cause
// ("install OpenCode to discover") it had not established. That is the
// regression the ticket's own history records — "what left the picker
// permanently empty before" — and the collapse CLAUDE.md's *No silent failures*
// forbids by name.
//
// The copy asserted here is pinned in the spec (R2/R4), which is the only
// reason asserting it is legitimate.
// ---------------------------------------------------------------------------

const OPENCODE_SENTINEL = { id: "", label: "Default (OpenCode profile)" };
const LMSTUDIO_SENTINEL = { id: "", label: "Auto (first loaded chat model)" };
const CODEX_SENTINEL = { id: "", label: "Default (Codex profile)" };

function discovery(
  overrides: Partial<Record<"codex" | "lmstudio" | "opencode", { status: DiscoveryStatus; detail?: string; target?: string }>>,
): NonNullable<RuntimeOptions["model_discovery"]> {
  const base = {
    codex: { status: "fresh" as DiscoveryStatus, detail: "", target: "/bin/codex" },
    lmstudio: { status: "fresh" as DiscoveryStatus, detail: "", target: "http://127.0.0.1:1234/v1" },
    opencode: { status: "fresh" as DiscoveryStatus, detail: "", target: "/bin/opencode" },
  };
  const merged = { ...base };
  for (const [provider, patch] of Object.entries(overrides)) {
    const key = provider as keyof typeof base;
    merged[key] = { ...base[key], ...patch };
  }
  return merged;
}

const DISCOVERY_OPTIONS: RuntimeOptions = {
  ...OPTIONS,
  cli_adapters: [
    ...OPTIONS.cli_adapters,
    { id: "lmstudio", label: "LM Studio" },
    { id: "opencode", label: "OpenCode" },
  ],
  codex_models: [CODEX_SENTINEL],
  lmstudio_models: [LMSTUDIO_SENTINEL],
  opencode_models: [OPENCODE_SENTINEL],
  model_discovery: discovery({}),
};

/** 95 options is what `opencode models` measured on this machine, 2026-10-20. */
const NINETY_FIVE = [
  OPENCODE_SENTINEL,
  ...Array.from({ length: 94 }, (_, i) => ({
    id: `opencode/model-${i}`,
    label: `opencode/model-${i}`,
  })),
];

function withDiscovery(
  overrides: Parameters<typeof discovery>[0],
  rest: Partial<RuntimeOptions> = {},
): RuntimeOptions {
  return { ...DISCOVERY_OPTIONS, ...rest, model_discovery: discovery(overrides) };
}

// --- U4 / U5: loading, and loading that does not look empty -----------------

test.each([
  ["opencode", "OpenCode model", "Discovering OpenCode models…"],
  ["lmstudio", "LM Studio model", "Discovering LM Studio models…"],
  ["codex", "Codex model", "Discovering Codex models…"],
] as const)(
  "%s says it is still discovering, and the select waits with it",
  (adapter, selectLabel, copy) => {
    renderFields({ cli_adapter: adapter }, withDiscovery({ [adapter]: { status: "probing" } }));

    const line = screen.getByRole("status");
    expect(line).toHaveTextContent(copy);

    const select = screen.getByLabelText(selectLabel);
    expect(select).toBeDisabled();
    // Only the sentinel: the catalogue has not arrived.
    expect(select.querySelectorAll("option")).toHaveLength(1);
  },
);

test("a probing provider and an answered-but-empty one do not share a string", () => {
  renderFields({ cli_adapter: "opencode" }, withDiscovery({ opencode: { status: "probing" } }));
  const probing = screen.getByRole("status").textContent;

  cleanup();

  renderFields({ cli_adapter: "opencode" }, withDiscovery({ opencode: { status: "fresh" } }));
  const empty = screen.getByRole("status").textContent;

  expect(probing).not.toEqual(empty);
});

test("the probing state does not reuse a placeholder that asserts a cause", () => {
  // "install OpenCode to discover" / "start LM Studio to discover" claim the CLI
  // is absent. While a probe is running that is exactly what is not yet known.
  renderFields({ cli_adapter: "opencode" }, withDiscovery({ opencode: { status: "probing" } }));
  expect(
    screen.queryByPlaceholderText("provider/model id (install OpenCode to discover)"),
  ).not.toBeInTheDocument();

  cleanup();

  renderFields({ cli_adapter: "lmstudio" }, withDiscovery({ lmstudio: { status: "probing" } }));
  expect(
    screen.queryByPlaceholderText("Loaded model id (start LM Studio to discover)"),
  ).not.toBeInTheDocument();
});

// --- U2 / U4: the escape hatch stays open ----------------------------------

test.each([
  ["opencode", "OpenCode model id"],
  ["lmstudio", "LM Studio model id"],
] as const)("%s keeps its free-text id input usable while probing", (adapter, label) => {
  const onChange = renderFields(
    { cli_adapter: adapter },
    withDiscovery({ [adapter]: { status: "probing" } }),
  );

  const input = screen.getByLabelText(label);
  expect(input).not.toBeDisabled();

  fireEvent.change(input, { target: { value: "provider/typed-by-hand" } });
  expect(onChange).toHaveBeenCalledWith(
    expect.objectContaining(
      adapter === "opencode"
        ? { opencode_model: "provider/typed-by-hand" }
        : { lmstudio_model: "provider/typed-by-hand" },
    ),
  );
});

// --- U6: the empty state, which is not the error state ----------------------

test("opencode names the thing to run when it discovered nothing", () => {
  renderFields({ cli_adapter: "opencode" }, withDiscovery({ opencode: { status: "fresh" } }));

  expect(screen.getByRole("status")).toHaveTextContent(
    "OpenCode reported no models. Check `opencode auth login`, or type a provider/model id below.",
  );
  expect(screen.getByLabelText("OpenCode model id")).not.toBeDisabled();
});

test("lmstudio names the server it asked when nothing is loaded", () => {
  renderFields(
    { cli_adapter: "lmstudio" },
    withDiscovery({ lmstudio: { status: "fresh", target: "http://192.168.1.9:1234/v1" } }),
  );

  // The resolved base URL is server-side knowledge: the client cannot rebuild
  // it from the workspace row, which is why `target` is in the payload.
  expect(screen.getByRole("status")).toHaveTextContent(
    "No chat models loaded at http://192.168.1.9:1234/v1. Load one in LM Studio, or type its id below.",
  );
  expect(screen.getByLabelText("LM Studio model id")).not.toBeDisabled();
});

test("codex says its catalogue was empty, and grows no text input", () => {
  renderFields({ cli_adapter: "codex" }, withDiscovery({ codex: { status: "fresh" } }));

  expect(screen.getByRole("status")).toHaveTextContent(
    "Codex reported no models beyond its profile default.",
  );
  // U10: codex has no free-text id today, its probe is 0.48s and its slug set
  // is closed. The status line is the whole change there.
  expect(screen.queryByLabelText("Codex model id")).not.toBeInTheDocument();
});

// --- U7: the error state ----------------------------------------------------

test("an unreachable provider renders the server's own reason, verbatim", () => {
  const detail = "Could not reach LM Studio at http://127.0.0.1:1234/v1 — connection refused.";
  renderFields(
    { cli_adapter: "lmstudio" },
    withDiscovery({ lmstudio: { status: "unavailable", detail } }),
  );

  const line = screen.getByRole("status");
  expect(line).toHaveTextContent(detail);
  // Not an interruption: the operator may be pinning a different provider.
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(pushToast).not.toHaveBeenCalled();
});

test("none of the four discovery states raises a toast", () => {
  for (const status of ["probing", "fresh", "stale", "unavailable"] as const) {
    renderFields(
      { cli_adapter: "opencode" },
      withDiscovery({ opencode: { status, detail: status === "unavailable" ? "It broke." : "" } }),
    );
    cleanup();
  }

  expect(pushToast).not.toHaveBeenCalled();
});

test("an unavailable provider still leaves a way to pin a model", () => {
  // U2: no state ends in text alone for lmstudio or opencode.
  const onChange = renderFields(
    { cli_adapter: "opencode" },
    withDiscovery({
      opencode: {
        status: "unavailable",
        detail: "OpenCode CLI not found. Install OpenCode, or set LOREGARDEN_OPENCODE_BIN.",
      },
    }),
  );

  fireEvent.change(screen.getByLabelText("OpenCode model id"), {
    target: { value: "anthropic/claude-opus-5" },
  });
  expect(onChange).toHaveBeenCalledWith(
    expect.objectContaining({ opencode_model: "anthropic/claude-opus-5" }),
  );
});

// --- U8: stale, driven from a fixture because a cold machine never reaches it

test("a stale provider shows the catalogue it has, enabled, while it refreshes", () => {
  renderFields(
    { cli_adapter: "opencode" },
    withDiscovery({ opencode: { status: "stale" } }, { opencode_models: NINETY_FIVE }),
  );

  expect(screen.getByRole("status")).toHaveTextContent("Refreshing…");
  const select = screen.getByLabelText("OpenCode model");
  expect(select).not.toBeDisabled();
  expect(select.querySelectorAll("option")).toHaveLength(95);
});

test("a stale provider with nothing cached is still not disabled", () => {
  // Only `probing` with no cached answer disables the Select (U9), so the
  // control goes disabled -> enabled and never back inside one open modal.
  renderFields({ cli_adapter: "opencode" }, withDiscovery({ opencode: { status: "stale" } }));

  expect(screen.getByLabelText("OpenCode model")).not.toBeDisabled();
});

// --- U3: real volume --------------------------------------------------------

test("a fresh catalogue of 95 options renders in one full-width select, with no hint", () => {
  renderFields(
    { cli_adapter: "opencode" },
    withDiscovery({ opencode: { status: "fresh" } }, { opencode_models: NINETY_FIVE }),
  );

  const select = screen.getByLabelText("OpenCode model");
  expect(select.querySelectorAll("option")).toHaveLength(95);
  // Browser typeahead plus the adjacent id Input are the filter above 50 items.
  expect(select.tagName).toBe("SELECT");
  expect(select).toHaveStyle({ width: "100%" });
  // Nothing is wrong and nothing is pending, so there is nothing to say.
  expect(screen.queryByRole("status")).not.toBeInTheDocument();
});

// --- U9: the select is never remounted, and focus survives a refetch --------

test.each(["lmstudio", "opencode"] as const)(
  "%s renders its model select even when only the sentinel is there",
  (adapter) => {
    // It used to be mounted only when options.length > 1, which remounts it
    // across exactly the probing -> fresh transition a refetch produces.
    const label = adapter === "opencode" ? "OpenCode model" : "LM Studio model";
    renderFields({ cli_adapter: adapter }, withDiscovery({ [adapter]: { status: "probing" } }));

    expect(screen.getByLabelText(label)).toBeInTheDocument();
  },
);

test("a probe resolving into 95 options does not take the typed id or the focus", async () => {
  // The refetch loop re-renders this component every 1.5s while probing. An
  // operator who types their model id must not have it, or the caret, removed
  // from under them when the catalogue lands.
  function Harness({ options }: { options: RuntimeOptions }) {
    const [runtime, setRuntime] = useState<WorkspaceRuntimeSettings>({
      ...DEFAULT_RUNTIME,
      cli_adapter: "opencode",
    });
    return (
      <WorkspaceRuntimeFields runtime={runtime} options={options} onChange={setRuntime} />
    );
  }

  const probing = withDiscovery({ opencode: { status: "probing" } });
  const { rerender } = render(<Harness options={probing} />);

  const input = screen.getByLabelText("OpenCode model id");
  input.focus();
  await userEvent.type(input, "anthropic/claude");
  expect(input).toHaveValue("anthropic/claude");

  rerender(
    <Harness
      options={withDiscovery({ opencode: { status: "fresh" } }, { opencode_models: NINETY_FIVE })}
    />,
  );

  const after = screen.getByLabelText("OpenCode model id");
  expect(after).toHaveValue("anthropic/claude");
  expect(after).toHaveFocus();
  expect(screen.getByLabelText("OpenCode model")).not.toBeDisabled();
});

// --- U10: the names a keyboard and an agent reach these controls by ---------

test("every discovery state keeps the control names and the tab order", () => {
  for (const status of ["probing", "fresh", "stale", "unavailable"] as const) {
    renderFields(
      { cli_adapter: "opencode" },
      withDiscovery({ opencode: { status, detail: status === "unavailable" ? "It broke." : "" } }),
    );

    const select = screen.getByLabelText("OpenCode model");
    const input = screen.getByLabelText("OpenCode model id");
    // The select precedes the input in the DOM, so it precedes it in the tab
    // order; neither takes a tabIndex that would reorder them.
    expect(select.compareDocumentPosition(input) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(select).not.toHaveAttribute("tabindex");
    expect(input).not.toHaveAttribute("tabindex");

    cleanup();
  }
});

test("a payload from a server that has no model_discovery renders as fresh", () => {
  // A cached 200 from a pre-deploy server; the block is optional for that reason.
  const withoutBlock: RuntimeOptions = { ...DISCOVERY_OPTIONS };
  delete withoutBlock.model_discovery;
  renderFields({ cli_adapter: "opencode" }, withoutBlock);

  // Treated as fresh everywhere: the empty-state copy, not a stuck spinner.
  expect(screen.getByLabelText("OpenCode model")).not.toBeDisabled();
  expect(screen.getByRole("status")).toHaveTextContent("OpenCode reported no models.");
});

// ===========================================================================
// Adversarial coverage (test-break stage).
//
// The states above are each driven for one provider at a time. Three providers
// times four statuses is twelve branches in one component, and the three
// branches are written separately — so the mistake that ships is not "the
// probing state is wrong", it is "codex never got the stale case" or "the
// lmstudio detail renders under the codex field". These drive the whole matrix,
// the combinations the server can really send, and the two values a client
// cannot type-check away: a status it does not know, and a payload that
// contradicts itself.
// ===========================================================================

const CODEX_EMPTY = "Codex reported no models beyond its profile default.";
const OPENCODE_EMPTY =
  "OpenCode reported no models. Check `opencode auth login`, or type a provider/model id below.";
const LMSTUDIO_EMPTY =
  "No chat models loaded at http://127.0.0.1:1234/v1. Load one in LM Studio, or type its id below.";
const LMSTUDIO_DOWN =
  "Could not reach LM Studio at http://127.0.0.1:1234/v1 — connection refused.";

const CACHED: Record<"codex" | "lmstudio" | "opencode", Partial<RuntimeOptions>> = {
  codex: {
    codex_models: [CODEX_SENTINEL, { id: "gpt-5", label: "GPT-5" }],
  },
  lmstudio: {
    lmstudio_models: [LMSTUDIO_SENTINEL, { id: "qwen/qwen3.5-9b", label: "qwen/qwen3.5-9b · loaded" }],
  },
  opencode: {
    opencode_models: [OPENCODE_SENTINEL, { id: "anthropic/claude", label: "anthropic/claude" }],
  },
};

const SELECT_LABEL = {
  codex: "Codex model",
  lmstudio: "LM Studio model",
  opencode: "OpenCode model",
} as const;

// provider, status, cached catalogue?, the one line that must appear
const MATRIX = [
  ["codex", "probing", false, "Discovering Codex models…"],
  ["codex", "stale", true, "Refreshing…"],
  ["codex", "fresh", false, CODEX_EMPTY],
  ["codex", "stale", false, CODEX_EMPTY],
  ["codex", "unavailable", false, "Codex CLI not found. Install Codex, or set LOREGARDEN_CODEX_BIN."],
  ["lmstudio", "probing", false, "Discovering LM Studio models…"],
  ["lmstudio", "stale", true, "Refreshing…"],
  ["lmstudio", "fresh", false, LMSTUDIO_EMPTY],
  ["lmstudio", "stale", false, LMSTUDIO_EMPTY],
  ["lmstudio", "unavailable", false, LMSTUDIO_DOWN],
  ["opencode", "probing", false, "Discovering OpenCode models…"],
  ["opencode", "stale", true, "Refreshing…"],
  ["opencode", "fresh", false, OPENCODE_EMPTY],
  ["opencode", "stale", false, OPENCODE_EMPTY],
  ["opencode", "unavailable", false, "`opencode models` did not answer within 45s."],
] as const;

test.each(MATRIX)(
  "%s in %s renders exactly one line, and it is the right one",
  (provider, status, cached, copy) => {
    renderFields(
      { cli_adapter: provider },
      withDiscovery(
        {
          [provider]: {
            status,
            detail: status === "unavailable" ? copy : "",
          },
        },
        cached ? CACHED[provider] : {},
      ),
    );

    // One fact, one line: a second `role="status"` under the same field is two
    // screen-reader announcements for one state, and usually a branch that
    // forgot to be exclusive.
    const lines = screen.getAllByRole("status");
    expect(lines).toHaveLength(1);
    expect(lines[0]).toHaveTextContent(copy);
    expect(lines[0]).toHaveClass("modal-hint");
    // U7: never an interruption, in any of the four states.
    expect(pushToast).not.toHaveBeenCalled();
  },
);

test.each(["codex", "lmstudio", "opencode"] as const)(
  "%s only disables its select while probing with nothing cached",
  (provider) => {
    const select = () => screen.getByLabelText(SELECT_LABEL[provider]);

    renderFields(
      { cli_adapter: provider },
      withDiscovery({ [provider]: { status: "probing" } }, CACHED[provider]),
    );
    // U9: a refetch that lands `probing` beside a catalogue the operator is
    // already reading must not take the control away from them. Disabled is for
    // "there is nothing to pick yet", not for "a probe is running".
    expect(select()).toBeEnabled();
    cleanup();

    renderFields(
      { cli_adapter: provider },
      withDiscovery({ [provider]: { status: "unavailable", detail: "It broke." } }),
    );
    // Unavailable leaves the sentinel selectable rather than trapping the
    // operator in a dead control; for lmstudio and opencode the id Input is the
    // real path, and codex can still save the profile default.
    expect(select()).toBeEnabled();
  },
);

test("a provider's failure is not reported under another provider's field", () => {
  // Weakness: rendering `model_discovery` as a map instead of reading the entry
  // for the adapter on screen. LM Studio is unreachable on most machines, so
  // this mistake shows a connection error under the Codex field for nearly
  // every operator — and the codex field is the one state with no free-text
  // escape hatch.
  renderFields(
    { cli_adapter: "codex" },
    withDiscovery({
      codex: { status: "fresh" },
      lmstudio: { status: "unavailable", detail: LMSTUDIO_DOWN },
      opencode: { status: "probing" },
    }, CACHED.codex),
  );

  expect(screen.queryByText(LMSTUDIO_DOWN)).not.toBeInTheDocument();
  expect(screen.queryByText("Discovering OpenCode models…")).not.toBeInTheDocument();
  // codex is fresh with a catalogue, so it has nothing to say at all.
  expect(screen.queryAllByRole("status")).toHaveLength(0);
});

test("a status this client has never heard of leaves the picker usable", () => {
  // The server's vocabulary is an enum that may grow, and a cached response can
  // outlive a deploy. An unknown value must degrade the way a missing block
  // does — to "fresh" — and must never be the branch that disables the control:
  // a permanently disabled, permanently empty picker is this ticket's named
  // regression reached by a different road.
  renderFields(
    { cli_adapter: "opencode" },
    withDiscovery(
      // @ts-expect-error — deliberately outside the union the client knows
      { opencode: { status: "quiesced" } },
      CACHED.opencode,
    ),
  );

  const select = screen.getByLabelText("OpenCode model");
  expect(select).toBeEnabled();
  expect(select.querySelectorAll("option")).toHaveLength(2);
  expect(screen.getByLabelText("OpenCode model id")).toBeEnabled();
  expect(pushToast).not.toHaveBeenCalled();
});

test("a detail on a status that is not unavailable is not shown as the reason", () => {
  // The server pins `detail` to the `unavailable` status, so a non-empty detail
  // on a `fresh` answer is a server-side bug — and the one thing the UI must not
  // do with it is render a stale failure beside a catalogue that just succeeded.
  renderFields(
    { cli_adapter: "opencode" },
    withDiscovery({
      opencode: { status: "fresh", detail: "A reason from the probe before this one." },
    }),
  );

  expect(screen.queryByText("A reason from the probe before this one.")).not.toBeInTheDocument();
  expect(screen.getByRole("status")).toHaveTextContent(OPENCODE_EMPTY);
});

test("the id input survives every discovery state for both providers that have one", () => {
  // U2/U10. The escape hatch is the whole reason none of these states is a dead
  // end, and it is the control an operator is most likely to be typing into
  // when a refetch lands.
  for (const provider of ["lmstudio", "opencode"] as const) {
    for (const status of ["probing", "fresh", "stale", "unavailable"] as const) {
      renderFields(
        { cli_adapter: provider },
        withDiscovery({ [provider]: { status, detail: status === "unavailable" ? "Down." : "" } }),
      );
      const label = provider === "lmstudio" ? "LM Studio model id" : "OpenCode model id";
      expect(screen.getByLabelText(label)).toBeEnabled();
      cleanup();
    }
  }
});
