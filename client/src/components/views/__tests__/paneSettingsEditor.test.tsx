/**
 * 554 — "a pane can be configured: a settings editor generated from the
 * primitive's own fields."
 *
 * ## What is real here, and why that is the whole point
 *
 * Almost every test below drives the **real grid page** through the shared view
 * harness: the real `PaneHeader`, the real `ContainerPane`, the real
 * `useViewLayoutEdit`, and a fake server that stores what was PATCHed. Eight
 * tickets in this milestone shipped tests that asserted a fixture the test had
 * written itself and passed while the shipped surface was broken — 556's own
 * accessibility test checked a synthetic `<button>` and missed six real controls
 * with no `title`. So a claim about a control is made by finding that control on
 * the rendered page, and a claim about what is stored is read out of the PATCH.
 *
 * Two tests do mount `PaneSettingsEditor` with a primitive the registry does not
 * hold, and that is deliberate rather than a shortcut: AC3 says *adding a
 * primitive must require no change to the editor*, and the only way to exercise
 * that claim is to hand the editor a schema it has never seen. The component
 * under test is the shipped one; only its input is new. No registered primitive
 * declares a `number` field today, so the number path has no other way to be
 * reached at all — and it is the path where a wrong answer is silent (444: a
 * non-finite number is coerced to `null` server-side without complaint).
 *
 * ## What jsdom is not asked
 *
 * Nothing about how the panel *looks*. There is no layout engine here — every
 * element measures 0px and no stylesheet resolves — so spacing, overlap and
 * whether the panel fits a short pane are unmeasurable in this file. They were
 * checked in a browser instead.
 */

import { QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { viewsKeys, type ViewSummary } from "../../../lib/viewsApi";
import { SidebarWorkspaceProvider } from "../../../state/SidebarWorkspaceContext";
import {
  SLUG,
  containersOf,
  control,
  gridNode,
  installGridHarness,
  lastLayout,
  leafLayout,
  mockUpdateView,
  pairLayout,
  renderGrid,
  settle,
  storePatch,
  testClient,
  viewOf,
  type Json,
} from "../../../test/gridHarness";
import { assertServerAcceptableLayout } from "../../../test/viewLayoutContract";
import { NOT_A_NUMBER, initialDraft, readDraft } from "../paneSettingsDraft";
import { PANE_SETTINGS_LABEL } from "../paneSettingsLabel";
import { PaneSettingsEditor } from "../PaneSettingsEditor";
import {
  CONTAINER_PRIMITIVES,
  composeSettings,
  containerWithSettings,
  newContainerFor,
} from "../primitives/registry";
import type { RegisteredPrimitive, SettingsField } from "../primitives/types";

jest.mock("../../../lib/viewsApi", () => ({
  ...jest.requireActual("../../../lib/viewsApi"),
  fetchView: jest.fn(),
  updateView: jest.fn(),
}));

/**
 * A real terminal opens a websocket and an xterm instance. Mounting it is not
 * what any assertion here is about — that a *configured* terminal mounts at all
 * is, so the stand-in is observable and nothing else about it is claimed.
 */
jest.mock("../../TerminalPanel", () => ({
  __esModule: true,
  TerminalPanel: ({ workspaceSlug }: { workspaceSlug: string }) => (
    <div data-testid="live-shell" data-workspace={workspaceSlug} />
  ),
}));

jest.mock("../../RunLedgerPanel", () => ({
  __esModule: true,
  RunLedgerPanel: ({ ticketId }: { ticketId: string }) => (
    <div data-testid="run-ledger" data-ticket={ticketId} />
  ),
}));

installGridHarness();

// ---------------------------------------------------------------------------
// Fixtures: containers exactly as `newContainerFor` would have stored them, so
// no test asserts against a shape the app cannot actually produce.
// ---------------------------------------------------------------------------

const containerOf = (primitiveId: string): Json =>
  newContainerFor(primitiveId) as unknown as Json;

const settingsOf = (layout: Json, containerId: string): Json =>
  containersOf(layout)[containerId].settings as Json;

/** Open the settings panel on the grid's single seeded pane. */
async function openSettings(root: HTMLElement, user: ReturnType<typeof userEvent.setup>) {
  await user.click(control(root, "n-seed", "pane-settings"));
}

describe("AC1/AC3 — the editor is reached from the pane and generated from the schema", () => {
  it("puts the settings control in the header of a pane that has a primitive", async () => {
    const { container } = renderGrid(leafLayout(containerOf("terminal")));
    await screen.findByTestId("view-host");

    const button = control(container, "n-seed", "pane-settings");
    // 434's rule, restated for the control this ticket adds: the glyph is
    // aria-hidden, so both carriers of the name have to be on the button.
    expect(button).toHaveAttribute("aria-label", PANE_SETTINGS_LABEL);
    expect(button).toHaveAttribute("title", PANE_SETTINGS_LABEL);
  });

  it("puts it in the contents zone, before the picker and before the hairline", async () => {
    // 556's decision, which 554 was handed and must not relitigate: a control's
    // zone follows what it acts on, and a settings editor generated from the
    // primitive's own fields edits the *contents*. So it sits between the name
    // and the picker, on the near side of `.pane-header-zone-rule`.
    //
    // Asserted on the real grid header rather than on a fixture, and asserted at
    // all because 556's own zone test could not see this control: it renders a
    // `PaneHeader` with a synthetic button and an unconfigured container, which
    // has no settings control to place. A settings button moved into the
    // arrangement zone passed every other test in this file.
    const { container } = renderGrid(leafLayout(containerOf("terminal")));
    await screen.findByTestId("view-host");

    const row = gridNode(container, "n-seed").querySelector(".pane-header") as HTMLElement;
    const children = Array.from(row.children);
    const indexOf = (predicate: (el: Element) => boolean) => children.findIndex(predicate);

    const title = indexOf((el) => el.classList.contains("pane-header-title"));
    const settings = indexOf((el) => el.getAttribute("aria-label") === PANE_SETTINGS_LABEL);
    const picker = indexOf((el) => el.getAttribute("aria-label") === "Change contents");
    const rule = indexOf((el) => el.classList.contains("pane-header-zone-rule"));
    const close = indexOf((el) => el.getAttribute("aria-label") === "Close this pane");

    expect({ title, ordered: title < settings && settings < picker && picker < rule }).toEqual({
      title: 0,
      ordered: true,
    });
    // And the arrangement's controls are still on the far side of the rule, so
    // this ticket did not move the hairline instead of respecting it.
    expect(close).toBeGreaterThan(rule);
  });

  it("offers no settings control on a pane that has not picked a primitive", async () => {
    // A form with no fields in it is a control that says a pane is configurable
    // when it is not — the same lie 554 removed from the empty-pane copy.
    const { container } = renderGrid(leafLayout());
    await screen.findByTestId("view-host");

    expect(
      gridNode(container, "n-seed").querySelector('[data-grid-action="pane-settings"]'),
    ).toBeNull();
  });

  it("renders one input per declared field, typed by that field's kind", async () => {
    // The run ledger is the registered primitive with two fields of different
    // kinds — a string and a boolean — so it is the one that can show the
    // dispatch happening rather than merely being claimed.
    const user = userEvent.setup();
    const { container } = renderGrid(leafLayout(containerOf("run_ledger")));
    await screen.findByTestId("view-host");
    await openSettings(container, user);

    const ticket = screen.getByLabelText("Ticket");
    expect(ticket).toHaveAttribute("type", "text");
    const live = screen.getByLabelText("Poll while the ticket is running");
    expect(live).toHaveAttribute("type", "checkbox");
    // The schema's help text reaches the operator, and is tied to its own input
    // rather than merely sitting near it.
    expect(ticket).toHaveAttribute(
      "aria-describedby",
      expect.stringMatching(new RegExp(`^${ticket.id}-help$`)),
    );
    expect(screen.getByText("The ticket whose stage visits this pane lists.")).toBeInTheDocument();
  });

  it("opens on the values already stored, not on the schema's defaults", async () => {
    // The gap that made this whole suite look better than it was: every other
    // test opens the editor on a `newContainerFor` container, whose stored
    // values ARE the schema defaults — so "seeded from stored" and "seeded from
    // default" are indistinguishable in all of them, and a `draftValue` that
    // ignored the container entirely passed 247 tests.
    //
    // It is not a cosmetic bug. `containerWithSettings` composes the container
    // whole, so a form that opened on defaults would blank every field the user
    // did not retype the moment they saved.
    const user = userEvent.setup();
    const configured: Json = {
      kind: "panel",
      settings: { primitive_id: "run_ledger", ticket_id: "t-1", live: true },
    };
    const { container } = renderGrid(leafLayout(configured));
    await screen.findByTestId("view-host");
    await openSettings(container, user);

    expect(screen.getByLabelText("Ticket")).toHaveValue("t-1");
    // The checkbox's *displayed* state, which nothing else asserts: a
    // permanently-unchecked box still reports `true` from its first click, so a
    // write-only toggle satisfies every save test while being impossible to
    // switch off.
    expect(screen.getByLabelText("Poll while the ticket is running")).toBeChecked();
  });

  it("keeps the fields the user did not touch when it saves", async () => {
    // The consequence of replace-whole, stated as a test rather than trusted:
    // editing one field must not blank the others.
    const user = userEvent.setup();
    const configured: Json = {
      kind: "panel",
      settings: { primitive_id: "run_ledger", ticket_id: "t-1", live: true },
    };
    const { container } = renderGrid(leafLayout(configured));
    await screen.findByTestId("view-host");
    await openSettings(container, user);

    await user.click(screen.getByLabelText("Poll while the ticket is running"));
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(mockUpdateView).toHaveBeenCalledTimes(1));
    expect(settingsOf(lastLayout(), "c-seed")).toEqual({
      primitive_id: "run_ledger",
      ticket_id: "t-1",
      live: false,
    });
  });

  it("writes the edited values into the view record", async () => {
    const user = userEvent.setup();
    const { container } = renderGrid(leafLayout(containerOf("run_ledger")));
    await screen.findByTestId("view-host");
    await openSettings(container, user);

    await user.type(screen.getByLabelText("Ticket"), "lg-flex-views-554");
    await user.click(screen.getByLabelText("Poll while the ticket is running"));
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(mockUpdateView).toHaveBeenCalledTimes(1));
    const layout = lastLayout();
    // Still a layout the server would take: AC1 is "persists to the view
    // record", and a body it would reject persists nothing.
    assertServerAcceptableLayout(layout);
    expect(settingsOf(layout, "c-seed")).toEqual({
      primitive_id: "run_ledger",
      ticket_id: "lg-flex-views-554",
      live: true,
    });
  });

  it("names no primitive, no field key and no field label of its own", () => {
    // The literal form of AC3's second half — "adding a primitive requires no
    // change to the editor". A generated editor cannot mention what it renders;
    // one that special-cased `workspace_slug` would still pass every rendering
    // test above and would need editing for the next primitive.
    //
    // Compared against the editor's *string literals*, not against its text: a
    // bare substring search calls `useSidebarWorkspaceSlug` a hard-coded
    // "Workspace" label and fails a correct implementation. A special case is
    // written as a literal — `field.key === "workspace_slug"` — so that is what
    // is looked for.
    const fs: typeof import("fs") = jest.requireActual("fs");
    const path: typeof import("path") = jest.requireActual("path");
    const files = ["../PaneSettingsEditor.tsx", "../paneSettingsDraft.ts"];

    const literals = new Set<string>();
    for (const file of files) {
      const source = fs.readFileSync(path.resolve(__dirname, file), "utf8");
      // The docstrings name ticket numbers and rationale; only the code is
      // bound by this, so comments are stripped before looking.
      const code = source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/.*$/gm, "");
      for (const [, dq, sq] of code.matchAll(/"([^"\\]*)"|'([^'\\]*)'/g)) {
        literals.add(dq ?? sq ?? "");
      }
    }

    const registry: RegisteredPrimitive[] =
      jest.requireActual("../primitives/registry").CONTAINER_PRIMITIVES;
    expect(registry.length).toBeGreaterThanOrEqual(3);
    for (const entry of registry) {
      for (const named of [entry.id, entry.displayName]) {
        expect({ named, quoted: literals.has(named) }).toEqual({ named, quoted: false });
      }
      expect(entry.settingsFields.length).toBeGreaterThan(0);
      for (const field of entry.settingsFields) {
        for (const named of [field.key, field.label, field.help ?? "\u0000"]) {
          expect({ named, quoted: literals.has(named) }).toEqual({ named, quoted: false });
        }
      }
    }
  });
});

