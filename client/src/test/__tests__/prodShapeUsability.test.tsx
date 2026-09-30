import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";

import { api } from "../../api/client";
import type { KnowledgeGraph } from "../../api/memoryApi";
import type { InitiativeMilestone, InitiativeView } from "../../api/client";
import type { MonitorFinding } from "../../api/types";
import { WorkflowMonitorView } from "../../components/dashboard/WorkflowMonitorView";
import { MemoryMap } from "../../components/knowledge/MemoryMap";
import { StructuredContent } from "../../components/reader/StructuredContent";
import { inferredEdges } from "../../lib/memoryInferred";
import { findUsabilityProblems } from "../../lib/usabilityCheck";
import { InitiativesPage } from "../../pages/InitiativesPage";
import attachable from "../fixtures/prod-shape/attachable-milestones.json";
import artifacts from "../fixtures/prod-shape/ticket-artifacts.json";
import initiatives from "../fixtures/prod-shape/initiatives.json";
import memoryGraph from "../fixtures/prod-shape/memory-graph-loregarden.json";
import monitorFindings from "../fixtures/prod-shape/monitor-findings.json";

/**
 * The surfaces that shipped unusable, rendered over the API's own responses on
 * the production-shaped scenario (`server/tests/test_prod_shape_fixtures.py`),
 * and checked for the shapes an unusable surface takes.
 *
 * Each check has a control: the old rendering, rebuilt from the same data,
 * must be flagged — otherwise a pass would only prove the checker is blind.
 */

jest.mock("../../api/client");
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

it("Initiatives with no initiatives still shows what to group, without a wall", async () => {
  mockApi.initiatives.mockResolvedValue(initiatives as unknown as InitiativeView[]);
  mockApi.attachableMilestones.mockResolvedValue(attachable as unknown as InitiativeMilestone[]);
  const { container } = render(withProviders(<InitiativesPage />));

  await screen.findByRole("heading", { name: /milestones without an initiative/i });

  expect(findUsabilityProblems(container)).toEqual([]);
});

it("a production-sized plan artifact renders as a document, not escaped JSON", () => {
  const plan = (artifacts as { items: { kind: string; content: unknown }[] }).items.find((a) => a.kind === "plan");
  const { container } = render(<StructuredContent value={plan?.content} />);

  expect(container.textContent).not.toMatch(/\\n|"document":/);
  expect(screen.getByRole("heading", { name: "Steps" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Left out" })).toBeInTheDocument();
});
