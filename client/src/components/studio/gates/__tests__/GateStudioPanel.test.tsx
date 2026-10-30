import fs from "fs";
import path from "path";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";

import { ApiError } from "../../../../api/http";
import type { WorkspaceSummary } from "../../../../api/types";
import { GateStudioPanel } from "../GateStudioPanel";
import { agent, profile, stage, workflow, workspace } from "./gateStudioFixtures";

jest.mock("../../../../api/client", () => {
  const originalClient = jest.requireActual("../../../../api/client");
  return {
    ...originalClient,
    api: {
      ...originalClient.api,
      orchestrationProfile: jest.fn(),
      studioWorkflows: jest.fn(),
      studioAgents: jest.fn(),
      updateWorkspaceGates: jest.fn(),
      testWorkspaceGates: jest.fn(),
    },
  };
});

const { api } = require("../../../../api/client");

let lastPath = "";
function PathProbe() {
  lastPath = useLocation().pathname;
  return null;
}

function renderAt(
  path: string,
  {
    workspaces = [workspace()],
    active,
    error = null,
    retry = () => {},
  }: { workspaces?: WorkspaceSummary[]; active?: string; error?: unknown; retry?: () => void } = {},
) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route
            path="/studio/gates/*"
            element={
              <>
                <PathProbe />
                <GateStudioPanel
                  workspaces={workspaces}
                  workspacesLoading={false}
                  workspacesError={error}
                  onRetryWorkspaces={retry}
                  activeWorkspaceSlug={active}
                />
              </>
            }
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const gateStage = stage({
  key: "gate",
  name: "Gate",
  stage_type: "gate",
  exit_actions_enabled: true,
  exit_actions: [
    { key: "sign-off", label: "Sign off", requirement: { kind: "operator_judgment", decision_prompt: "Ship it?" } },
  ],
});

beforeEach(() => {
  jest.clearAllMocks();
  api.orchestrationProfile.mockResolvedValue(profile());
  api.studioWorkflows.mockResolvedValue([
    workflow({ slug: "other-flow", name: "Other Flow", published_template_slug: "other-flow" }),
    workflow({ stages: [stage(), gateStage] }),
  ]);
  api.studioAgents.mockResolvedValue([agent()]);
});