describe("AC2 — a configured pane renders what it was configured with", () => {
  it("mounts a live shell once the terminal is given a workspace", async () => {
    const user = userEvent.setup();
    const { container } = renderGrid(leafLayout(containerOf("terminal")));
    await screen.findByTestId("view-host");
    // Before: the primitive's own empty state, because the seeded slug is "".
    expect(screen.queryByTestId("live-shell")).toBeNull();

    await openSettings(container, user);
    await user.type(screen.getByLabelText("Workspace"), "loregarden");
    await user.click(screen.getByRole("button", { name: "Save" }));

    // After: the record the server stored is what the pane re-renders from.
    await waitFor(() => expect(screen.getByTestId("live-shell")).toBeInTheDocument());
    expect(screen.getByTestId("live-shell")).toHaveAttribute("data-workspace", "loregarden");
  });

  it("mounts the ledger once the run ledger is given a ticket", async () => {
    const user = userEvent.setup();
    const { container } = renderGrid(leafLayout(containerOf("run_ledger")));
    await screen.findByTestId("view-host");

    await openSettings(container, user);
    await user.type(screen.getByLabelText("Ticket"), "t-42");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(screen.getByTestId("run-ledger")).toBeInTheDocument());
    expect(screen.getByTestId("run-ledger")).toHaveAttribute("data-ticket", "t-42");
  });

  it("frames the page once the web embed is given an https URL", async () => {
    // No mock in this one: `WebEmbedFrame` is the shipped component and the
    // URL policy it is behind is the shipped policy.
    const user = userEvent.setup();
    const { container } = renderGrid(leafLayout(containerOf("web_embed")));
    await screen.findByTestId("view-host");

    await openSettings(container, user);
    await user.type(screen.getByLabelText("URL"), "https://example.com/board");
    await user.click(screen.getByRole("button", { name: "Save" }));

    const frame = await waitFor(() => {
      const found = container.querySelector("iframe");
      if (found === null) throw new Error("no frame yet");
      return found;
    });
    expect(frame).toHaveAttribute("src", "https://example.com/board");
  });
});

