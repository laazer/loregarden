import { pageFromPath, pathForPage, ticketIdFromPath, ticketPath } from "../appNavigation";

describe("appNavigation", () => {
  it("maps known paths to app pages", () => {
    expect(pageFromPath("/")).toBe("dashboard");
    expect(pageFromPath("/studio")).toBe("studio");
    expect(pageFromPath("/studio/agents")).toBe("studio");
    expect(pageFromPath("/editor")).toBe("editor");
    expect(pageFromPath("/editor/loregarden/client/src/App.tsx")).toBe("editor");
    expect(pageFromPath("/queue")).toBe("queue");
    expect(pageFromPath("/queue/history")).toBe("queue");
    expect(pageFromPath("/tickets/abc-123")).toBe("dashboard");
  });

  it("falls back to dashboard for unknown paths", () => {
    expect(pageFromPath("/unknown")).toBe("dashboard");
  });

  it("returns canonical paths for each page", () => {
    expect(pathForPage("dashboard")).toBe("/");
    expect(pathForPage("studio")).toBe("/studio");
    expect(pathForPage("editor")).toBe("/editor");
    expect(pathForPage("queue")).toBe("/queue");
  });

  it("builds and parses ticket routes", () => {
    expect(ticketPath("abc-123")).toBe("/tickets/abc-123");
    expect(ticketPath("ticket/with/slash")).toBe("/tickets/ticket%2Fwith%2Fslash");
    expect(ticketIdFromPath("/tickets/abc-123")).toBe("abc-123");
    expect(ticketIdFromPath("/tickets/ticket%2Fwith%2Fslash")).toBe("ticket/with/slash");
    expect(ticketIdFromPath("/")).toBeNull();
  });
});
