/**
 * Machine → Tokens: "are agent runs getting cheaper in tokens?" Figures below
 * are the live database's, measured 2026-11-05 (uncached view trimmed).
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { api } from "../../../api/client";
import type { TokenTrend, TokenTrendWeek } from "../../../api/usageTypes";
import { findUsabilityProblems } from "../../../lib/usabilityCheck";
import { formatTokens } from "../../../lib/formatTokens";
import { TokenTrendView } from "../TokenTrendView";

jest.mock("../../../api/client", () => ({ api: { tokenTrend: jest.fn() } }));
const mockTrend = api.tokenTrend as jest.MockedFunction<typeof api.tokenTrend>;

function week(
  week_start: string,
  runs: number,
  measured: number,
  median: number | null,
  partial = false,
): TokenTrendWeek {
  return {
    week_start,
    partial,
    tickets: Math.ceil(runs / 5),
    runs,
    measured_runs: measured,
    unmeasured_runs: runs - measured,
    input_tokens: measured ? 1000 * measured : null,
    output_tokens: measured ? 20_000 * measured : null,
    cache_read_tokens: measured ? 3_000_000 * measured : null,
    cache_write_tokens: measured ? 100_000 * measured : null,
    total_tokens: measured ? 3_121_000 * measured : null,
    median_tokens_per_run: median,
  };
}

const LIVE: TokenTrend = {
  weeks: [
    week("2026-08-17", 13, 0, null),
    week("2026-08-24", 125, 9, 1_959_990),
    week("2026-08-31", 139, 42, 2_108_790),
    week("2026-09-07", 168, 92, 1_249_551),
    week("2026-09-14", 340, 277, 1_342_264),
    week("2026-09-21", 327, 294, 1_251_942),
    week("2026-09-28", 268, 49, 3_987_365),
    week("2026-10-05", 197, 166, 2_069_406),
    week("2026-10-12", 657, 552, 2_262_302),
    week("2026-10-19", 749, 650, 2_705_393),
    week("2026-10-26", 675, 529, 2_263_621),
    week("2026-11-02", 308, 278, 5_299_280, true),
  ],
  comparison: {
    recent_start: "2026-10-05",
    prior_start: "2026-09-07",
    recent_measured_runs: 1897,
    prior_measured_runs: 712,
    recent_median: 2_389_608,
    prior_median: 1_368_864,
    change_percent: 74.6,
    direction: "up",
  },
  metric: "total",
  models: ["(unattributed)", "claude-opus-5", "claude-sonnet-5"],
  stages: ["implement", "review"],
};

function renderView() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <TokenTrendView />
    </QueryClientProvider>,
  );
}

beforeEach(() => mockTrend.mockReset());

describe("TokenTrendView", () => {
  it("answers the question with the per-run comparison and a row per week", async () => {
    mockTrend.mockResolvedValue(LIVE);
    const { container } = renderView();

    expect(await screen.findByText(/Up 74\.6% per run/)).toBeInTheDocument();
    expect(screen.getByText(/1\.37M over 712 runs/)).toBeInTheDocument();
    const table = screen.getByRole("table");
    // Header row + 12 weeks.
    expect(within(table).getAllByRole("row")).toHaveLength(13);
    expect(
      within(table).getByRole("rowheader", { name: /2026-11-02 so far/ }),
    ).toBeInTheDocument();
    expect(screen.getByText(/mix 3 models/)).toBeInTheDocument();
    expect(findUsabilityProblems(container)).toEqual([]);
  });

  it("shows a dash for an unmeasured week, never zero", async () => {
    mockTrend.mockResolvedValue(LIVE);
    renderView();
    const row = await screen.findByRole("row", { name: /2026-08-17/ });
    expect(within(row).getAllByText("—").length).toBeGreaterThanOrEqual(5);
  });

  it("refetches with the chosen model and drops the mixed-model note", async () => {
    mockTrend.mockResolvedValue(LIVE);
    renderView();
    await screen.findByText(/Up 74\.6%/);

    await userEvent.selectOptions(
      screen.getByLabelText("Model"),
      "claude-opus-5",
    );
    expect(mockTrend).toHaveBeenLastCalledWith({
      metric: "total",
      model: "claude-opus-5",
      stage_key: null,
    });
    expect(screen.queryByText(/mix 3 models/)).not.toBeInTheDocument();
  });

  it("withholds a percentage when either side is too thin", async () => {
    mockTrend.mockResolvedValue({
      ...LIVE,
      comparison: {
        ...LIVE.comparison,
        prior_measured_runs: 3,
        change_percent: null,
        direction: "unknown",
      },
    });
    renderView();
    expect(
      await screen.findByText(/Not enough measured runs to compare: 3/),
    ).toBeInTheDocument();
  });

  it("says what would appear when there are no runs", async () => {
    mockTrend.mockResolvedValue({
      ...LIVE,
      weeks: LIVE.weeks.map((w) => ({ ...week(w.week_start, 0, 0, null) })),
    });
    renderView();
    expect(
      await screen.findByText(/No finished agent runs match/),
    ).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("names the failure and offers a retry", async () => {
    mockTrend
      .mockRejectedValueOnce(new Error("boom"))
      .mockResolvedValueOnce(LIVE);
    renderView();
    expect(await screen.findByRole("alert")).toHaveTextContent("boom");
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText(/Up 74\.6%/)).toBeInTheDocument();
  });
});

describe("formatTokens", () => {
  it.each([
    [null, "—"],
    [0, "0"],
    [999, "999"],
    [131_678, "132k"],
    [2_389_608, "2.39M"],
    [6_932_167_794, "6.93B"],
  ])("%p → %p", (value, expected) => {
    expect(formatTokens(value)).toBe(expected);
  });
});