describe("AC5 — an edit replaces the container rather than merging under a stale kind", () => {
  it("stamps the kind the primitive belongs to, repairing a container that disagreed", async () => {
    // The exact shape `ContainerPrimitiveHost` refuses: a terminal stored as a
    // panel. A settings write that *merged* would leave `kind: "panel"` behind
    // and the pane would still render the placeholder — which is why this is
    // asserted on a container that starts out wrong rather than one that starts
    // out right, where a merge and a replace are indistinguishable.
    const user = userEvent.setup();
    const stale: Json = { kind: "panel", settings: { primitive_id: "terminal", workspace_slug: "" } };
    const { container } = renderGrid(leafLayout(stale));
    await screen.findByTestId("view-host");
    expect(container.querySelector("[data-primitive-unknown='kind-mismatch']")).not.toBeNull();

    await openSettings(container, user);
    await user.type(screen.getByLabelText("Workspace"), "loregarden");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(mockUpdateView).toHaveBeenCalledTimes(1));
    expect(containersOf(lastLayout())["c-seed"]).toEqual({
      kind: "terminal",
      settings: { primitive_id: "terminal", workspace_slug: "loregarden" },
    });
    // And the pane the disagreement had broken now draws its primitive.
    await waitFor(() => expect(screen.getByTestId("live-shell")).toBeInTheDocument());
  });

  it("carries no settings key the primitive's schema does not declare", async () => {
    const user = userEvent.setup();
    const orphaned: Json = {
      kind: "terminal",
      settings: { primitive_id: "terminal", workspace_slug: "", legacy_theme: "solarized" },
    };
    const { container } = renderGrid(leafLayout(orphaned));
    await screen.findByTestId("view-host");

    await openSettings(container, user);
    await user.type(screen.getByLabelText("Workspace"), "loregarden");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(mockUpdateView).toHaveBeenCalledTimes(1));
    expect(settingsOf(lastLayout(), "c-seed")).toEqual({
      primitive_id: "terminal",
      workspace_slug: "loregarden",
    });
  });
});

