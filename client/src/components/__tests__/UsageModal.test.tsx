import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

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

/**
 * `.modal-body usage-modal-body` is a bounded `overflow: auto` region
 * (lg-milestone-that-2215). `Close` and `Refresh` are both in `.modal-footer`
 * OUTSIDE it, so the per-provider report could not be scrolled from the
 * keyboard — and this is the one body that genuinely overflows, at three
 * providers with meters and a per-model breakdown each.
 */
describe("the usage report's keyboard path", () => {
  const REGION = "Usage by provider";

  function renderWith(props: {
    snapshot?: UsageSnapshot | null;
    isLoading?: boolean;
    error?: { message: string } | null;
  }) {
    const onClose = jest.fn();
    render(
      <UsageModal
        open
        snapshot={props.snapshot === undefined ? SNAPSHOT : (props.snapshot ?? undefined)}
        isLoading={props.isLoading ?? false}
        error={(props.error ?? null) as never}
        onClose={onClose}
        onRefresh={() => {}}
      />,
    );
    return { onClose };
  }

  it("is a tab stop that steps on to the footer's Close — AC3", async () => {
    renderWith({});

    const body = screen.getByRole("group", { name: REGION });
    expect(body).toHaveAttribute("tabindex", "0");
    expect(body).toHaveClass("scroll-focus-ring");

    // The footer FOLLOWS the body, so Tab forward is the step this surface
    // offers. Named by its text rather than by role alone: the header's cross
    // carries the same accessible name.
    body.focus();
    await userEvent.tab();
    const footerClose = screen
      .getAllByRole("button", { name: "Close" })
      .find((button) => button.textContent === "Close");
    expect(footerClose).toHaveFocus();
  });

  it("puts each substitute state in its place rather than inside the region — AC8", () => {
    // The pane used to hold the loading line, the error line AND the report,
    // so a tab stop on it would have announced "Usage by provider" over the
    // word "Loading…". Each state is now one line where the region was.
    renderWith({ snapshot: null, isLoading: true });
    expect(screen.queryByRole("group", { name: REGION })).toBeNull();
    expect(screen.getByText("Loading usage…")).toBeInTheDocument();

    cleanup();
    renderWith({ snapshot: null, error: { message: "claude CLI is not logged in" } });
    expect(screen.queryByRole("group", { name: REGION })).toBeNull();
    expect(screen.getByText("claude CLI is not logged in")).toBeInTheDocument();

    cleanup();
    renderWith({ snapshot: { providers: [], near_limit: false, warnings: [], fetched_at: null } as never });
    expect(screen.queryByRole("group", { name: REGION })).toBeNull();
    expect(screen.getByText("No usage has been reported for any provider yet.")).toBeInTheDocument();
  });

  it("shows a failed refresh beside the numbers it could not replace", () => {
    // react-query keeps `data` when a refetch fails (hooks/useUsageSnapshot.ts
    // hands this modal one query's `data` and `error`), so the operator pressing
    // Refresh and failing still has the previous report on screen. The error
    // line lives outside the branch for exactly that case, and
    // api/queryClient.ts suppresses the toast whenever data is already present,
    // so this paragraph is the only surface that says the refresh failed.
    renderWith({ error: { message: "claude CLI is not logged in" } });

    expect(screen.getByRole("group", { name: REGION })).not.toBeNull();
    // role="alert" because nothing else announces it: the toast is suppressed and
    // the stale numbers beside it look unchanged. Its own class, because
    // .usage-provider-error is a 240px right-aligned span inside a provider row.
    const line = screen.getByRole("alert");
    expect(line).toHaveTextContent("claude CLI is not logged in");
    expect(line).toHaveClass("usage-modal-error");
  });

  it("lets Escape from inside the region still close the dialog — AC9", () => {
    const { onClose } = renderWith({});

    const body = screen.getByRole("group", { name: REGION });
    body.focus();
    fireEvent.keyDown(body, { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
  });
});
