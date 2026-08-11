import {
  BUILTIN_COMMANDS,
  activeTrigger,
  applyCompletion,
  matchCommands,
  parseDraft,
  resolveCommand,
  skillCommands,
} from "../composerCommands";

describe("activeTrigger", () => {
  it("opens the command menu on a leading slash", () => {
    expect(activeTrigger("/qu", 3)).toEqual({
      kind: "slash",
      query: "qu",
      start: 0,
      end: 3,
    });
  });

  it("leaves a slash mid-message alone — that is a path, not a command", () => {
    expect(activeTrigger("look at src/lib", 15)).toBeNull();
  });

  it("closes the command menu once the command token is finished", () => {
    // The caret has moved past the token into the message body.
    expect(activeTrigger("/queue ship it", 12)).toBeNull();
  });

  it("opens the reference menu on @ at a word boundary", () => {
    expect(activeTrigger("look at @App", 12)).toEqual({
      kind: "mention",
      query: "App",
      start: 8,
      end: 12,
    });
  });

  it("ignores an @ inside a word, so emails are not references", () => {
    expect(activeTrigger("mail me@example", 15)).toBeNull();
  });

  it("ends the reference at the first space", () => {
    expect(activeTrigger("@src/app.ts and then", 20)).toBeNull();
  });

  it("reads the query up to the caret, not to the end of the token", () => {
    expect(activeTrigger("@components", 4)?.query).toBe("com");
  });
});

describe("matchCommands", () => {
  const commands = [...BUILTIN_COMMANDS, ...skillCommands(["review", "queue-audit"])];

  it("returns everything for an empty query", () => {
    expect(matchCommands(commands, "")).toHaveLength(commands.length);
  });

  it("finds a command through its alias", () => {
    expect(matchCommands(commands, "q")[0].name).toBe("queue");
  });

  it("ranks an exact name above a command that merely contains it", () => {
    expect(matchCommands(commands, "queue").map((c) => c.name)).toEqual([
      "queue",
      "queue-audit",
    ]);
  });

  it("offers skills alongside the built-ins", () => {
    expect(matchCommands(commands, "rev").map((c) => c.name)).toEqual(["review"]);
  });
});

describe("applyCompletion", () => {
  it("replaces the command token and keeps the rest of the draft", () => {
    const trigger = activeTrigger("/qu", 3)!;
    expect(applyCompletion("/qu", trigger, "/queue", " ")).toEqual({
      value: "/queue ",
      caret: 7,
    });
  });

  it("replaces only the reference, leaving the surrounding message", () => {
    const value = "look at @App and fix it";
    const trigger = activeTrigger(value, 12)!;
    expect(applyCompletion(value, trigger, "@client/AppBar.tsx", " ")).toEqual({
      value: "look at @client/AppBar.tsx and fix it",
      caret: 26,
    });
  });

  it("leaves a directory reference open so the next keystroke narrows inside it", () => {
    const trigger = activeTrigger("@client", 7)!;
    expect(applyCompletion("@client", trigger, "@client/src", "/").value).toBe("@client/src/");
  });
});

describe("parseDraft", () => {
  it("splits the leading command off the body", () => {
    expect(parseDraft("/queue ship the queue fix")).toEqual({
      command: "queue",
      body: "ship the queue fix",
    });
  });

  it("keeps a multi-line body intact", () => {
    expect(parseDraft("/note first\nsecond").body).toBe("first\nsecond");
  });

  it("reports no command for ordinary text", () => {
    expect(parseDraft("ship the queue fix")).toEqual({
      command: "",
      body: "ship the queue fix",
    });
  });

  it("reports a bare command with an empty body", () => {
    expect(parseDraft("/note")).toEqual({ command: "note", body: "" });
  });
});

describe("resolveCommand", () => {
  const commands = [...BUILTIN_COMMANDS, ...skillCommands(["review"])];

  it("resolves an alias to its command", () => {
    expect(resolveCommand(commands, "q")?.name).toBe("queue");
  });

  it("resolves /run to orchestrate", () => {
    expect(resolveCommand(commands, "run")?.name).toBe("orchestrate");
  });

  it("resolves /open to ticket", () => {
    expect(resolveCommand(commands, "open")?.name).toBe("ticket");
  });

  it("resolves a skill by name", () => {
    expect(resolveCommand(commands, "review")?.kind).toBe("skill");
  });

  it("refuses an unknown token, so it is sent as ordinary text", () => {
    expect(resolveCommand(commands, "nope")).toBeNull();
  });
});

describe("BUILTIN_COMMANDS", () => {
  it("ships the b12 vocabulary", () => {
    expect(BUILTIN_COMMANDS.map((command) => command.name)).toEqual([
      "help",
      "queue",
      "note",
      "new",
      "fork",
      "clear",
      "orchestrate",
      "stop",
      "approve",
      "reject",
      "btw",
      "ticket",
      "create",
    ]);
  });
});
