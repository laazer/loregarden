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

  it("strips loregarden MCP prefix for display", () => {
    const details = buildPermissionDetails(
      "mcp__loregarden__loregarden_get_ticket",
      JSON.stringify({ ticket_id: "abc" }),
    );
    expect(details.subtitle).toBe("loregarden_get_ticket");
    expect(details.toolLabel).toBe("MCP tool");
  });
});
