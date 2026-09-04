import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";

import type { StudioWorkflowDrift } from "../../../api/types";
import { WorkflowDriftNotice } from "../WorkflowDriftNotice";

const studioWorkflowDrift = jest.fn();

jest.mock("../../../api/client", () => ({
  api: {
    studioWorkflowDrift: (slug: string) => studioWorkflowDrift(slug),
  },
}));

function drift(overrides: Partial<StudioWorkflowDrift> = {}): StudioWorkflowDrift {
  return {
    slug: "demo",
    published_template_slug: "studio-demo",
    published: true,
    drifted: true,
    stages_added: [],
    stages_removed: [],
    stages_changed: {},
    draft_transition_count: 2,
    template_transition_count: 2,
    template_version: 3,
    stranded: { count: 0, stage_keys: [], ticket_ids: [] },
    ...overrides,
  };
}

function renderNotice() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <WorkflowDriftNotice slug="demo" />
    </QueryClientProvider>,
  );
}

describe("WorkflowDriftNotice", () => {
  beforeEach(() => studioWorkflowDrift.mockReset());

  it("names the stages a publish would remove", async () => {
    studioWorkflowDrift.mockResolvedValue(drift({ stages_removed: ["verify", "done"] }));
    renderNotice();

    expect(await screen.findByText(/differs from studio-demo/i)).toBeInTheDocument();
    expect(screen.getByText("verify, done")).toBeInTheDocument();
  });

  it("says how many live tickets a removal would strand", async () => {
    studioWorkflowDrift.mockResolvedValue(
      drift({
        stages_removed: ["verify"],
        stranded: { count: 3, stage_keys: ["verify"], ticket_ids: ["a", "b", "c"] },
      }),
    );
    renderNotice();

    expect(
      await screen.findByText(/3 live ticket\(s\) are on a stage this would remove/i),
    ).toBeInTheDocument();
  });

  it("renders nothing when the draft matches its template", async () => {
    studioWorkflowDrift.mockResolvedValue(drift({ drifted: false }));
    const { container } = renderNotice();

    await waitFor(() => expect(studioWorkflowDrift).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing for a draft that was never published", async () => {
    // Never published is not drift — there is nothing to have drifted from, and
    // a banner on every new draft is a banner nobody reads.
    studioWorkflowDrift.mockResolvedValue(drift({ published: false, drifted: false }));
    const { container } = renderNotice();

    await waitFor(() => expect(studioWorkflowDrift).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });
});
