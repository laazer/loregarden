import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import {
  pageFromPath,
  studioAgentPath,
  studioPath,
  studioTicketSessionPath,
  ticketPath,
} from "../appNavigation";
import {
  useAppPage,
  useArtifactTabFromRoute,
  useStudioResourceFromRoute,
  useStudioSectionFromRoute,
} from "../useAppNavigation";

function PageProbe() {
  const page = useAppPage();
  const artifactTab = useArtifactTabFromRoute();
  const studioSection = useStudioSectionFromRoute();
  const studioResourceId = useStudioResourceFromRoute();
  return (
    <div>
      <div data-testid="page">{page}</div>
      <div data-testid="artifact-tab">{artifactTab}</div>
      <div data-testid="studio-section">{studioSection}</div>
      <div data-testid="studio-resource">{studioResourceId ?? ""}</div>
    </div>
  );
}

describe("useAppNavigation", () => {
  it("derives the active page from the current route", () => {
    render(
      <MemoryRouter initialEntries={["/queue"]}>
        <Routes>
          <Route path="/queue/*" element={<PageProbe />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(screen.getByTestId("page")).toHaveTextContent("queue");
    expect(pageFromPath("/queue")).toBe("queue");
  });

  it("reads artifact tab from ticket routes", () => {
    render(
      <MemoryRouter initialEntries={[ticketPath("ticket-42", "hive")]}>
        <Routes>
          <Route path="/tickets/:ticketId/:artifactTab" element={<PageProbe />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(screen.getByTestId("page")).toHaveTextContent("dashboard");
    expect(screen.getByTestId("artifact-tab")).toHaveTextContent("hive");
  });

  it("reads studio section and resource from studio routes", () => {
    render(
      <MemoryRouter initialEntries={[studioAgentPath("backend_implementer")]}>
        <Routes>
          <Route path="/studio/:studioSection/:resourceId/*" element={<PageProbe />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(screen.getByTestId("page")).toHaveTextContent("studio");
    expect(screen.getByTestId("studio-section")).toHaveTextContent("agents");
    expect(screen.getByTestId("studio-resource")).toHaveTextContent("backend_implementer");
  });

  it("reads ticket studio session routes", () => {
    render(
      <MemoryRouter initialEntries={[studioTicketSessionPath("scope-1")]}>
        <Routes>
          <Route path="/studio/:studioSection/:resourceId/*" element={<PageProbe />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(screen.getByTestId("studio-section")).toHaveTextContent("tickets");
    expect(screen.getByTestId("studio-resource")).toHaveTextContent("scope-1");
    expect(studioPath("tickets")).toBe("/studio/tickets");
  });
});
