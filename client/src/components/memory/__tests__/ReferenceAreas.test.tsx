import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";

import { api } from "../../../api/client";
import type { AreaView } from "../../../api/referenceAreasApi";
import { useToastStore } from "../../../state/toastStore";
import { ReferenceAreasManager } from "../ReferenceAreasManager";
import { ReferencePasteForm } from "../ReferencePasteForm";

jest.mock("../../../api/client");

const mockApi = api as jest.Mocked<typeof api>;

function area(workspace: string, slug: string, name: string, extra: Partial<AreaView> = {}): AreaView {
  return {
    id: `${workspace}-${slug}`, workspace_slug: workspace, slug, label: `${workspace}/${slug}`, name,
    description: `${name} description`, keywords: [], home_ticket: null, archived: false, ...extra,
  };  // prettier-ignore
}

const AREAS = [
  area("loregarden", "capacity-ops", "Capacity and ops"),
  area("loregarden", "gates-ci", "Gates and CI"),
  area("loremaker", "ops", "Operations"),
];

// Two entries from the self-hosting post the feature was asked for.
const POST = `1. SigNoz – Open-Source Datadog Alternative
* Why it's trending: unifies application metrics, distributed tracing, and log management.
* GitHub: https://github.com/SigNoz/signoz
2. Woodpecker CI – Minimalist Container-Native CI/CD
* Why it's trending: runs every build step in isolated Docker containers.
* GitHub: https://github.com/woodpecker-ci/woodpecker`;

const SIGNOZ = "https://github.com/SigNoz/signoz";
const WOODPECKER = "https://github.com/woodpecker-ci/woodpecker";

function wrap(node: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>{node}</MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  jest.clearAllMocks();
  useToastStore.setState({ toasts: [] });
  mockApi.workspaces.mockResolvedValue([] as Awaited<ReturnType<typeof api.workspaces>>);
  mockApi.initiatives.mockResolvedValue([]);
  mockApi.referenceAreas.mockResolvedValue(AREAS);
});

describe("ReferencePasteForm with project areas", () => {
  it("pre-ticks the agent's areas per link, keeps them editable, and sends them with each link", async () => {
    mockApi.suggestReferenceAreas.mockResolvedValue({
      mode: "agent",
      suggestions: [
        { url: SIGNOZ, areas: [
          { area: "loremaker/ops", reason: "Tracing for loremaker's services" },
          { area: "loregarden/capacity-ops", reason: "Metrics for the capacity ledger" },
        ] },
        { url: WOODPECKER, areas: [] },
      ],
      warnings: [],
    });  // prettier-ignore
    mockApi.addReferenceSources.mockResolvedValue([]);
    wrap(<ReferencePasteForm workspace="loregarden" onClose={jest.fn()} />);
    const form = screen.getByRole("form", { name: "Paste a list of references" });
    fireEvent.change(within(form).getByRole("textbox", { name: "Text with links" }), { target: { value: POST } });

    fireEvent.click(within(form).getByRole("button", { name: "Suggest areas" }));
    await waitFor(() =>
      expect(mockApi.suggestReferenceAreas).toHaveBeenCalledWith({
        mode: "agent",
        entries: [
          expect.objectContaining({ url: SIGNOZ, name: "SigNoz" }),
          expect.objectContaining({ url: WOODPECKER, name: "Woodpecker CI" }),
        ],
      }),
    );
    const chips = await within(form).findByRole("list", { name: "Areas for SigNoz" });
    // The area in view reads by name; another workspace's says whose it is.
    expect(within(chips).getAllByRole("listitem").map((item) => item.textContent)).toEqual([
      "loremaker · Operations×",
      "Capacity and ops×",
    ]);
    expect(within(chips).getAllByRole("listitem")[0]).toHaveAttribute("title", "Tracing for loremaker's services");
    expect(within(form).getByText(/mapped 1 of 2 links/)).toBeInTheDocument();

    // The operator corrects it: drops one suggestion and maps the other link by hand.
    fireEvent.click(within(chips).getByRole("button", { name: "Remove loregarden/capacity-ops from SigNoz" }));
    fireEvent.change(within(form).getByRole("combobox", { name: "Add an area for Woodpecker CI" }), {
      target: { value: "loregarden/gates-ci" },
    });
    fireEvent.click(within(form).getByRole("button", { name: "Queue 2 for review" }));

    await waitFor(() => expect(mockApi.addReferenceSources).toHaveBeenCalled());
    const body = mockApi.addReferenceSources.mock.calls[0][0];
    expect(body.entries?.map((entry) => [entry.url, entry.areas])).toEqual([
      [SIGNOZ, [{ area: "loremaker/ops", reason: "Tracing for loremaker's services" }]],
      [WOODPECKER, [{ area: "loregarden/gates-ci", reason: "" }]],
    ]);
  });

  it("says why there are no suggestions when the agent fails, and leaves the rows as they were", async () => {
    mockApi.suggestReferenceAreas.mockRejectedValue(new Error("The agent ran under loregarden's runtime and failed"));
    wrap(<ReferencePasteForm workspace="loregarden" onClose={jest.fn()} />);
    const form = screen.getByRole("form", { name: "Paste a list of references" });
    fireEvent.change(within(form).getByRole("textbox", { name: "Text with links" }), { target: { value: POST } });
    fireEvent.click(within(form).getByRole("button", { name: "Suggest areas" }));

    expect(await within(form).findByText(/No suggestions: The agent ran under/)).toBeInTheDocument();
    expect(useToastStore.getState().toasts.map((toast) => toast.title)).toContain("Areas were not suggested");
    expect(within(form).queryByRole("list", { name: "Areas for SigNoz" })).not.toBeInTheDocument();
    expect(within(form).getByRole("button", { name: "Keywords only" })).toBeEnabled();
  });
});