describe("AC4 — the write shares the view's one queue, and opens no second path", () => {
  it("composes a settings save from the layout an earlier in-flight write produced", async () => {
    /**
     * The property `useViewLayoutEdit` exists for, exercised through the
     * settings editor rather than argued about: PATCH replaces the layout
     * whole, so a save composed at click time from the layout on screen would
     * revert whatever an open write was storing.
     *
     * A split is left in flight, a settings save is made while it is open, and
     * the second PATCH has to carry *both* — the split's third container and
     * the new workspace. A second write path, or a body composed at click time,
     * sends a two-container layout and deletes the pane the split just made.
     */
    const user = userEvent.setup();
    const { container } = renderGrid(
      pairLayout([0.5, 0.5], "horizontal", [
        containerOf("terminal") as Json,
        containerOf("terminal") as Json,
      ]),
    );
    await screen.findByTestId("view-host");

    let landSplit: (record: ViewSummary) => void = () => {};
    mockUpdateView.mockImplementation(
      (_slug, _viewId, patch) =>
        new Promise<ViewSummary>((resolve) => {
          landSplit = resolve;
          storePatch(patch);
        }),
    );

    await user.click(control(container, "n-1", "split-horizontal"));
    await waitFor(() => expect(mockUpdateView).toHaveBeenCalledTimes(1));
    expect(Object.keys(containersOf(lastLayout()))).toHaveLength(3);

    // Saved while the split's PATCH is still open.
    await user.click(control(container, "n-2", "pane-settings"));
    await user.type(screen.getByLabelText("Workspace"), "loregarden");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await act(async () => {
      landSplit(storePatch({}));
    });
    await settle();

    await waitFor(() => expect(mockUpdateView).toHaveBeenCalledTimes(2));
    const layout = lastLayout();
    assertServerAcceptableLayout(layout);
    // Both edits survive: the split's pane, and this pane's workspace.
    expect(Object.keys(containersOf(layout))).toHaveLength(3);
    expect(settingsOf(layout, "c-2")).toEqual({
      primitive_id: "terminal",
      workspace_slug: "loregarden",
    });
  });

  it("reaches the server through no module but the shared layout write", async () => {
    // The structural half. 438 shipped a Critical where a second layout write
    // read its identity from a render closure and destroyed another view's
    // record; the guarantee that cannot happen again is that there is only one
    // mutation, not that this one happens to be written carefully.
    const fs: typeof import("fs") = jest.requireActual("fs");
    const path: typeof import("path") = jest.requireActual("path");
    const source = fs.readFileSync(path.resolve(__dirname, "../PaneSettingsEditor.tsx"), "utf8");

    expect(source).not.toMatch(/useMutation|updateView|apiFetch|fetch\(/);
    expect(source).toMatch(/useContainerSettingsWrite/);
  });
});

describe("AC6 — the empty-pane copy names a control that exists", () => {
  it("points an unconfigured pane at the control in its own header", async () => {
    // Derived from the shipped copy and resolved against the shipped header:
    // the name is read out of the rendered hint and then looked up as an
    // accessible name in the same pane. A test that hard-coded the string would
    // still pass if the header's label were reworded and the copy were not.
    const { container } = renderGrid(leafLayout(containerOf("terminal")));
    await screen.findByTestId("view-host");

    const node = gridNode(container, "n-seed");
    const hint = node.querySelector(".pane-unconfigured-hint");
    expect(hint).not.toBeNull();
    const named = (hint as HTMLElement).querySelector("b")?.textContent ?? "";
    expect(named).not.toEqual("");

    expect(within(node).getByRole("button", { name: named })).toBeInTheDocument();
  });

  it("says nothing about a settings surface on a pane that has no primitive", async () => {
    // The other half of AC6: the copy must not point at a control that is not
    // there, and an empty pane deliberately has no settings control.
    const { container } = renderGrid(leafLayout());
    await screen.findByTestId("view-host");

    expect(gridNode(container, "n-seed").textContent ?? "").not.toContain(PANE_SETTINGS_LABEL);
  });
});

describe("AC7 — a value naming something absent is stored, not refused", () => {
  it("saves a workspace slug no workspace has, and lets the primitive answer", async () => {
    const user = userEvent.setup();
    const { container } = renderGrid(leafLayout(containerOf("terminal")));
    await screen.findByTestId("view-host");

    await openSettings(container, user);
    await user.type(screen.getByLabelText("Workspace"), "no-such-workspace");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(mockUpdateView).toHaveBeenCalledTimes(1));
    expect(settingsOf(lastLayout(), "c-seed")).toEqual({
      primitive_id: "terminal",
      workspace_slug: "no-such-workspace",
    });
  });

  it("saves a URL the embed policy refuses, and shows the primitive's own refusal", async () => {
    // The strongest form of AC7 available without a network: the value is
    // stored, the edit is not blocked, and what the operator then sees is the
    // *primitive's* verdict on it rather than the editor's.
    const user = userEvent.setup();
    const { container } = renderGrid(leafLayout(containerOf("web_embed")));
    await screen.findByTestId("view-host");

    await openSettings(container, user);
    await user.type(screen.getByLabelText("URL"), "ftp://files.example.com");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(mockUpdateView).toHaveBeenCalledTimes(1));
    expect(settingsOf(lastLayout(), "c-seed")).toEqual({
      primitive_id: "web_embed",
      url: "ftp://files.example.com",
    });
    // The pane, not the form, is where the refusal is reported.
    expect(await screen.findByText("ftp://files.example.com")).toBeInTheDocument();
    expect(container.querySelector("iframe")).toBeNull();
  });
});

