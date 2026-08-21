import {
  artifactTabFromPath,
  isArtifactsSubTab,
  pageFromPath,
  pathForPage,
  PRIMARY_ARTIFACT_TABS,
  studioAgentNewPath,
  studioAgentPath,
  studioPath,
  studioResourceFromPath,
  studioSectionFromPath,
  studioTicketSessionPath,
  studioWorkflowPath,
  ticketIdFromPath,
  ticketPath,
  viewIdFromPath,
  viewPath,
} from "../appNavigation";

describe("appNavigation", () => {
  it("maps known paths to app pages", () => {
    expect(pageFromPath("/")).toBe("home");
    expect(pageFromPath("/chat")).toBe("chat");
    expect(pageFromPath("/console")).toBe("dashboard");
    expect(pageFromPath("/studio")).toBe("studio");
    expect(pageFromPath("/studio/agents")).toBe("studio");
    expect(pageFromPath("/studio/workflows")).toBe("studio");
    expect(pageFromPath("/editor")).toBe("editor");
    expect(pageFromPath("/queue")).toBe("queue");
    expect(pageFromPath("/branch-triage")).toBe("branch-triage");
    expect(pageFromPath("/tickets/abc-123/diff")).toBe("dashboard");
  });

  it("falls back to home for unknown paths", () => {
    expect(pageFromPath("/unknown")).toBe("home");
  });

  it("returns canonical paths for each page", () => {
    expect(pathForPage("home")).toBe("/");
    expect(pathForPage("chat")).toBe("/chat");
    expect(pathForPage("dashboard")).toBe("/console");
    expect(pathForPage("studio")).toBe("/studio/agents");
    expect(pathForPage("editor")).toBe("/editor");
    expect(pathForPage("queue")).toBe("/queue");
    expect(pathForPage("branch-triage")).toBe("/branch-triage");
  });

  it("builds and parses ticket routes", () => {
    expect(ticketPath("abc-123")).toBe("/tickets/abc-123/diff");
    expect(ticketPath("abc-123", "logs")).toBe("/tickets/abc-123/logs");
    expect(artifactTabFromPath("/tickets/abc-123/logs")).toBe("logs");
    expect(artifactTabFromPath("/tickets/abc-123")).toBeNull();
  });

  it("keeps artifacts sub-tabs routable but off the primary bar", () => {
    expect(PRIMARY_ARTIFACT_TABS).not.toContain("errors");
    expect(PRIMARY_ARTIFACT_TABS).not.toContain("context");
    expect(PRIMARY_ARTIFACT_TABS).not.toContain("ledger");
    expect(PRIMARY_ARTIFACT_TABS).toContain("artifacts");
    expect(isArtifactsSubTab("artifacts")).toBe(true);
    expect(isArtifactsSubTab("errors")).toBe(true);
    expect(isArtifactsSubTab("context")).toBe(true);
    expect(isArtifactsSubTab("ledger")).toBe(true);
    expect(isArtifactsSubTab("diff")).toBe(false);
    expect(artifactTabFromPath("/tickets/abc-123/errors")).toBe("errors");
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

  it("builds and parses the gates studio route", () => {
    expect(studioPath("gates")).toBe("/studio/gates");
    expect(studioSectionFromPath("/studio/gates")).toBe("gates");
    expect(studioSectionFromPath("/studio/gates/anything")).toBe("gates");
  });

  it("reads a view id off its route, decoded", () => {
    expect(viewIdFromPath(viewPath("v grid"))).toBe("v grid");
    expect(viewIdFromPath("/console")).toBeNull();
  });

  it("hands back a malformed view segment rather than throwing on it", () => {
    // A URL segment is whatever the address bar holds, and a bare `%` is not
    // valid percent-encoding. This runs during render, above the error
    // boundaries, so a throw here blanks the whole shell instead of missing.
    expect(viewIdFromPath("/view/%")).toBe("%");
    expect(viewIdFromPath("/view/%E0%A4%A")).toBe("%E0%A4%A");
    // Same guarantee for a ticket segment: it does not throw. It answers null
    // rather than `%` because a malformed segment is not a ticket id either —
    // see the shareable-id case below.
    expect(ticketIdFromPath("/tickets/%/diff")).toBeNull();
  });

  it("reads the ticket id from a canonical ticket path", () => {
    const uuid = "41aac2d7-26a6-4f0b-988a-fc220d8dfa6c";
    expect(ticketIdFromPath(`/tickets/${uuid}/diff`)).toBe(uuid);
    expect(ticketIdFromPath(`/tickets/${uuid}`)).toBe(uuid);
    expect(ticketIdFromPath("/console")).toBeNull();
  });

  it("reports no ticket while a shareable id is still being resolved", () => {
    // App chrome reads the path directly, so answering with the ref would make
    // it fetch ticket-scoped endpoints under an id none of them accept.
    expect(ticketIdFromPath("/tickets/lor-mcp-gateway-142/diff")).toBeNull();
    expect(ticketIdFromPath("/tickets/456-one-dispatch-decision/diff")).toBeNull();
  });
});
