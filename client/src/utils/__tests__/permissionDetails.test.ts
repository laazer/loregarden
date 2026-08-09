import { buildPermissionDetails } from "../permissionDetails";

describe("buildPermissionDetails", () => {
  it("formats bash commands legibly", () => {
    const details = buildPermissionDetails("Bash", JSON.stringify({ command: "npm test" }));
    expect(details.toolLabel).toBe("Shell command");
    expect(details.primary?.label).toBe("Command");
    expect(details.primary?.value).toBe("npm test");
  });

  it("formats write requests with file and content preview", () => {
    const details = buildPermissionDetails(
      "Write",
      JSON.stringify({ file_path: "src/app.py", content: "print('hi')" }),
    );
    expect(details.toolLabel).toBe("Write file");
    expect(details.primary?.value).toBe("src/app.py");
    expect(details.fields.some((field) => field.label === "Content")).toBe(true);
  });

  it("marks a plan approval's body as markdown rather than preformatted text", () => {
    const plan = "## Steps\n\n- Add the reader\n- Wire the UI\n";
    const details = buildPermissionDetails("ExitPlanMode", JSON.stringify({ plan }));

    expect(details.toolLabel).toBe("Implementation plan");
    expect(details.primary?.label).toBe("Plan");
    expect(details.primary?.markdown).toBe(true);
    expect(details.primary?.mono).toBe(false);
    expect(details.primary?.value).toBe(plan.trim());
  });

  it("keeps a long plan intact past the generic preview limit", () => {
    const plan = `# Plan\n${"- step\n".repeat(1000)}`;
    const details = buildPermissionDetails("exit_plan_mode", JSON.stringify({ plan }));

    expect(details.primary?.value).not.toContain("more characters");
  });

  it("renders a plan as markdown even from a tool it has no branch for", () => {
    const details = buildPermissionDetails("SomeOtherPlanTool", JSON.stringify({ plan: "# Plan" }));
    const planField = details.fields.find((field) => field.label === "plan");

    expect(planField?.markdown).toBe(true);
  });

  it("strips loregarden MCP prefix for display", () => {
    const details = buildPermissionDetails(
      "mcp__loregarden__loregarden_get_ticket",
      JSON.stringify({ ticket_id: "abc" }),
    );
    expect(details.subtitle).toBe("loregarden_get_ticket");
    expect(details.toolLabel).toBe("MCP tool");
  });
});