describe("AC3 — a primitive the editor has never seen needs no change to it", () => {
  /**
   * A schema no registered primitive has: a number field, and a string field
   * whose key would reach `Object.prototype` if the editor or the registry
   * looked a value up on a plain object rather than a `Map`.
   *
   * The editor is the shipped one, mounted inside the same provider shell the
   * page gives it — the route that carries the view id, the workspace context,
   * and the react-query cache the write composes from. Only the schema is new,
   * which is exactly the claim: adding a primitive requires no change here. The
   * number path in particular has no other way to be reached, because no
   * registered primitive declares one.
   */
  const NOVEL_FIELDS: SettingsField[] = [
    { key: "constructor", kind: "string", label: "Name", default: "" },
    { key: "refresh_seconds", kind: "number", label: "Refresh seconds", default: 30 },
  ];

  const novelPrimitive: RegisteredPrimitive = {
    id: "novel",
    displayName: "Novel",
    icon: "◆",
    category: "Test",
    containerKind: "panel",
    settingsFields: NOVEL_FIELDS,
    parseSettings: (raw) => raw,
    Component: () => null,
  };

  /** The shipped editor, in the page's own shell, over a loaded view record. */
  function renderNovelEditor(stored: Json, onDone: () => void = () => {}) {
    const client = testClient();
    const record = viewOf(leafLayout(stored));
    // The write composes from the cache and refuses a view that is not in it,
    // which is the same record `ViewPage`'s query would have put there.
    client.setQueryData(viewsKeys.view(SLUG, record.id), record);

    return render(
      <QueryClientProvider client={client}>
        <SidebarWorkspaceProvider slug={SLUG}>
          <MemoryRouter initialEntries={[`/view/${record.id}`]}>
            <Routes>
              <Route
                path="/view/:viewId"
                element={
                  <PaneSettingsEditor
                    containerId="c-seed"
                    container={stored}
                    primitive={novelPrimitive}
                    onDone={onDone}
                  />
                }
              />
            </Routes>
          </MemoryRouter>
        </SidebarWorkspaceProvider>
      </QueryClientProvider>,
    );
  }

  const novelContainer = (): Json => ({
    kind: "panel",
    settings: { primitive_id: "novel", constructor: "", refresh_seconds: 30 },
  });

  it("generates an input per field of a schema the registry does not hold", () => {
    renderNovelEditor(novelContainer());

    expect(screen.getByLabelText("Name")).toHaveAttribute("type", "text");
    // The union's whole reason for existing: `kind` picks the input.
    expect(screen.getByLabelText("Refresh seconds")).toHaveAttribute("type", "number");
    // Seeded from what is stored, not from the schema's default.
    expect(screen.getByLabelText("Refresh seconds")).toHaveValue(30);
  });

  it("refuses a number that does not parse, in the field, rather than storing null", async () => {
    // 444: a non-finite number is coerced to `null` server-side and nothing
    // complains, so sending one discards the field silently. The type check
    // happens here — which is not the *meaning* validation the ticket puts out
    // of scope. Whether 45 seconds is a sensible refresh is never asked.
    const user = userEvent.setup();
    renderNovelEditor(novelContainer());

    await user.clear(screen.getByLabelText("Refresh seconds"));
    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(screen.getByRole("alert")).toHaveTextContent("Enter a number.");
    expect(screen.getByLabelText("Refresh seconds")).toHaveAttribute("aria-invalid", "true");
    expect(mockUpdateView).not.toHaveBeenCalled();
  });

  it("keeps the form open and says so when the write never leaves", async () => {
    // Worth pinning rather than working around: the write composes its
    // container in the registry, so an id the registry cannot resolve writes
    // nothing at all — the same refusal `newContainerFor` makes for a stale
    // pick. `useViewLayoutEdit` drops a write the same way when the workspace
    // has not resolved yet and `slug` is still "".
    //
    // The first version of this test asserted only that no PATCH was sent,
    // which pinned the *silence* as correct: the panel closed, the draft was
    // discarded, and a save that went nowhere looked exactly like one that
    // worked. What the operator is owed is the form they typed into, still
    // open, saying it did not save.
    const user = userEvent.setup();
    const onDone = jest.fn();
    renderNovelEditor(novelContainer(), onDone);

    await user.type(screen.getByLabelText("Name"), "board");
    await user.click(screen.getByRole("button", { name: "Save" }));
    await settle();

    expect(mockUpdateView).not.toHaveBeenCalled();
    // Not dismissed, so the typing survives and can be retried.
    expect(onDone).not.toHaveBeenCalled();
    expect(screen.getByLabelText("Name")).toHaveValue("board");
    expect(screen.getByRole("alert")).toHaveTextContent(/could not be saved/i);
  });

});