describe("GateStudioPanel", () => {
  it("opens a bare /studio/gates on the app-wide active workspace", async () => {
    renderAt("/studio/gates", { workspaces: [workspace(), workspace({ slug: "loregarden", name: "Loregarden" })], active: "loregarden" });

    await waitFor(() => expect(lastPath).toBe("/studio/gates/loregarden"));
    await waitFor(() => expect(api.orchestrationProfile).toHaveBeenCalledWith("loregarden"));
  });

  it("lists every workflow with the workspace's own pinned first and badged", async () => {
    renderAt("/studio/gates/blobert");

    const nav = await screen.findByRole("navigation", { name: "Workflows for blobert" });
    const links = await within(nav).findAllByRole("link");
    expect(links[0]).toHaveTextContent("Blobert TDD");
    expect(links[0]).toHaveTextContent("Runs here");
    expect(links[1]).toHaveTextContent("Other Flow");
    expect(links[1]).not.toHaveTextContent("Runs here");
  });

  it("shows each kind in its own section, with an empty state where a workflow has none", async () => {
    renderAt("/studio/gates/blobert/blobert-tdd");

    expect(await screen.findByRole("heading", { name: "Gate stages" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Gate" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Sign off" })).toBeInTheDocument();
    expect(await screen.findByText(/No agent this workflow runs has handoff checks/)).toBeInTheDocument();
  });

  it("renders the same detail from a deep link as from clicking through", async () => {
    const first = renderAt("/studio/gates/blobert/blobert-tdd/exit-action:gate:sign-off");
    const deep = (await screen.findByRole("heading", { level: 2, name: "Sign off" })).closest("article")?.textContent;
    first.unmount();

    renderAt("/studio/gates/blobert/blobert-tdd");
    fireEvent.click(await screen.findByRole("link", { name: "Sign off" }));
    const clicked = (await screen.findByRole("heading", { level: 2, name: "Sign off" })).closest("article")?.textContent;

    expect(clicked).toBe(deep);
    expect(clicked).toContain("Operator judgement — Ship it?");
    expect(lastPath).toBe("/studio/gates/blobert/blobert-tdd/exit-action:gate:sign-off");
  });

  it("links a read-only control to the studio that owns it", async () => {
    renderAt("/studio/gates/blobert/blobert-tdd/gate-stage:gate");
    expect(await screen.findByRole("link", { name: "Edit workflow blobert-tdd in Workflow Studio" })).toHaveAttribute(
      "href",
      "/studio/workflows/blobert-tdd",
    );
  });

  it("opens a transition command's detail with the editor beneath it", async () => {
    renderAt("/studio/gates/blobert/_/transition-command:0");
    expect(await screen.findByRole("heading", { level: 2, name: "ruff check {workspace_root}" })).toBeInTheDocument();
    expect(await screen.findByDisplayValue("ruff check {workspace_root}")).toBeInTheDocument();
  });

  it("asks before a link discards unsaved transition edits", async () => {
    renderAt("/studio/gates/blobert");
    fireEvent.change(await screen.findByDisplayValue("ruff check {workspace_root}"), { target: { value: "edited" } });

    fireEvent.click(screen.getByRole("link", { name: /Other Flow/ }));
    expect(screen.getByText(/unsaved gate changes for blobert/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Keep editing" })).toHaveFocus();
    fireEvent.click(screen.getByRole("button", { name: "Keep editing" }));
    expect(lastPath).toBe("/studio/gates/blobert");
    expect(screen.getByDisplayValue("edited")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("link", { name: /Other Flow/ }));
    fireEvent.click(screen.getByRole("button", { name: "Discard and leave" }));
    await waitFor(() => expect(lastPath).toBe("/studio/gates/blobert/other-flow"));
  });

  it("reloads the editor when switching workspaces", async () => {
    api.orchestrationProfile.mockImplementation((slug: string) =>
      Promise.resolve(slug === "blobert" ? profile() : profile({ slug: "loregarden", gates_commands: ["pytest"] })),
    );
    renderAt("/studio/gates/blobert", { workspaces: [workspace(), workspace({ slug: "loregarden", name: "Loregarden" })] });

    await screen.findByDisplayValue("ruff check {workspace_root}");
    fireEvent.click(screen.getByRole("link", { name: "Loregarden" }));
    expect(await screen.findByDisplayValue("pytest")).toBeInTheDocument();
  });

  it.each([
    ["/studio/gates/nope", /No workspace named nope/],
    ["/studio/gates/blobert/missing-flow", /No workflow named missing-flow for blobert/],
    ["/studio/gates/blobert/blobert-tdd/gate-stage:gone", /Nothing named gate-stage:gone in blobert-tdd/],
  ])("names what was not found at %s", async (path, message) => {
    renderAt(path);
    expect(await screen.findByText(message)).toBeInTheDocument();
  });

  it("says when the gate settings are not readable, naming the workspace", async () => {
    api.orchestrationProfile.mockRejectedValue(new ApiError(403, "forbidden"));
    renderAt("/studio/gates/blobert");
    expect(await screen.findByText(/don't have permission to read the gate settings for blobert/)).toBeInTheDocument();
  });

  // lg-milestone-that-1215 AC13. This panel is the surface that *writes* these
  // profile files, so a half-written or hand-edited one is its own output coming
  // back. Until now only a 403 was handled: every other failure fell through to
  // `collectWorkspaceControls(workspace, null)`, which renders a plausible,
  // complete-looking gate list for a file nobody could read. The question the
  // operator arrives with is "why are my gates not what I set?", and an empty
  // list is the one answer that stops them finding out.
  //
  // lg-milestone-that-1434 then fixed what that branch *said*: two sentences ran
  // together because the server's detail carries no terminator, and "Fix that
  // file and reload." was asserted for every non-403 failure, including the ones
  // that name no file.
  //
  // Every fixture below uses the wording this route really sends,
  // `{absolute path} could not be read as an orchestration profile: {reason}`
  // (server/loregarden/services/orchestration_profile.py:60-63). The original
  // report's `<path> will not parse: …` is a *different* endpoint's message
  // (handoff_tree_status.py); a fixture built from it would test a string this
  // route never sends (AC7).
  const UNREADABLE_PROFILE_PATH = "/repo/loregarden/agent_context/orchestration/default.yaml";
  const unreadableProfileError = () =>
    new ApiError(
      400,
      `${UNREADABLE_PROFILE_PATH} could not be read as an orchestration profile: while parsing a flow sequence, expected ',' or ']'`,
    );

  /** A 400 whose detail ends in no punctuation at all — the terminator oracle (AC6). */
  const UNTERMINATED_DETAIL = `${UNREADABLE_PROFILE_PATH} could not be read as an orchestration profile: the document is not valid YAML at line 4, column 12`;

  /**
   * A 2-error pydantic `ValidationError` as this route returns it: 7 lines, 545
   * characters, echoing the offending value and a pydantic docs URL (AC8).
   * Measured on this tree against the live profile code — a YAML syntax error
   * is 409 chars / 4 lines, this is the larger of the two realistic shapes.
   */
  const VALIDATION_DETAIL_LINES = [
    `${UNREADABLE_PROFILE_PATH} could not be read as an orchestration profile: 2 validation errors for OrchestrationProfileModel`,
    "gates.commands.0",
    "  Input should be a valid string [type=string_type, input_value=['ruff', 'check', '{workspace_root}'], input_type=list]",
    "    For further information visit https://errors.pydantic.dev/2.11/v/string_type",
    "max_stages_per_run",
    "  Input should be a valid integer, unable to parse string as an integer [type=int_parsing, input_value='as many as it takes', input_type=str]",
    "    For further information visit https://errors.pydantic.dev/2.11/v/int_parsing",
  ];

  const FIX_THE_FILE = "Fix that file, then try again.";
  const CHECK_THE_CONTROL_PLANE = "Check that the control plane is reachable, then try again.";
  const LEAD = "Gate settings for blobert could not be loaded.";

  /** A promise the test resolves, so an in-flight read can be asserted (AC5). */
  function deferred<T>(): { promise: Promise<T>; resolve: (value: T) => void } {
    let resolve!: (value: T) => void;
    const promise = new Promise<T>((res) => {
      resolve = res;
    });
    return { promise, resolve };
  }

  /**
   * The innermost element whose RAW text is exactly `text`.
   *
   * The default matcher collapses whitespace, which would make a 7-line detail
   * unfindable by its own content and would quietly accept a detail whose
   * newlines had been collapsed away. `normalizer` is identity here for the
   * same reason AC6 bans `toHaveTextContent`. Ancestors match in document
   * order before their descendants, so the last hit is the element itself.
   */
  function rawText(container: HTMLElement, text: string): HTMLElement {
    const matches = within(container).getAllByText(text, { normalizer: (value) => value });
    return matches[matches.length - 1];
  }

  /** The detail as the branch must render it: terminated, nothing else changed (AC1, AC2). */
  const terminated = (detail: string) => `${detail}.`;

  /** Every stylesheet the gates surfaces are painted from, comments stripped. */
  function gateStylesheets(): string {
    const dir = path.resolve(__dirname, "..", "..");
    const files = [path.join(dir, "WorkspaceGatesPanel.css"), path.join(dir, "gates", "GateStudio.css")];
    return files
      .filter((file) => fs.existsSync(file))
      .map((file) => fs.readFileSync(file, "utf8"))
      .join("\n")
      .replace(/\/\*[\s\S]*?\*\//g, "");
  }

  /**
   * Every declaration any rule makes about one class name.
   *
   * jsdom loads none of the app's CSS, so a `getComputedStyle` assertion here
   * could only pass against an inline style — which is the per-component
   * styling the theme tokens exist to remove. The stylesheet is the subject.
   */
  function declarationsFor(className: string, css = gateStylesheets()): string {
    const selector = new RegExp(`(^|[^\\w-])\\.${className}(?![\\w-])[^{}]*\\{([^}]*)\\}`, "g");
    let body = "";
    for (const match of css.matchAll(selector)) body += `${match[2]}\n`;
    return body;
  }

  /** The classes on `el` and every ancestor up to and including `stop`. */
  function classChain(el: Element, stop: Element): string[] {
    const names: string[] = [];
    for (let node: Element | null = el; node; node = node.parentElement) {
      names.push(...Array.from(node.classList));
      if (node === stop) break;
    }
    return names;
  }

  it("says the gate file is broken, and names the file, when the profile will not load", async () => {
    api.orchestrationProfile.mockRejectedValue(unreadableProfileError());
    renderAt("/studio/gates/blobert");

    const alert = await screen.findByRole("alert");
    // "loaded", not "read": the 400 detail owns the words "could not be read as
    // an orchestration profile", and the lead must not say them again two words
    // ahead of it.
    expect(alert.textContent).toContain(LEAD);
    expect(alert.textContent).toContain(UNREADABLE_PROFILE_PATH);
    expect(alert.textContent).toContain(FIX_THE_FILE);
  });

  it("says 'could not be read' once, in the server's detail and not also in the lead", async () => {
    api.orchestrationProfile.mockRejectedValue(unreadableProfileError());
    renderAt("/studio/gates/blobert");

    const alert = await screen.findByRole("alert");
    const stutters = (alert.textContent ?? "").match(/could not be read/g) ?? [];
    expect(stutters).toHaveLength(1);
  });

  // AC6. Both spellings are banned, and the assertion is on RAW textContent.
  // Separate block elements join in textContent with no separator at all, so a
  // test that banned only the spaced form would pass without the sentence ever
  // being terminated; and `toHaveTextContent` normalizes whitespace, so it
  // would pass over the very defect being fixed.
  it("terminates the server's detail before the sentence that follows it", async () => {
    api.orchestrationProfile.mockRejectedValue(new ApiError(400, UNTERMINATED_DETAIL));
    renderAt("/studio/gates/blobert");

    const alert = await screen.findByRole("alert");
    const text = alert.textContent ?? "";
    expect(text).toContain("at line 4, column 12.");
    expect(text).not.toContain("column 12 Fix");
    expect(text).not.toContain("column 12Fix");
  });

  // AC7(a).
  it("tells the operator to fix the file when the server named one", async () => {
    api.orchestrationProfile.mockRejectedValue(unreadableProfileError());
    renderAt("/studio/gates/blobert");

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain(FIX_THE_FILE);
    expect(alert.textContent).toContain(UNREADABLE_PROFILE_PATH);
    expect(alert.textContent).not.toContain(CHECK_THE_CONTROL_PLANE);
  });

  // AC7(b). An aborted or offline request is what a down control plane looks
  // like: not an ApiError, so `errorStatus` is null and no file was named.
  // Today this renders "… could not be read: Failed to fetch Fix that file and
  // reload." — no file named, and no file to fix.
  it("names no file when the request never reached the control plane", async () => {
    api.orchestrationProfile.mockRejectedValue(new Error("Failed to fetch"));
    renderAt("/studio/gates/blobert");

    const alert = await screen.findByRole("alert");
    const text = alert.textContent ?? "";
    expect(text).toContain("Failed to fetch.");
    expect(text).toContain(CHECK_THE_CONTROL_PLANE);
    // Neither the instruction being replaced nor any other way of telling them
    // to fix a file. The correct copy contains neither word, so this oracle can
    // be honest rather than banning a word the fix needs.
    expect(text).not.toMatch(/\bfix\b/i);
    expect(text).not.toMatch(/\bfiles?\b/i);
  });

  // AC2: the HTTP status decides the instruction and nothing else. This is the
  // mutant that a substring or regex test of the message text passes and the
  // status test fails — a 500 whose body happens to carry a path and the
  // server's own "could not be read as an orchestration profile" wording. A
  // text-sniffing branch sends the operator to edit a file over a failure that
  // named none.
  it("chooses the instruction by status, not by what the message happens to contain", async () => {
    api.orchestrationProfile.mockRejectedValue(
      new ApiError(500, `${UNREADABLE_PROFILE_PATH} could not be read as an orchestration profile: internal error`),
    );
    renderAt("/studio/gates/blobert");

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain(CHECK_THE_CONTROL_PLANE);
    expect(alert.textContent).not.toContain(FIX_THE_FILE);
  });

  // AC2, adversarial. Every other fixture on this branch routes to `blobert`,
  // so a lead with the slug baked in as a literal passes all of them. The
  // operator's question is about *this* workspace: a lead naming the wrong one
  // sends them to audit a file that was never read.
  it("names the workspace it actually failed to read, not the one the fixtures use", async () => {
    api.orchestrationProfile.mockRejectedValue(unreadableProfileError());
    renderAt("/studio/gates/loregarden", {
      workspaces: [workspace(), workspace({ slug: "loregarden", name: "Loregarden" })],
    });

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Gate settings for loregarden could not be loaded.");
    expect(alert.textContent).not.toContain("blobert");
  });

  // AC2 + AC9, adversarial. The branch holds its last failure across its own
  // re-read (AC5/AC10), and the held failure must be keyed to the workspace it
  // describes. The profile query key is ["orchestration-profile", slug], so
  // switching workspaces starts a brand-new observer — isError false, isFetching
  // true — and a clear guarded on `!isFetching` never runs. An unkeyed ref then
  // renders the previous workspace's file under the new workspace's name: the
  // operator is sent to audit a file that belongs to a workspace they left.
  it("drops the held failure when the operator switches workspaces", async () => {
    api.orchestrationProfile.mockImplementation((slug: string) =>
      slug === "blobert" ? Promise.reject(new ApiError(400, UNTERMINATED_DETAIL)) : new Promise(() => {}),
    );
    renderAt("/studio/gates/blobert", {
      workspaces: [workspace(), workspace({ slug: "loregarden", name: "Loregarden" })],
    });

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain(UNREADABLE_PROFILE_PATH);

    fireEvent.click(screen.getByRole("link", { name: "Loregarden" }));
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
    const page = document.body.textContent ?? "";
    expect(page).not.toContain(UNREADABLE_PROFILE_PATH);
    expect(page).not.toContain("could not be loaded");
  });

  // The 403 branch reads the same held failure, so the same switch renders a
  // false permission claim about a workspace nobody has tried to read yet.
  it("drops a held permission failure when the operator switches workspaces", async () => {
    api.orchestrationProfile.mockImplementation((slug: string) =>
      slug === "blobert" ? Promise.reject(new ApiError(403, "forbidden")) : new Promise(() => {}),
    );
    renderAt("/studio/gates/blobert", {
      workspaces: [workspace(), workspace({ slug: "loregarden", name: "Loregarden" })],
    });

    await screen.findByText(/don't have permission to read the gate settings for blobert/);

    fireEvent.click(screen.getByRole("link", { name: "Loregarden" }));
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
    expect(document.body.textContent ?? "").not.toContain("don't have permission");
  });

  // AC1 + AC2, adversarial. `endSentence` is pinned as a unit, but nothing so
  // far forces the branch to go *through* it: an inline `${describeError(…)}.`
  // template terminates the unterminated fixture and passes every oracle
  // above, while rendering `…expected ',' or ']'..` for the detail shapes that
  // already end in punctuation. A 400 whose detail ends in a period is the
  // ordinary case, not a corner: `str(ValidationError)` and several yaml
  // reasons end in one.
  it("does not double the terminator when the server's detail already ends in one", async () => {
    const terminatedDetail = `${UNREADABLE_PROFILE_PATH} could not be read as an orchestration profile: the file is empty.`;
    api.orchestrationProfile.mockRejectedValue(new ApiError(400, terminatedDetail));
    renderAt("/studio/gates/blobert");

    const alert = await screen.findByRole("alert");
    const text = alert.textContent ?? "";
    expect(text).toContain("the file is empty.");
    expect(text).not.toContain("empty..");
    expect(text).not.toMatch(/\.\s*\./);
  });

  // AC1 + AC2, adversarial. The same mutant seen from the other side: a detail
  // whose last character is a terminator the branch must leave alone. A
  // pydantic detail really does end in `]` (the echoed input_value) or a `/`
  // -free docs URL, and a 2-error one ends in a colon when the reason is
  // truncated — so this is the set AC1 enumerates, exercised through the UI
  // rather than only through the helper.
  it.each([
    ["a colon", "2 validation errors for OrchestrationProfileModel:"],
    ["an ellipsis", "the reader gave up\u2026"],
    ["a question mark", "is this file yours?"],
  ])("leaves a detail ending in %s exactly as the server sent it", async (_label, reason) => {
    const detail = `${UNREADABLE_PROFILE_PATH} could not be read as an orchestration profile: ${reason}`;
    api.orchestrationProfile.mockRejectedValue(new ApiError(400, detail));
    renderAt("/studio/gates/blobert");

    const alert = await screen.findByRole("alert");
    const text = alert.textContent ?? "";
    expect(text).toContain(detail);
    expect(text).not.toContain(`${detail}.`);
    expect(text).toContain(FIX_THE_FILE);
  });

  // AC2, adversarial. The fallback is part of the pinned copy and no oracle
  // above reaches it: every fixture throws an Error or an ApiError carrying a
  // message. react-query rejects with whatever the promise rejected with, and
  // a thrown non-Error lands on `describeError`'s `fallback` argument — so a
  // branch that dropped the argument renders `describeError`'s own default,
  // "Unexpected error", and passes everything else.
  it("uses this branch's own fallback when the failure carries no message", async () => {
    api.orchestrationProfile.mockRejectedValue({ nope: true });
    renderAt("/studio/gates/blobert");

    const alert = await screen.findByRole("alert");
    const text = alert.textContent ?? "";
    expect(text).toContain("the request failed.");
    expect(text).not.toContain("Unexpected error");
    // Not an ApiError, so no status and no file was named.
    expect(text).toContain(CHECK_THE_CONTROL_PLANE);
  });

  // AC1 + AC3, adversarial. `endSentence` returns "" for input that is empty
  // after the right-trim, which is the one input that makes the detail block
  // render nothing at all. The alert must still be two whole sentences rather
  // than a lead followed by a stray period or a doubled separator.
  it("renders no stray punctuation when the server's detail is only whitespace", async () => {
    api.orchestrationProfile.mockRejectedValue(new ApiError(400, "   \n  "));
    renderAt("/studio/gates/blobert");

    const alert = await screen.findByRole("alert");
    const text = alert.textContent ?? "";
    expect(text).toContain(LEAD);
    expect(text).toContain(FIX_THE_FILE);
    expect(text).not.toMatch(/\.\s*\./);
  });

  // AC8.
  it("renders every line of a multi-line parse failure", async () => {
    api.orchestrationProfile.mockRejectedValue(new ApiError(400, VALIDATION_DETAIL_LINES.join("\n")));
    renderAt("/studio/gates/blobert");

    const alert = await screen.findByRole("alert");
    const text = alert.textContent ?? "";
    for (const line of VALIDATION_DETAIL_LINES) {
      expect({ line, rendered: text.includes(line) }).toEqual({ line, rendered: true });
    }
  });

  // AC3. Three separate blocks, found by their copy rather than by a class name
  // or a test id, so the branch is free to choose its own markup.
  it("renders lead, detail and instruction as three separate elements", async () => {
    api.orchestrationProfile.mockRejectedValue(new ApiError(400, UNTERMINATED_DETAIL));
    renderAt("/studio/gates/blobert");

    const alert = await screen.findByRole("alert");
    const lead = rawText(alert, LEAD);
    const detail = rawText(alert, terminated(UNTERMINATED_DETAIL));
    const instruction = rawText(alert, FIX_THE_FILE);

    expect(new Set([lead, detail, instruction]).size).toBe(3);
    for (const [a, b] of [
      [lead, detail],
      [detail, instruction],
      [lead, instruction],
    ]) {
      expect(a.contains(b) || b.contains(a)).toBe(false);
    }
  });

  // AC2 + AC3, adversarial. The three-elements oracle above pins distinctness
  // and non-nesting but says nothing about order, so a branch rendering the
  // instruction first passes it while telling the operator what to do before
  // saying what happened. Document order is what a screen reader reads out and
  // what `textContent` concatenates.
  it("reads lead, then detail, then instruction, in that order", async () => {
    api.orchestrationProfile.mockRejectedValue(new ApiError(400, UNTERMINATED_DETAIL));
    renderAt("/studio/gates/blobert");

    const alert = await screen.findByRole("alert");
    const text = alert.textContent ?? "";
    const at = (needle: string) => text.indexOf(needle);

    expect(at(LEAD)).toBeGreaterThanOrEqual(0);
    expect(at(UNREADABLE_PROFILE_PATH)).toBeGreaterThan(at(LEAD));
    expect(at(FIX_THE_FILE)).toBeGreaterThan(at("at line 4, column 12."));
  });

  // AC3. A 7-line detail must survive at its real width: the whitespace the
  // server sent is the structure of the message, and an absolute path or a
  // pydantic URL has no break opportunity in it.
  it("styles the detail to keep its own line breaks and to wrap a long path", async () => {
    api.orchestrationProfile.mockRejectedValue(new ApiError(400, VALIDATION_DETAIL_LINES.join("\n")));
    renderAt("/studio/gates/blobert");

    const alert = await screen.findByRole("alert");
    const detail = rawText(alert, terminated(VALIDATION_DETAIL_LINES.join("\n")));
    const declarations = Array.from(detail.classList)
      .map((name) => declarationsFor(name))
      .join("\n");

    expect(declarations).toMatch(/white-space:\s*pre-wrap/);
    expect(declarations).toMatch(/overflow-wrap:\s*anywhere/);
    expect(declarations).toMatch(/max-width:\s*100%/);
  });

  // AC3. `.gate-load-error` is a one-line flex row today
  // (WorkspaceGatesPanel.css:555-571: flex-wrap, align-items center,
  // justify-content space-between). Laid out as a flex item beside the lead and
  // the button, a 7-line detail is unreadable — so either the alert itself
  // gains a column modifier, or the three blocks sit inside a container of its
  // own. Both are accepted; a bare flex row is not.
  it("stacks the alert in a column rather than laying the detail out beside the lead", async () => {
    api.orchestrationProfile.mockRejectedValue(new ApiError(400, VALIDATION_DETAIL_LINES.join("\n")));
    renderAt("/studio/gates/blobert");

    const alert = await screen.findByRole("alert");
    const detail = rawText(alert, terminated(VALIDATION_DETAIL_LINES.join("\n")));
    const stacking = classChain(detail, alert)
      .map((name) => declarationsFor(name))
      .join("\n");

    expect(stacking).toMatch(/flex-direction:\s*column|display:\s*(block|grid)/);
    expect(stacking).not.toMatch(/flex-direction:\s*row/);
  });

  // AC3. `ts-theme` and `ts-motion` are diff-scoped and will see these lines.
  it("paints the new error styling from tokens, with no colour literals and no motion", async () => {
    api.orchestrationProfile.mockRejectedValue(new ApiError(400, UNTERMINATED_DETAIL));
    renderAt("/studio/gates/blobert");

    const alert = await screen.findByRole("alert");
    const detail = rawText(alert, terminated(UNTERMINATED_DETAIL));
    const declarations = classChain(detail, alert)
      .map((name) => declarationsFor(name))
      .join("\n");

    expect(declarations).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
    expect(declarations).not.toMatch(/\b(rgba?|hsla?)\(/);
    expect(declarations).not.toMatch(/\btransition\b|\banimation\b/);
  });

  // AC4. The alert carries a control, not just prose — matching the
  // workspaces-failed alert in the same file. No `window.location.reload()`,
  // and nothing telling the operator to reload the browser.
  it("offers a retry inside the alert that re-reads the profile", async () => {
    api.orchestrationProfile.mockRejectedValueOnce(unreadableProfileError()).mockResolvedValue(profile());
    renderAt("/studio/gates/blobert");

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).not.toMatch(/reload/i);
    const before = api.orchestrationProfile.mock.calls.length;

    fireEvent.click(within(alert).getByRole("button", { name: "Try again" }));

    expect(await screen.findByDisplayValue("ruff check {workspace_root}")).toBeInTheDocument();
    // A delta, not a total: once the profile loads, the gates editor beneath it
    // reads the profile for itself, so the absolute count is not this test's
    // business. What is, is that the click caused another read.
    expect(api.orchestrationProfile.mock.calls.length).toBeGreaterThan(before);
  });

  // AC5, and the half of AC10 that only shows up here. The refetch is held open
  // by a promise this test resolves, so the in-flight state is asserted while it
  // really is in flight rather than raced against an immediate rejection.
  //
  // Measured on react-query 5 (probe in this suite's review): `refetch()` on a
  // query that failed with no data resets it to `pending/fetching` and clears
  // `error`, so a branch guarded on `profile.isError` alone UNMOUNTS the moment
  // the retry starts — and this panel has no loading branch, so the operator
  // would be dropped back onto `collectWorkspaceControls(workspace, undefined)`:
  // the plausible, complete-looking empty gate list that
  // lg-milestone-that-1215 removed. The alert must therefore survive its own
  // retry. That is what makes the in-flight label reachable at all.
  it("keeps the alert up, disabled and saying so, while the re-read is in flight", async () => {
    const held = deferred<ReturnType<typeof profile>>();
    api.orchestrationProfile.mockRejectedValueOnce(unreadableProfileError()).mockReturnValueOnce(held.promise);
    const { container } = renderAt("/studio/gates/blobert");

    fireEvent.click(await screen.findByRole("button", { name: "Try again" }));

    const trying = await screen.findByRole("button", { name: "Trying again…" });
    expect(trying).toBeDisabled();
    expect(screen.getByRole("alert").textContent).toContain(UNREADABLE_PROFILE_PATH);
    expect(container.querySelector(".gate-studio-controls")).toBeNull();

    // A second click cannot fire a second read.
    const during = api.orchestrationProfile.mock.calls.length;
    fireEvent.click(trying);
    expect(api.orchestrationProfile.mock.calls.length).toBe(during);

    held.resolve(profile());
    expect(await screen.findByDisplayValue("ruff check {workspace_root}")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  // AC10. No skeleton and no spinner: parsing the real 8542-byte profile takes
  // a median 0.0ms (max 0.5ms over 20 runs), far under the ~200ms a skeleton is
  // for. Focus is not moved, because the alert appears on load rather than in
  // response to a keystroke — `role="alert"` already announces it.
  it("announces the failure without stealing focus, and adds no spinner", async () => {
    api.orchestrationProfile.mockRejectedValue(unreadableProfileError());
    const { container } = renderAt("/studio/gates/blobert");

    const alert = await screen.findByRole("alert");
    expect(document.activeElement).toBe(document.body);
    expect(container.querySelector("[role='progressbar'], .skeleton, .spinner")).toBeNull();
    expect(alert.textContent).not.toMatch(/loading/i);

    const retry = within(alert).getByRole("button", { name: "Try again" });
    expect(retry.tagName).toBe("BUTTON");
    expect(retry).not.toHaveAttribute("tabindex", "-1");
  });

  it("renders no gate list at all when the profile will not load", async () => {
    api.orchestrationProfile.mockRejectedValue(unreadableProfileError());
    const { container } = renderAt("/studio/gates/blobert/blobert-tdd");

    await screen.findByRole("alert");
    expect(container.querySelector(".gate-studio-controls")).toBeNull();
    // The error state and the legitimate empty state must not read alike: the
    // empty copy here would say this workflow has no controls of a kind, which
    // is a different and perfectly fine answer.
    expect(screen.queryByText(/No agent this workflow runs has handoff checks/)).not.toBeInTheDocument();
  });

  it("keeps the 403 copy, and keeps it ahead of the general failure branch", async () => {
    api.orchestrationProfile.mockRejectedValue(new ApiError(403, UNREADABLE_PROFILE_PATH));
    renderAt("/studio/gates/blobert");

    expect(await screen.findByText(/don't have permission to read the gate settings for blobert/)).toBeInTheDocument();
    // A general `isError` branch placed *before* the 403 one would swallow it,
    // and the operator would be told to go and fix a file they are not allowed
    // to read. Anchored on the general branch's *lead*: a probe for "could not
    // be read" would match the 400 detail this branch still renders verbatim,
    // and would assert nothing (AC11).
    expect(screen.queryByText(/Gate settings for blobert could not be loaded/)).not.toBeInTheDocument();
    expect(screen.queryByText(FIX_THE_FILE)).not.toBeInTheDocument();
  });

  it("says what failed when the workflows cannot load, and retries", async () => {
    api.studioWorkflows.mockRejectedValueOnce(new Error("boom"));
    renderAt("/studio/gates/blobert");
    expect(await screen.findByText(/Couldn't load the workflows for blobert.*boom/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("navigation", { name: "Workflows for blobert" })).toBeInTheDocument();
  });

  it("says the workspace list failed rather than that there are none, and retries", () => {
    const retry = jest.fn();
    renderAt("/studio/gates", { workspaces: [], error: new Error("502 Bad Gateway"), retry });

    expect(screen.getByText(/Couldn't load the workspaces.*502/)).toBeInTheDocument();
    expect(screen.queryByText(/No workspaces yet/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(retry).toHaveBeenCalled();
  });

  it("says what to do when there are no workspaces", () => {
    renderAt("/studio/gates", { workspaces: [] });
    expect(screen.getAllByText(/No workspaces yet/).length).toBeGreaterThan(0);
  });
});
