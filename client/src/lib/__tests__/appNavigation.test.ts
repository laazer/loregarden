import {
  artifactTabFromPath,
  pageFromPath,
  pathForPage,
  studioAgentNewPath,
  studioAgentPath,
  studioPath,
  studioResourceFromPath,
  studioSectionFromPath,
  studioTicketSessionPath,
  studioWorkflowPath,
  ticketPath,
} from "../appNavigation";

describe("appNavigation", () => {
  it("maps known paths to app pages", () => {
    expect(pageFromPath("/")).toBe("dashboard");
    expect(pageFromPath("/studio")).toBe("studio");
    expect(pageFromPath("/studio/agents")).toBe("studio");
    expect(pageFromPath("/studio/workflows")).toBe("studio");
    expect(pageFromPath("/editor")).toBe("editor");
    expect(pageFromPath("/queue")).toBe("queue");
    expect(pageFromPath("/tickets/abc-123/diff")).toBe("dashboard");
  });

  it("falls back to dashboard for unknown paths", () => {
    expect(pageFromPath("/unknown")).toBe("dashboard");
  });

  it("returns canonical paths for each page", () => {
    expect(pathForPage("dashboard")).toBe("/");
    expect(pathForPage("studio")).toBe("/studio/agents");
    expect(pathForPage("editor")).toBe("/editor");
    expect(pathForPage("queue")).toBe("/queue");
  });

  it("builds and parses ticket routes", () => {
    expect(ticketPath("abc-123")).toBe("/tickets/abc-123/diff");
    expect(ticketPath("abc-123", "logs")).toBe("/tickets/abc-123/logs");
    expect(artifactTabFromPath("/tickets/abc-123/logs")).toBe("logs");
    expect(artifactTabFromPath("/tickets/abc-123")).toBeNull();
  });

  it("builds and parses studio routes", () => {
    expect(studioPath("agents")).toBe("/studio/agents");
    expect(studioPath("workflows")).toBe("/studio/workflows");
    expect(studioSectionFromPath("/studio/workflows")).toBe("workflows");
    expect(studioSectionFromPath("/studio/tickets")).toBe("tickets");
    expect(studioSectionFromPath("/studio")).toBe("agents");
    expect(studioAgentPath("planner")).toBe("/studio/agents/planner");
    expect(studioAgentNewPath()).toBe("/studio/agents/new");
    expect(studioWorkflowPath("loregarden-tdd")).toBe("/studio/workflows/loregarden-tdd");
    expect(studioTicketSessionPath("session-42")).toBe("/studio/tickets/session-42");
    expect(studioResourceFromPath("/studio/agents/planner")).toBe("planner");
    expect(studioResourceFromPath("/studio/tickets/new")).toBe("new");
    expect(studioResourceFromPath("/studio/agents")).toBeNull();
  });
});