describe("every registered primitive can actually be configured", () => {
  it("declares at least one settings field", () => {
    // The invariant `Unconfigured`'s copy leans on. It says "Open Pane settings
    // in this pane's header", and the header hides that control for a primitive
    // whose schema is empty — so a future primitive with no fields would
    // reintroduce the exact bug 554 removed: copy naming a control that is not
    // on screen. Asserted here because the copy cannot assert it about itself.
    for (const entry of CONTAINER_PRIMITIVES) {
      expect({ id: entry.id, fields: entry.settingsFields.length > 0 }).toEqual({
        id: entry.id,
        fields: true,
      });
    }
  });
});

describe("containerWithSettings — the one place a container is composed", () => {
  it("falls back to the schema's default for a field the caller did not supply", () => {
    expect(containerWithSettings("run_ledger", new Map([["ticket_id", "t-1"]]))).toEqual({
      kind: "panel",
      settings: { primitive_id: "run_ledger", ticket_id: "t-1", live: false },
    });
  });

  it("defines a field key that Object.prototype would have swallowed", () => {
    // Through `composeSettings`, not through `containerWithSettings`: no
    // registered primitive declares a `__proto__` field, so this rule has no
    // reachable path through the registry lookup and would otherwise be
    // asserted nowhere. A first attempt checked `Object.fromEntries` on a
    // literal, which is a tautology — the mutation that put plain assignment
    // back survived it.
    //
    // The bug it guards: `settings["__proto__"] = x` hits `Object.prototype`'s
    // setter, is silently discarded, and the declared field never reaches the
    // wire with no error anywhere. `constructor` is an ordinary own property
    // and always worked, which is why testing only that one proved nothing.
    const fields: SettingsField[] = [
      { key: "__proto__", kind: "string", label: "Proto", default: "" },
      { key: "constructor", kind: "string", label: "Ctor", default: "" },
    ];
    const composed = composeSettings(
      fields,
      new Map([
        ["__proto__", "board"],
        ["constructor", "ledger"],
      ]),
      "novel",
    );

    expect(Object.hasOwn(composed, "__proto__")).toBe(true);
    // Serialised, because reaching the wire is the claim — a key that is an own
    // property but not enumerable would still be lost in the PATCH.
    //
    // The expectation is built with `fromEntries` for the same reason the code
    // is: `{ __proto__: "board" }` in a literal sets the *prototype* and
    // declares no own key, so a literal here quietly expects the bug. The first
    // version of this assertion did exactly that and failed against correct
    // code.
    expect(JSON.parse(JSON.stringify(composed))).toEqual(
      Object.fromEntries([
        ["__proto__", "board"],
        ["constructor", "ledger"],
        ["primitive_id", "novel"],
      ]),
    );
  });

  it("lets no declared field overwrite the primitive id", () => {
    // The id is the container's other record of the same decision the `kind`
    // carries; a field keyed `primitive_id` winning would store a container
    // `ContainerPrimitiveHost` cannot mount.
    const fields: SettingsField[] = [
      { key: "primitive_id", kind: "string", label: "Id", default: "" },
    ];
    const composed = composeSettings(fields, new Map([["primitive_id", "attacker"]]), "novel");
    expect(composed.primitive_id).toBe("novel");
  });

  it("writes nothing for a primitive this build does not have", () => {
    expect(containerWithSettings("gone", new Map([["url", "https://x.test"]]))).toBeUndefined();
  });
});