describe("ReferenceAreasManager", () => {
  it("lists areas grouped by workspace, the one in view first, and adds one", async () => {
    mockApi.referenceAreas.mockResolvedValue([
      ...AREAS,
      area("blobert", "enemy-generator", "Enemy generator", { home_ticket: "bl-adopt-4" }),
    ]);
    mockApi.addReferenceArea.mockResolvedValue(area("loremaker", "stratos", "Stratos"));
    wrap(<ReferenceAreasManager workspace="loremaker" onClose={jest.fn()} />);

    const table = await screen.findByRole("table", { name: "Project areas" });
    const groups = within(table).getAllByRole("rowheader").map((cell) => cell.textContent);
    expect(groups).toEqual(["loremaker · 1", "blobert · 1", "loregarden · 2"]);
    expect(within(table).getByText("bl-adopt-4")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Add an area" }));
    const form = screen.getByRole("form", { name: "Add a project area" });
    fireEvent.change(within(form).getByLabelText("Code name"), { target: { value: "Stratos" } });
    expect(within(form).getByLabelText("Code name")).toHaveValue("stratos");
    fireEvent.change(within(form).getByLabelText("Name"), { target: { value: "Stratos" } });
    fireEvent.change(within(form).getByLabelText(/comma-separated/), { target: { value: "workflow, data model ," } });
    fireEvent.click(within(form).getByRole("button", { name: "Add area" }));

    await waitFor(() =>
      expect(mockApi.addReferenceArea).toHaveBeenCalledWith({
        workspace_slug: "loremaker", slug: "stratos", name: "Stratos", description: "",
        keywords: ["workflow", "data model"], home_ticket: "",
      }),
    );  // prettier-ignore
    expect(await screen.findByRole("table", { name: "Project areas" })).toBeInTheDocument();
  });

  it("keeps the fields and says why when saving fails", async () => {
    mockApi.addReferenceArea.mockRejectedValue(new Error("loremaker/stratos already exists"));
    wrap(<ReferenceAreasManager workspace="loremaker" onClose={jest.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: "Add an area" }));
    const form = screen.getByRole("form", { name: "Add a project area" });
    fireEvent.change(within(form).getByLabelText("Code name"), { target: { value: "stratos" } });
    fireEvent.change(within(form).getByLabelText("Name"), { target: { value: "Stratos" } });
    fireEvent.click(within(form).getByRole("button", { name: "Add area" }));

    expect(await within(form).findByRole("alert")).toHaveTextContent("Not saved: loremaker/stratos already exists");
    expect(within(form).getByLabelText("Name")).toHaveValue("Stratos");
  });
});
