import { render, screen, within } from "@testing-library/react";

import type { UsageProviderSnapshot, UsageSnapshot } from "../../api/client";
import { UsageModal } from "../UsageModal";

function provider(overrides: Partial<UsageProviderSnapshot>): UsageProviderSnapshot {
  return {
    provider: "claude",
    plan: null,
    logged_in: true,
    error: null,
    meters: [],
    breakdown: [],
    from_cache: false,
    cached_at: null,
    configured_model: null,
    active_adapter: false,
    observed_at: null,
    ...overrides,
  };
}

const SNAPSHOT: UsageSnapshot = {
  providers: [
    provider({ provider: "claude", plan: "Max 20x", configured_model: "claude-opus-5" }),
    provider({ provider: "cursor", logged_in: false }),
    provider({
      provider: "codex",
      plan: "Plus",
      configured_model: "gpt-5.6-sol",
      active_adapter: true,
      observed_at: "2026-08-08T12:33:25.191Z",
      meters: [
        {
          key: "primary",
          label: "Weekly",
          used: 84,
          limit: 100,
          unit: "percent",
          percent_used: 84,
          resets_at: null,
          status: "warning",
        },
      ],
      breakdown: [
        { name: "gpt-5.5", amount: 84188405, unit: "tokens", share_percent: 79.2 },
        { name: "gpt-5.6-sol", amount: 19143552, unit: "tokens", share_percent: 18 },
      ],
    }),
  ],
  near_limit: true,
  warnings: [],
  fetched_at: "2026-08-08T13:00:00Z",
};

function renderModal() {
  render(
    <UsageModal
      open
      snapshot={SNAPSHOT}
      isLoading={false}
      error={null}
      onClose={() => {}}
      onRefresh={() => {}}
    />,
  );
  return screen.getByRole("dialog", { name: /usage/i });
}

describe("UsageModal", () => {
  it("shows Codex alongside Claude and Cursor", () => {
    const dialog = renderModal();

    expect(within(dialog).getByText("Codex")).toBeInTheDocument();
    expect(within(dialog).getByText("Claude")).toBeInTheDocument();
    expect(within(dialog).getByText("Cursor")).toBeInTheDocument();
    expect(within(dialog).getByText("Plus")).toBeInTheDocument();
    expect(within(dialog).getByText("84%")).toBeInTheDocument();
  });

  it("names the model each provider is configured to run, and which one is active", () => {
    const dialog = renderModal();

    const pinned = within(dialog)
      .getAllByText(/./, { selector: ".usage-provider-model-value" })
      .map((node) => node.textContent);

    // An unpinned provider resolves to the CLI's own default, not to nothing.
    expect(pinned).toEqual(["claude-opus-5", "CLI default model", "gpt-5.6-sol"]);
    expect(within(dialog).getAllByText("active")).toHaveLength(1);
  });

  it("breaks usage down per model", () => {
    const dialog = renderModal();

    expect(within(dialog).getByText("Local activity by model (last 7 days)")).toBeInTheDocument();
    expect(within(dialog).getByText("gpt-5.5")).toBeInTheDocument();
    expect(within(dialog).getByText("84,188,405 tokens")).toBeInTheDocument();
    expect(within(dialog).getByText("79% share")).toBeInTheDocument();
  });

  it("dates a self-reported reading so a stale one is not read as current", () => {
    const dialog = renderModal();

    expect(within(dialog).getByText(/^Recorded /)).toBeInTheDocument();
  });
});