describe("readDraft — what a draft settles as", () => {
  /**
   * The number semantics live here rather than in a rendered form, and not for
   * convenience: no registered primitive declares a number field, and the write
   * resolves its container through the registry, so there is no end-to-end path
   * to drive one down. This is the unit that owns the decision, asked directly.
   *
   * The string and boolean paths *are* exercised end to end above, through the
   * run ledger's two real fields.
   */
  const numberField: SettingsField = {
    key: "refresh_seconds",
    kind: "number",
    label: "Refresh seconds",
    default: 30,
  };

  it("stores a parsed number, not the text that was typed", () => {
    const { values, errors } = readDraft([numberField], new Map([["refresh_seconds", "45"]]));
    expect(errors.size).toBe(0);
    // `null` and "45" are both things the server takes, which is why the type
    // is asserted and not only the value.
    expect(values.get("refresh_seconds")).toBe(45);
  });

  it.each([
    ["an empty box", ""],
    ["blank space", "   "],
    ["something that is not a number", "soon"],
    ["a partial exponent", "1e"],
    ["an infinity", "Infinity"],
  ])("refuses %s rather than storing a null the server would swallow", (_case, text) => {
    const { values, errors } = readDraft([numberField], new Map([["refresh_seconds", text]]));
    expect(errors.get("refresh_seconds")).toBe(NOT_A_NUMBER);
    // Refused means *not written*: a field that both errored and stored would
    // save the bad value and tell the operator it had not.
    expect(values.has("refresh_seconds")).toBe(false);
  });

  it("stores a boolean for every declared checkbox, touched or not", () => {
    const live: SettingsField = { key: "live", kind: "boolean", label: "Live", default: false };
    const { values } = readDraft([live], new Map());
    // Not `undefined`: a container whose declared key is missing is a container
    // the primitive then reads a default for twice, in two different places.
    expect(values.get("live")).toBe(false);
  });

  it("opens a number field on its default when what is stored is not a number", () => {
    // 444's shape: a non-finite number comes back as `null`, which is not
    // something a user can edit into a number.
    expect(initialDraft([numberField], { refresh_seconds: null })).toEqual(
      new Map([["refresh_seconds", "30"]]),
    );
    expect(initialDraft([numberField], { refresh_seconds: 12 })).toEqual(
      new Map([["refresh_seconds", "12"]]),
    );
  });
});

describe("the header's panels survive a pane too short to hold them", () => {
  it("declares the panels as scroll containers that may shrink", () => {
    /**
     * Structural, and it says so: jsdom has no layout engine, so "the Save
     * button is off the bottom of a 148px pane" is not a question that can be
     * asked here. What can be asked is the cause.
     *
     * The pane a header is drawn in is a flex column with `overflow: hidden`,
     * and a flex child refuses to shrink below its content until `min-height:
     * 0` says otherwise. Without both declarations the settings form was
     * *clipped* — measured at 167px inside a 149px pane, with the Save button
     * unreachable and no scrollbar to say so. Found in a browser, which is
     * where the design pass this ticket follows found its own defects too.
     */
    const fs: typeof import("fs") = jest.requireActual("fs");
    const path: typeof import("path") = jest.requireActual("path");
    const css = fs.readFileSync(path.resolve(__dirname, "../paneChrome.css"), "utf8");

    for (const panel of ["pane-settings-panel", "pane-picker-panel"]) {
      const declared: Record<string, string> = {};
      for (const [, selector, body] of css
        .replace(/\/\*[\s\S]*?\*\//g, "")
        .matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
        const matches = selector
          .split(",")
          .some((part) => new RegExp(`\\.${panel}(?![\\w-])`).test(part));
        if (!matches) continue;
        for (const declaration of body.split(";")) {
          const [property, ...rest] = declaration.split(":");
          if (rest.length === 0) continue;
          declared[property.trim().toLowerCase()] = rest.join(":").trim().toLowerCase();
        }
      }
      expect({ panel, minHeight: declared["min-height"] }).toEqual({ panel, minHeight: "0" });
      const scrolls = ["overflow", "overflow-y"].some((property) =>
        ["auto", "scroll"].includes(declared[property] ?? ""),
      );
      expect({ panel, scrolls }).toEqual({ panel, scrolls: true });
    }
  });
});
