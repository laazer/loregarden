import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";

import { api } from "../../api/client";
import type { KnowledgeGraph } from "../../api/memoryApi";
import type { InitiativeMilestone, InitiativeSuggestionSet, InitiativeView, TicketDetail } from "../../api/client";
import type { MonitorFinding, TicketArtifactsFeed, TicketLedger } from "../../api/types";
import { TicketOutputs } from "../../components/dashboard/TicketOutputs";
import { TicketTimeline } from "../../components/dashboard/TicketTimeline";
import { WorkflowMonitorView } from "../../components/dashboard/WorkflowMonitorView";
import { stageFanoutApi } from "../../lib/stageFanoutApi";
import { MemoryMap } from "../../components/knowledge/MemoryMap";
import { StructuredContent } from "../../components/reader/StructuredContent";
import { inferredEdges } from "../../lib/memoryInferred";
import { findUsabilityProblems } from "../../lib/usabilityCheck";
import { InitiativeSuggestionsPage } from "../../pages/InitiativeSuggestionsPage";
import { InitiativesPage } from "../../pages/InitiativesPage";
import attachable from "../fixtures/prod-shape/attachable-milestones.json";
import artifacts from "../fixtures/prod-shape/ticket-artifacts.json";
import suggestions from "../fixtures/prod-shape/initiative-suggestions.json";
import initiatives from "../fixtures/prod-shape/initiatives.json";
import memoryGraph from "../fixtures/prod-shape/memory-graph-loregarden.json";
import monitorFindings from "../fixtures/prod-shape/monitor-findings.json";
import historyArtifacts from "../fixtures/prod-shape/ticket-history-artifacts.json";
import historyLedger from "../fixtures/prod-shape/ticket-history-ledger.json";

/**
 * The surfaces that shipped unusable, rendered over the API's own responses on
 * the production-shaped scenario (`server/tests/test_prod_shape_fixtures.py`),
 * and checked for the shapes an unusable surface takes.
 *
 * Each check has a control: the old rendering, rebuilt from the same data,
 * must be flagged — otherwise a pass would only prove the checker is blind.
 */

jest.mock("../../api/client");
jest.mock("../../lib/stageFanoutApi");
const mockApi = api as jest.Mocked<typeof api>;

const findings = monitorFindings as unknown as MonitorFinding[];
const graph = memoryGraph as unknown as KnowledgeGraph;

function withProviders(children: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <MemoryRouter>
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    </MemoryRouter>
  );
}

describe("Monitor at production volume", () => {
  it("control: the old flat list of summaries is flagged", () => {
    const { container } = render(
      <ul>
        {findings.map((finding, index) => (
          <li key={index}>{finding.summary}</li>
        ))}
      </ul>,
    );

    const kinds = findUsabilityProblems(container).map((p) => p.kind);
    expect(kinds).toEqual(expect.arrayContaining(["repeated-text", "dead-end-list"]));
  });

  it("the monitor view is not", async () => {
    mockApi.monitorFindings.mockResolvedValue(findings);
    const { container } = render(withProviders(<WorkflowMonitorView />));

    await screen.findByRole("button", { name: /needs attention/i });

    expect(findUsabilityProblems(container)).toEqual([]);
  });
});

describe("Memory map at production volume", () => {
  it("control: the map without inferred groups is an edgeless graph", () => {
    const { container } = render(
      <MemoryMap nodes={graph.nodes} relations={graph.relations} inferred={[]} selectedId={null} onSelect={() => {}} />,
    );

    expect(findUsabilityProblems(container).map((p) => p.kind)).toContain("edgeless-graph");
  });

  it("the map with inferred groups is not", () => {
    const { container } = render(
      <MemoryMap
        nodes={graph.nodes}
        relations={graph.relations}
        inferred={inferredEdges(graph.inferred)}
        selectedId={null}
        onSelect={() => {}}
      />,
    );

    expect(findUsabilityProblems(container)).toEqual([]);
  });
});

