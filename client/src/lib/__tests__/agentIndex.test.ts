import type { AgentRunStats, StudioAgent } from "../../api/types";
import {
  agentIndexRows,
  isFailureAlert,
  matchesAgentFilter,
  sortAgentRows,
} from "../agentIndex";

function agent(slug: string, extra: Partial<StudioAgent> = {}): StudioAgent {
  return { slug, name: slug, description: "", built_in: true, ...extra } as StudioAgent;
}

function stat(agent_id: string, runs: number, failed: number, last = "2026-10-28T12:00:00"): AgentRunStats {
  return { agent_id, runs, failed, last_run_at: last };
}

const names = (rows: ReturnType<typeof agentIndexRows>) => rows.map((row) => row.agent.slug);

describe("agentIndex", () => {
  const agents = [agent("planner"), agent("repair"), agent("lonely"), agent("blog_post")];
  const stats = [stat("planner", 660, 32), stat("repair", 149, 96), stat("lonely", 1, 1)];

  it("joins run counts by slug, and leaves an agent that never ran with no rate", () => {
    const rows = agentIndexRows(agents, stats);
    const blog = rows.find((row) => row.agent.slug === "blog_post");
    expect(blog).toMatchObject({ runs: 0, failed: 0, rate: null, lastRunAt: null });
    expect(rows.find((row) => row.agent.slug === "repair")?.rate).toBeCloseTo(96 / 149);
  });

  it("puts the worst reliable rate first, and a rate from too few runs below every reliable one", () => {
    const rows = sortAgentRows(agentIndexRows(agents, stats), "rate", "descending");
    // lonely failed 1 of 1 (100%), but one run is no evidence.
    expect(names(rows)).toEqual(["repair", "planner", "lonely", "blog_post"]);
  });

  it("alerts at 15% on enough runs, and never on a thin sample", () => {
    const rows = agentIndexRows(
      [agent("a"), agent("b"), agent("c"), agent("d")],
      [stat("a", 100, 15), stat("b", 100, 14), stat("c", 9, 9), stat("d", 301, 44)],
    );
    // d is 14.6%, printed as 15%, so it is flagged like a.
    expect(rows.map(isFailureAlert)).toEqual([true, false, false, true]);
  });

  it("sorts by name, and breaks ties by runs", () => {
    const rows = agentIndexRows([agent("b"), agent("a"), agent("c")], [stat("b", 5, 0), stat("c", 9, 0)]);
    expect(names(sortAgentRows(rows, "name", "ascending"))).toEqual(["a", "b", "c"]);
    expect(names(sortAgentRows(rows, "failed", "descending"))).toEqual(["c", "b", "a"]);
  });

  it("filters on name, slug and description", () => {
    const [row] = agentIndexRows([agent("repair", { name: "Repair", description: "Fixes gates" })], []);
    expect(matchesAgentFilter(row, "gates")).toBe(true);
    expect(matchesAgentFilter(row, "  REP ")).toBe(true);
    expect(matchesAgentFilter(row, "planner")).toBe(false);
  });
});