it("Initiatives shows its one initiative and what is left to group, without a wall", async () => {
  mockApi.initiatives.mockResolvedValue(initiatives as unknown as InitiativeView[]);
  mockApi.attachableMilestones.mockResolvedValue(attachable as unknown as InitiativeMilestone[]);
  const { container } = render(withProviders(<InitiativesPage />));

  await screen.findByRole("heading", { name: "Trustworthy gate outcomes on every stage transition" });
  await screen.findByRole("heading", { name: /milestones without an initiative/i });

  expect(findUsabilityProblems(container)).toEqual([]);
});

it("Suggested initiatives at production volume names each group and stays operable", async () => {
  const data = suggestions as unknown as InitiativeSuggestionSet;
  mockApi.initiativeSuggestions.mockResolvedValue(data);
  const { container } = render(withProviders(<InitiativeSuggestionsPage />));

  for (const suggestion of data.suggestions) {
    expect(await screen.findByDisplayValue(suggestion.title)).toBeInTheDocument();
  }
  const problems = findUsabilityProblems(container).filter((p) => p.kind !== "tabular-list");
  expect(problems).toEqual([]);
});

// lg-initiatives-cross-899 — open: SprintCard lists 392 members that each repeat
// five fields. When it becomes a table this passes, `failing` turns that into a
// red run, and the case above drops its filter.
it.failing("Suggested initiatives compares a sprint's members in a table", async () => {
  mockApi.initiativeSuggestions.mockResolvedValue(suggestions as unknown as InitiativeSuggestionSet);
  const { container } = render(withProviders(<InitiativeSuggestionsPage />));

  await screen.findByDisplayValue((suggestions as unknown as InitiativeSuggestionSet).suggestions[0].title);
  expect(findUsabilityProblems(container).map((p) => p.kind)).not.toContain("tabular-list");
});

it("a production-sized plan artifact renders as a document, not escaped JSON", () => {
  const plan = (artifacts as { items: { kind: string; content: unknown }[] }).items.find((a) => a.kind === "plan");
  const { container } = render(<StructuredContent value={plan?.content} />);

  expect(container.textContent).not.toMatch(/\\n|"document":/);
  expect(screen.getByRole("heading", { name: "Steps" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Left out" })).toBeInTheDocument();
});

describe("A worked ticket's history at the live p90 (33 runs, 172 rows)", () => {
  const feed = historyArtifacts as unknown as TicketArtifactsFeed;
  const ledger = historyLedger as unknown as TicketLedger;
  const ticketId = "worked";

  beforeEach(() => {
    mockApi.ticketArtifacts.mockResolvedValue(feed);
    mockApi.ticketLedger.mockResolvedValue(ledger);
    (stageFanoutApi.list as jest.Mock).mockResolvedValue({ groups: [], open_group_id: null });
  });

  it("control: every row in one flat list is flagged", () => {
    const { container } = render(
      <ul>
        {feed.items.map((item) => (
          <li key={item.id}>
            {item.kind} {item.title}
          </li>
        ))}
      </ul>,
    );

    expect(findUsabilityProblems(container).map((p) => p.kind)).toEqual(
      expect.arrayContaining(["dead-end-list", "unfiltered-long-list"]),
    );
  });

  it("the Timeline is not", async () => {
    const ticket = { id: ticketId, stages: [], blocking_issues: "", artifacts: {} } as unknown as TicketDetail;
    const { container } = render(
      withProviders(
        <TicketTimeline ticket={ticket} runs={[]} isActive={false} pendingApprovals={0} onOpenRunLog={() => {}} />,
      ),
    );

    await screen.findByText(/33 runs/);
    expect(findUsabilityProblems(container)).toEqual([]);
  });

  it("Outputs is not, and hides the bookkeeping", async () => {
    const { container } = render(withProviders(<TicketOutputs ticketId={ticketId} isActive={false} />));

    await screen.findByRole("table");
    expect(screen.getByRole("checkbox", { name: /Show 69 system records/ })).not.toBeChecked();
    expect(findUsabilityProblems(container)).toEqual([]);
  });
});
