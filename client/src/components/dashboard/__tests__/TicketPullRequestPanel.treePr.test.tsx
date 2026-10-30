import { readFileSync } from "node:fs";
import { join } from "node:path";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import type { TicketPullRequest } from "../../../api/ticketPullRequestApi";
import { uiActionRegistry } from "../../../lib/agentActions/registry";
import { useTicketRefStore } from "../../../state/ticketRefStore";
import { TicketPullRequestPanel } from "../TicketPullRequestPanel";
import {
  deferred,
  lookup,
  mockApi,
  OPEN_TREE_PR,
  PR,
  PROVENANCE,
  provenanceLine,
  READY,
  renderPanel,
  renderPending,
  TREE_MEMBER,
  TREE_ROOT,
} from "./ticketPullRequestPanelFixtures";

jest.mock("../../../api/client");

/**
 * The route out of the provenance line (lg-verdict-outcome-1655).
 *
 * The line named the tree's branch and ended there: an operator who had just
 * read "Checks pass; ready to merge" and found no merge button had to recognise
 * `integration/lg-ms-1` as a branch, strip the prefix, recognise the remainder
 * as a ticket id and go find it. The root is named by the server
 * (`ships_with_root`) and the line ends in a link to its PR tab.
 *
 * `ships_with_root` is "lg-ms-1" here while `ships_with` is
 * "integration/lg-ms-1" and the PR's head is "publish/integration/lg-ms-1", so
 * every assertion on the link's target also tells apart reading the field from
 * deriving it off the branch string.
 */
describe("TicketPullRequestPanel, the route to the tree's pull request", () => {
  beforeEach(() => jest.clearAllMocks());
  // `useTicketRefStore` is a module global; one test below teaches it a pair.
  afterEach(() => useTicketRefStore.setState({ uuidByRef: {}, refByUuid: {} }));

  /** The provenance line's link, found inside the line rather than in the pane. */
  async function treeRootLink(data: TicketPullRequest): Promise<{ line: HTMLElement; link: HTMLElement }> {
    const container = renderPanel(data);
    await screen.findByRole("link", { name: PR.title });
    const line = provenanceLine(container);
    expect(line).not.toBeNull();
    return { line: line!, link: within(line!).getByRole("link", { name: OPEN_TREE_PR }) };
  }

  it("ends the line in a link to the tree root's PR tab", async () => {
    // AC1 + UI-1/UI-2: one <p>, the branch still in its <code>, and a route out
    // of it. The target is the root's PR tab, not its default tab.
    const { line, link } = await treeRootLink(TREE_MEMBER);

    expect(link).toHaveAttribute("href", "/tickets/lg-ms-1/pr");
    expect(line.tagName).toBe("P");
    expect(line.className).toBe("prp-muted");
    expect(line.querySelector("code.prp-branch")).toHaveTextContent("integration/lg-ms-1");
  });

  it("puts the link after the branch code, as its sibling in the same paragraph", async () => {
    // UI-2: a sibling AFTER the <code>, inside the same <p> — not a second line,
    // not a wrapper around the branch, not before the text.
    const { line, link } = await treeRootLink(TREE_MEMBER);

    expect(link.parentElement).toBe(line);
    expect(line.querySelector("code.prp-branch")?.nextElementSibling).toBe(link);
    expect(line.textContent).toMatch(
      /^Ships in its tree’s pull request\s+integration\/lg-ms-1\s+Open the tree’s pull request$/,
    );
  });

  it("states its destination in its own text, with no aria-label, title or icon", async () => {
    // UI-2 + UI-7: the text IS the accessible name, and the class is what
    // carries the colour and the underline, so no literal of either can arrive
    // here. `getByRole(name)` above already fails on a different name.
    const { link } = await treeRootLink(TREE_MEMBER);

    expect(link.textContent).toBe(OPEN_TREE_PR);
    expect(link.className).toBe("prp-tree-root");
    expect(link.getAttribute("aria-label")).toBeNull();
    expect(link.getAttribute("title")).toBeNull();
    expect(link.getAttribute("style")).toBeNull();
    expect(link.querySelector("svg")).toBeNull();
  });

  it("leaves the branch name plain text, never the link itself", async () => {
    // UI-2: a 41-character mono token with `overflow-wrap: anywhere` would wrap
    // mid-token and split its own underline, and "integration/lg-x-86" promises
    // GitHub rather than this ticket's page. One link on the line, and the
    // <code> is not inside it.
    const { line } = await treeRootLink(TREE_MEMBER);
    const code = line.querySelector("code.prp-branch");

    expect(code?.className).toBe("prp-branch");
    expect(code?.closest("a")).toBeNull();
    expect(code?.querySelector("a")).toBeNull();
    expect(code?.getAttribute("style")).toBeNull();
    expect(line.querySelectorAll("code")).toHaveLength(1);
    expect(within(line).getAllByRole("link")).toHaveLength(1);
  });

  it("follows the root the server named, not the branch with its prefix taken off", async () => {
    // UI-4: the client derives nothing. A `ships_with.slice("integration/".length)`
    // passes every other test in this block and fails here, where the root ref
    // and the branch's own slug are different strings.
    const { link } = await treeRootLink({ ...TREE_MEMBER, ships_with_root: "lg-other-7" });

    expect(link).toHaveAttribute("href", "/tickets/lg-other-7/pr");
    expect(link).not.toHaveAttribute("href", "/tickets/lg-ms-1/pr");
  });

  it("links a uuid root ref as readily as a shareable id", async () => {
    // AC2: a root with no external_id is named by its FULL uuid, which
    // `ticketPath` accepts and `TicketRouteResolver` later re-addresses. The
    // 8-character prefix in the branch is a branch slug no route resolves.
    const uuid = "3f2a1b4c-0d5e-4f60-8a71-9b2c3d4e5f60";
    const { link } = await treeRootLink({
      ...TREE_MEMBER,
      ships_with: `integration/${uuid.slice(0, 8)}`,
      ships_with_root: uuid,
    });

    expect(link).toHaveAttribute("href", `/tickets/${uuid}/pr`);
  });

  it("renders exactly today's text, and nothing in the link's place, when the root is unresolvable", async () => {
    // AC2 + UI-5: the empty case. A constructed payload, not a live one — 0 of
    // 2086 tickets have a blank external_id, so this is reachable only through
    // the field's "" default or a payload predating it. No link, and no
    // placeholder standing in for one: no empty <span>, no "—", no
    // aria-disabled, no reserved space.
    const container = renderPanel({ ...TREE_MEMBER, ships_with_root: "" });
    await screen.findByRole("link", { name: PR.title });
    const line = provenanceLine(container);

    expect(line?.tagName).toBe("P");
    expect(line?.className).toBe("prp-muted");
    expect(line?.textContent).toMatch(/^Ships in its tree’s pull request\s+integration\/lg-ms-1$/);
    expect(line?.querySelectorAll("code")).toHaveLength(1);
    expect(line?.querySelector("a")).toBeNull();
    expect(line?.querySelector("span")).toBeNull();
    expect(line?.querySelector("[aria-disabled]")).toBeNull();
    expect(within(line!).queryByRole("link")).toBeNull();
    expect(screen.queryByRole("link", { name: OPEN_TREE_PR })).not.toBeInTheDocument();
  });

  it("adds no link where there is no provenance line to put it on", async () => {
    // The mutant: a link gated on `rootRef` alone rather than nested inside the
    // line's own gate. This root's payload carries a root ref and renders no
    // line, so a loose gate puts a bare link into the panel here.
    const container = renderPanel({ ...TREE_ROOT, ships_with_root: "lg-ms-1" });
    await screen.findByRole("link", { name: PR.title });

    expect(provenanceLine(container)).toBeNull();
    expect(screen.queryByRole("link", { name: OPEN_TREE_PR })).not.toBeInTheDocument();
    expect(screen.queryByText(PROVENANCE)).not.toBeInTheDocument();
  });

  it("makes the link the first focusable thing in the pane, ahead of Open on GitHub", async () => {
    // UI-6: the provenance line is the pane's first child, so its link is the
    // pane's first stop — which is right for a tab that offers no merge. Enter
    // follows it; nothing is disabled and nothing is in flight.
    const container = renderPanel(TREE_MEMBER);
    await screen.findByRole("link", { name: PR.title });
    const focusable = [...container.querySelectorAll<HTMLElement>(".prp a[href], .prp button")];
    const link = screen.getByRole("link", { name: OPEN_TREE_PR });
    const github = screen.getByRole("link", { name: "Open on GitHub" });

    expect(focusable[0]).toBe(link);
    expect(focusable.indexOf(link)).toBeLessThan(focusable.indexOf(github));
    expect(link).not.toBeDisabled();
    expect(link.getAttribute("aria-disabled")).toBeNull();
    link.focus();
    expect(link).toHaveFocus();
  });

  // UI-8: the other three `prp-branch` sites stay plain text. The `ap-state`
  // arm is the tempting one and is deliberately excluded — when the tree branch
  // has no PR, the root's PR tab shows that same "no pull request yet" state, so
  // a link there would lead somewhere that answers nothing. Each payload below
  // carries a non-empty root ref, so only a link scoped to the rendered
  // provenance line passes all three.
  const MEMBER_FIELDS = {
    branch: "publish/integration/lg-ms-1",
    ships_with: "integration/lg-ms-1",
    ships_with_root: "lg-ms-1",
  };

  it("adds no link to the ap-state arm for a member whose tree has no PR yet", async () => {
    renderPanel(lookup({ lookup: "none", pull_request: null, ...MEMBER_FIELDS }));

    expect(await screen.findByText(PROVENANCE)).toHaveClass("ap-state-title");
    expect(screen.queryByRole("link", { name: OPEN_TREE_PR })).not.toBeInTheDocument();
  });

  it("adds no link to the generic no-PR arm", async () => {
    renderPanel(lookup({ lookup: "none", pull_request: null, ships_with_root: "lg-ms-1" }));

    expect(await screen.findByText("No pull request for this branch yet")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: OPEN_TREE_PR })).not.toBeInTheDocument();
  });

  it("adds no link to the failed lookup", async () => {
    renderPanel(lookup({ lookup: "failed", pull_request: null, error: "gh: HTTP 503", ...MEMBER_FIELDS }));

    expect(await screen.findByRole("alert")).toHaveTextContent("GitHub could not be asked about");
    expect(screen.queryByRole("link", { name: OPEN_TREE_PR })).not.toBeInTheDocument();
  });

  it("builds the href through ticketPath, so a uuid root ref picks up the readable address", async () => {
    // UI-4: `to={ticketPath(rootRef, "pr")}`, not a path written out here. Every
    // other assertion in this block passes against a hardcoded
    // `/tickets/${rootRef}/pr`, because the ref store is empty in jest and
    // `ticketPath` then falls through to the id it was handed. With the pair
    // learned — which is what `TicketRouteResolver` does the moment the root's
    // page has been opened once — the two spellings differ, and only the helper
    // writes the address the rest of the app writes.
    const uuid = "7c1d9e02-5b44-4a21-9f30-8e6b5c4d3a21";
    useTicketRefStore.getState().remember({ id: uuid, external_id: "lg-ms-readable" });

    const { link } = await treeRootLink({
      ...TREE_MEMBER,
      ships_with: `integration/${uuid.slice(0, 8)}`,
      ships_with_root: uuid,
    });

    expect(link).toHaveAttribute("href", "/tickets/lg-ms-readable/pr");
  });
});

/**
 * The link's style, read off the stylesheet (UI-7, and UI-3's two prohibitions).
 *
 * The class name is asserted on the element above, but a class with no rules
 * behind it renders the link in the muted colour of its own sentence with no
 * underline — colour alone, and not even that. Nothing else in the suite opens
 * this file: the theme gate sees changed lines only, and it checks for literals
 * rather than for the rules being there at all.
 */
describe("TicketPullRequestPanel.css, the tree-root link", () => {
  const css = readFileSync(join(__dirname, "..", "TicketPullRequestPanel.css"), "utf8");

  /** The declarations inside one selector's block, whitespace collapsed. */
  function block(selector: string): string {
    const at = css.indexOf(selector);
    expect(at).toBeGreaterThanOrEqual(0);
    const open = css.indexOf("{", at);
    const close = css.indexOf("}", open);
    expect(open).toBeGreaterThan(-1);
    expect(close).toBeGreaterThan(open);
    return css.slice(open + 1, close).replace(/\s+/g, " ").trim();
  }

  it("is underlined in --txm at rest, because the line it sits in is muted", () => {
    // UI-7: colour alone would be the only cue inside a `prp-muted` sentence
    // (WCAG 1.4.1), and `--txm` rather than `--txl` is what tells the control
    // apart from the text around it. Tokens, never literals.
    const rest = block(".prp-tree-root {");

    expect(rest).toContain("color: var(--txm)");
    expect(rest).toContain("text-decoration: underline");
    expect(rest).toContain("text-underline-offset: 2px");
    expect(rest).not.toMatch(/#[0-9a-f]{3}/i);
    expect(rest).not.toContain("font-size");
  });

  it("answers hover and focus-visible together, in --ac2", () => {
    // UI-7, and UI-6: a keyboard user gets the same answer as a mouse, which is
    // the pattern `a.prp-check-name` and `.prp-title a` already use here.
    expect(css).toContain(".prp-tree-root:hover,\n.prp-tree-root:focus-visible");
    expect(block(".prp-tree-root:hover,")).toContain("color: var(--ac2)");
  });

  it("neither breaks mid-word nor refuses to wrap at all", () => {
    // UI-3: the pane's own header calls it narrow, and the longest realistic
    // line is "Ships in its tree’s pull request integration/<29 chars> Open the
    // tree’s pull request". `overflow-wrap: anywhere` (which `.prp-branch`
    // carries, deliberately) would split the link text and its underline
    // mid-word; `white-space: nowrap` would push the line out of the pane.
    const rules = `${block(".prp-tree-root {")} ${block(".prp-tree-root:hover,")}`;

    expect(rules).not.toContain("overflow-wrap");
    expect(rules).not.toContain("word-break");
    expect(rules).not.toContain("nowrap");
  });

  it("adds no motion, so nothing animates on a line that only changes colour", () => {
    // UI-7: no transition and no keyframes. A duration literal here would also
    // be the kind the motion gate only sees on a changed line.
    const rules = `${block(".prp-tree-root {")} ${block(".prp-tree-root:hover,")}`;

    expect(rules).not.toContain("transition");
    expect(rules).not.toContain("animation");
  });
});

/**
 * The states that are asserted rather than edited: the two no-PR arms and their
 * order, the failed lookup, and what is on screen while something is in flight.
 */
describe("TicketPullRequestPanel, the states around the tree PR", () => {
  beforeEach(() => jest.clearAllMocks());

  it("keeps a plain, non-alert pane for a member whose tree has no PR yet", async () => {
    // AC10(a) + AC13: pinned by structure — an `ap-state` pane carrying the
    // wording as its title, with no PR title, number, verdict or checks.
    const container = renderPanel(
      lookup({
        lookup: "none",
        pull_request: null,
        branch: "publish/integration/lg-ms-1",
        ships_with: "integration/lg-ms-1",
      }),
      { onOpenPr: jest.fn(), onCommitPush: jest.fn() },
    );

    const title = await screen.findByText(PROVENANCE);
    expect(title).toHaveClass("ap-state-title");
    expect(title.closest(".ap-state")).not.toHaveAttribute("role");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();

    expect(screen.getByRole("button", { name: "Refresh" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Open PR" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Commit & push" })).not.toBeInTheDocument();

    expect(screen.queryByRole("link", { name: PR.title })).not.toBeInTheDocument();
    expect(screen.queryByText("#555")).not.toBeInTheDocument();
    expect(screen.queryByText(/is failing/)).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Checks" })).not.toBeInTheDocument();
    expect(provenanceLine(container)).toBeNull();
  });

  it("never offers a member its own PR, whatever the lookup says", async () => {
    // AC10(b): the ships_with arm precedes the generic no-PR arm, so reading the
    // tree's PR can never become a route to opening a per-member one.
    renderPanel(lookup({ lookup: "found", pull_request: null, ships_with: "integration/lg-ms-1" }), {
      onOpenPr: jest.fn(),
      onCommitPush: jest.fn(),
    });

    expect(await screen.findByText(PROVENANCE)).toBeInTheDocument();
    expect(screen.queryByText("No pull request for this branch yet")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Open PR" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Commit & push" })).not.toBeInTheDocument();
  });

  it("keeps the generic no-PR arm, with both its buttons, for a ticket with no tree", async () => {
    // AC10(b), the other side: ships_with empty leaves today's path untouched.
    renderPanel(lookup({ lookup: "none", pull_request: null }), {
      onOpenPr: jest.fn(),
      onCommitPush: jest.fn(),
    });

    expect(await screen.findByText("No pull request for this branch yet")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Open PR" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Commit & push" })).toBeInTheDocument();
    expect(screen.queryByText(PROVENANCE)).not.toBeInTheDocument();
  });

  it("names the branch GitHub could not be asked about, beside the stderr", async () => {
    // AC10(c) + AC14(c): which branch, then why — and never the tree-PR panel.
    renderPanel(
      lookup({
        lookup: "failed",
        pull_request: null,
        branch: "publish/integration/lg-ms-1",
        ships_with: "integration/lg-ms-1",
        is_tree_pull_request: true,
        error: "gh: could not resolve to a Repository",
      }),
    );

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("GitHub could not be asked about");
    expect(alert).toHaveTextContent("gh: could not resolve to a Repository");

    const named = alert.querySelector("p.prp-muted > code.prp-branch");
    expect(named).toHaveTextContent("publish/integration/lg-ms-1");
    const body = alert.querySelector(".ap-state-body");
    expect(body?.firstElementChild).toBe(named?.parentElement);
    expect(body?.firstElementChild?.nextElementSibling).toHaveClass("prp-error");

    expect(alert.querySelector(".ap-state-title")?.textContent).toMatch(/^Could not ask GitHub about this branch’s pull request$/);
    expect(screen.getByRole("button", { name: "Refresh" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: PR.title })).not.toBeInTheDocument();
    expect(screen.queryByText(PROVENANCE)).not.toBeInTheDocument();
  });

  it("draws a five-row list skeleton, and no spinner, while the lookup is pending", async () => {
    // AC11, loading.
    const container = renderPending();

    const skeleton = await screen.findByTestId("pane-skeleton");
    expect(skeleton).toHaveAttribute("data-variant", "list");
    expect(container.querySelectorAll(".pane-skeleton-row")).toHaveLength(5);
    expect(skeleton).toHaveTextContent("Asking GitHub about this ticket's pull request…");
    expect(container.querySelector(".spinner")).toBeNull();
  });

  it("disables Refresh and reads 'Checking…' while it is refetching", async () => {
    // AC11, in flight.
    renderPanel(TREE_MEMBER);
    const refresh = await screen.findByRole("button", { name: "Refresh" });

    const pending = deferred<TicketPullRequest>();
    mockApi.ticketPullRequest.mockReturnValueOnce(pending.promise as never);
    fireEvent.click(refresh);

    const checking = await screen.findByRole("button", { name: "Checking…" });
    expect(checking).toBeDisabled();

    pending.settle(TREE_MEMBER);
    await waitFor(() => expect(screen.getByRole("button", { name: "Refresh" })).toBeEnabled());
  });

  it("disables 'Ask triage to fix' while it is posting, so a second click cannot post twice", async () => {
    // AC11, in flight: one message per click, not one per impatient click.
    const sending = deferred<void>();
    mockApi.sendTriageMessage.mockReturnValue(sending.promise as never);
    renderPanel(TREE_MEMBER);

    fireEvent.click(await screen.findByRole("button", { name: "Ask triage to fix" }));

    const inFlight = await screen.findByRole("button", { name: "Sending…" });
    expect(inFlight).toBeDisabled();
    fireEvent.click(inFlight);
    expect(mockApi.sendTriageMessage).toHaveBeenCalledTimes(1);

    sending.settle(undefined);
    await waitFor(() => expect(screen.getByRole("button", { name: "Sent to triage chat" })).toBeInTheDocument());
    expect(mockApi.sendTriageMessage).toHaveBeenCalledTimes(1);
  });
});

/**
 * The corners the tests above leave open.
 *
 * Each of these kills a version of the change that passes every test in this
 * file so far: a prompt whose other lines drifted, a root whose prompt picked up
 * the tree branch anyway, a provenance line pasted twice, a `treeBranch` whose
 * two consumers disagree about what "not a tree PR" is, and the failed-lookup
 * branch named only when the ticket happens to be a tree member.
 */
describe("TicketPullRequestPanel, the half-done versions of the tree PR", () => {
  beforeEach(() => jest.clearAllMocks());

  /** The whole message posted to triage, after one click. */
  async function askToFix(data: TicketPullRequest): Promise<string> {
    mockApi.sendTriageMessage.mockResolvedValue(undefined as never);
    renderPanel(data);
    fireEvent.click(await screen.findByRole("button", { name: "Ask triage to fix" }));
    await waitFor(() => expect(mockApi.sendTriageMessage).toHaveBeenCalled());
    return mockApi.sendTriageMessage.mock.calls[0][1] as string;
  }

  it("changes only the closing line of a member's prompt", async () => {
    // R3/AC14(b): "every other line is unchanged" is unpinned by a `toContain`
    // on the closing line alone, so this asserts the whole message.
    expect(await askToFix(TREE_MEMBER)).toBe(
      [
        "CI is failing on PR #555 (https://github.com/o/r/pull/555):",
        "",
        "- Server (Python): https://ci/1",
        "",
        "Read the failing logs, find the first real failure, fix it on integration/lg-ms-1 and push.",
      ].join("\n"),
    );
  });

  it("leaves a non-tree ticket's prompt byte-for-byte as it is today", async () => {
    expect(await askToFix(lookup({}))).toBe(
      [
        "CI is failing on PR #555 (https://github.com/o/r/pull/555):",
        "",
        "- Server (Python): https://ci/1",
        "",
        "Read the failing logs, find the first real failure, fix it on this ticket's branch, and push.",
      ].join("\n"),
    );
  });

  it("does not put the tree's branch in a tree ROOT's prompt", async () => {
    // The mutant this kills: the line gated on `is_tree_pull_request` but the
    // prompt handed `ships_with` unconditionally. Every test above passes it,
    // because the only prompt pinned for a non-member has `ships_with: ""`.
    const message = await askToFix(TREE_ROOT);

    expect(message).toContain("fix it on this ticket's branch, and push.");
    expect(message).not.toContain("integration/lg-ms-1");
  });

  it("lists every failing check above the closing line, with or without a log link", async () => {
    const message = await askToFix(
      lookup({
        branch: "publish/integration/lg-ms-1",
        ships_with: "integration/lg-ms-1",
        is_tree_pull_request: true,
        pull_request: {
          ...PR,
          head: "publish/integration/lg-ms-1",
          checks: [
            { name: "Server (Python)", outcome: "failing", url: "https://ci/1" },
            { name: "Client (jest)", outcome: "failing", url: "" },
            { name: "PR UX section", outcome: "passing", url: "https://ci/2" },
          ],
        },
      }),
    );

    expect(message).toBe(
      [
        "CI is failing on PR #555 (https://github.com/o/r/pull/555):",
        "",
        "- Server (Python): https://ci/1",
        "- Client (jest)",
        "",
        "Read the failing logs, find the first real failure, fix it on integration/lg-ms-1 and push.",
      ].join("\n"),
    );
  });

  it("treats an empty ships_with as not a tree PR, whatever the boolean says", async () => {
    // AC14(a)'s whole argument is that both consumers read one string, so they
    // cannot disagree. A payload that contradicts itself is the test of that: a
    // child gating on its own copy of `is_tree_pull_request` would render
    // "Ships in its tree's pull request" with an empty <code> after it.
    const contradictory = lookup({
      branch: PR.head,
      ships_with: "",
      is_tree_pull_request: true,
    });
    const container = renderPanel(contradictory);
    await screen.findByRole("link", { name: PR.title });

    expect(provenanceLine(container)).toBeNull();
    expect(container.querySelector(".prp code.prp-branch")).toBeNull();

    mockApi.sendTriageMessage.mockResolvedValue(undefined as never);
    fireEvent.click(screen.getByRole("button", { name: "Ask triage to fix" }));
    await waitFor(() => expect(mockApi.sendTriageMessage).toHaveBeenCalled());
    expect(mockApi.sendTriageMessage.mock.calls[0][1]).toContain("fix it on this ticket's branch, and push.");
  });

  it("refuses the merge on the same contradictory payload the display treats as not a tree PR", async () => {
    // R6. The display assertions in the test above use the default `PR`, whose
    // `mergeable_now` is false, so no other test in this file can see what the
    // merge guard does on a flag-true / empty-`ships_with` payload. That is the
    // gap this one test spends itself on. With the derivation ANDed
    // (`is_tree_pull_request && ships_with ? { shipsWith } : null`) the panel
    // would newly OFFER squash-merge-to-base and integration-branch deletion
    // here, where `isTreePullRequest={data.is_tree_pull_request}` refuses today.
    // Display behaves as if this were not a tree PR; the guard behaves as if it
    // were, and both of those are the behaviour at 034fedcc.
    renderPanel(
      lookup({
        branch: PR.head,
        ships_with: "",
        is_tree_pull_request: true,
        pull_request: READY,
      }),
    );
    await screen.findByRole("link", { name: PR.title });

    expect(screen.queryByRole("button", { name: "Merge and clean up" })).not.toBeInTheDocument();
    expect(uiActionRegistry.available()).not.toContain("ticket.merge_pull_request");
  });

  it("renders the provenance line once, and nowhere else in the panel", async () => {
    // "as its first child, and nowhere else" (R2). The first-child assertion
    // above still passes with a second copy pasted inside the header.
    const container = renderPanel(TREE_MEMBER);
    await screen.findByRole("link", { name: PR.title });

    const lines = [...container.querySelectorAll("p.prp-muted")].filter((p) => p.querySelector("code.prp-branch"));
    expect(lines).toHaveLength(1);
    expect(container.querySelectorAll(".prp > p.prp-muted > code.prp-branch")).toHaveLength(1);
  });

  it("keeps the line for a member whose tree PR has already merged", async () => {
    // The line is static text about the panel, not about the fix action: a
    // version that renders it beside "Ask triage to fix" disappears here.
    const container = renderPanel(
      lookup({
        branch: "publish/integration/lg-ms-1",
        ships_with: "integration/lg-ms-1",
        is_tree_pull_request: true,
        pull_request: { ...PR, head: "publish/integration/lg-ms-1", state: "merged" },
      }),
    );

    expect(await screen.findByText("Merged into main.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Ask triage to fix" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Checks" })).not.toBeInTheDocument();

    const line = provenanceLine(container);
    expect(container.querySelector(".prp")?.firstElementChild).toBe(line);
    expect(line?.querySelector("code.prp-branch")).toHaveTextContent("integration/lg-ms-1");
  });

  it("keeps the line, alone, for a member whose PR reported no checks at all", async () => {
    // `.prp-muted` is also what "No CI checks reported for this PR." uses, so
    // this is the payload that tells a direct-child query from a loose one.
    const container = renderPanel(
      lookup({
        branch: "publish/integration/lg-ms-1",
        ships_with: "integration/lg-ms-1",
        is_tree_pull_request: true,
        pull_request: { ...PR, head: "publish/integration/lg-ms-1", checks: [] },
      }),
    );

    expect(await screen.findByText("No CI checks reported for this PR.")).toBeInTheDocument();
    const line = provenanceLine(container);
    expect(container.querySelectorAll(".prp > p.prp-muted")).toHaveLength(1);
    expect(line?.querySelector("code.prp-branch")).toHaveTextContent("integration/lg-ms-1");
  });

  it("names the branch GitHub could not be asked about for a ticket with no tree", async () => {
    // AC10(c) is about there being more than one candidate per ticket, not about
    // trees: the only failed-lookup test above has `is_tree_pull_request: true`,
    // so a line gated on that boolean passes it and fails here.
    renderPanel(
      lookup({
        lookup: "failed",
        pull_request: null,
        error: "HTTP 401: Bad credentials",
        recorded: { url: "https://github.com/o/r/pull/9", number: "9", title: "older" },
      }),
    );

    const alert = await screen.findByRole("alert");
    const named = alert.querySelector("p.prp-muted > code.prp-branch");
    expect(named).toHaveTextContent(PR.head);

    const body = alert.querySelector(".ap-state-body");
    expect(body?.firstElementChild).toBe(named?.parentElement);
    expect(body?.firstElementChild?.nextElementSibling).toHaveClass("prp-error");
    expect(screen.getByRole("link", { name: "#9 older" })).toHaveAttribute("href", "https://github.com/o/r/pull/9");
  });

  it("still shows the query-error alert, and no provenance line, when the request itself fails", async () => {
    // AC11: "the existing describeError → query-error alert stays." Nothing
    // above renders this arm, so a `data.ships_with` read moved above the
    // isError guard would crash the panel with every test in this file green.
    mockApi.ticketPullRequest.mockRejectedValue(new Error("Network request failed"));
    mockApi.ciStatus.mockResolvedValue({ ci_status: null, auto_fix_history: [] });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { container } = render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <TicketPullRequestPanel ticketId="t1" isOpeningPr={false} isCommittingPush={false} />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Could not load this ticket’s pull request");
    expect(alert).toHaveTextContent("Network request failed");
    expect(screen.getByRole("button", { name: "Try again" })).toBeEnabled();
    expect(provenanceLine(container)).toBeNull();
    expect(screen.queryByText(PROVENANCE)).not.toBeInTheDocument();
  });

  it("re-reads the branch when the lookup comes back with a different tree", async () => {
    // A `treeBranch` captured once (a useMemo missing its dependency, a prop
    // read at mount) posts the previous tree's branch after a refresh.
    mockApi.sendTriageMessage.mockResolvedValue(undefined as never);
    renderPanel(TREE_MEMBER);
    const refresh = await screen.findByRole("button", { name: "Refresh" });

    const pending = deferred<TicketPullRequest>();
    mockApi.ticketPullRequest.mockReturnValueOnce(pending.promise as never);
    fireEvent.click(refresh);
    pending.settle(
      lookup({
        branch: "publish/integration/lg-ms-2",
        ships_with: "integration/lg-ms-2",
        is_tree_pull_request: true,
        pull_request: { ...PR, head: "publish/integration/lg-ms-2" },
      }),
    );
    await waitFor(() => expect(screen.getByRole("button", { name: "Refresh" })).toBeEnabled());

    fireEvent.click(screen.getByRole("button", { name: "Ask triage to fix" }));
    await waitFor(() => expect(mockApi.sendTriageMessage).toHaveBeenCalled());
    const message = mockApi.sendTriageMessage.mock.calls[0][1] as string;
    expect(message).toContain("fix it on integration/lg-ms-2 and push.");
    expect(message).not.toContain("integration/lg-ms-1");
  });

  it("retracts the merge, and the agent action, when a refetch says the PR is the tree's", async () => {
    // Every other merge-guard assertion in this file is a fresh mount, so a
    // guard captured at mount (a `useState` initialiser, a `useMemo` missing
    // its dependency) passes all of them. This is the one direction that
    // matters: a tab already offering squash-merge-to-base plus
    // integration-branch deletion, whose refetch comes back saying the resolved
    // PR is the tree's. The second payload keeps `mergeable_now: true` on
    // purpose — on a `PR`-based payload the button would vanish for
    // `!pr.mergeable_now` (MergeAndCleanUpButton.tsx:64) instead of via the
    // guard, and this test would be green under exactly the mutant it exists
    // to kill.
    renderPanel(lookup({ pull_request: READY }));
    await screen.findByRole("button", { name: "Merge and clean up" });
    expect(uiActionRegistry.available()).toContain("ticket.merge_pull_request");

    // After the render: `renderPanel` installs `mockResolvedValue`, which wins
    // over a `mockReturnValueOnce` installed before it.
    const pending = deferred<TicketPullRequest>();
    mockApi.ticketPullRequest.mockReturnValueOnce(pending.promise as never);
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    pending.settle(
      lookup({
        branch: "publish/integration/lg-ms-1",
        ships_with: "integration/lg-ms-1",
        ships_with_root: "lg-ms-1",
        is_tree_pull_request: true,
        pull_request: { ...READY, head: "publish/integration/lg-ms-1" },
      }),
    );

    await waitFor(() => expect(screen.queryByRole("button", { name: "Merge and clean up" })).not.toBeInTheDocument());
    await waitFor(() => expect(uiActionRegistry.available()).not.toContain("ticket.merge_pull_request"));
  });

  it("restores the merge, and the agent action, when a refetch says the PR is the ticket's own", async () => {
    // The mirror of the test above, and the direction nothing else drives. A
    // guard that latches true once a tree PR has been seen (a `useRef` written
    // in render, say) passes every test in this file: each fresh mount agrees
    // with it, the test above only drives false -> true, and the other refetch
    // test goes tree -> tree. A latching guard is fail-safe but it strands the
    // operator — the tab refuses the merge, and the `ticket.merge_pull_request`
    // action, until the page is reloaded.
    //
    // Both payloads keep `mergeable_now: true` on purpose. On a `PR`-based
    // first payload the opening assertions would hold via `!pr.mergeable_now`
    // (MergeAndCleanUpButton.tsx:64) rather than via the guard, and the
    // latching mutant would stay green — the mirror of the vacuity trap the
    // test above closes. That rules out TREE_MEMBER and TREE_ROOT too, since
    // both are built on `PR`.
    const container = renderPanel(
      lookup({
        branch: "publish/integration/lg-ms-1",
        ships_with: "integration/lg-ms-1",
        is_tree_pull_request: true,
        pull_request: { ...READY, head: "publish/integration/lg-ms-1" },
      }),
    );
    // A marker proving that payload has rendered, so the two assertions below
    // cannot pass merely because the lookup is still on the wire.
    await screen.findByRole("link", { name: PR.title });
    await waitFor(() => expect(provenanceLine(container)).not.toBeNull());
    expect(screen.queryByRole("button", { name: "Merge and clean up" })).not.toBeInTheDocument();
    expect(uiActionRegistry.available()).not.toContain("ticket.merge_pull_request");

    // After the render: `renderPanel` installs `mockResolvedValue`, which wins
    // over a `mockReturnValueOnce` installed before it.
    const pending = deferred<TicketPullRequest>();
    mockApi.ticketPullRequest.mockReturnValueOnce(pending.promise as never);
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    pending.settle(lookup({ pull_request: READY }));

    // Both halves: MergeAndCleanUpButton.tsx:62 (the render guard) and :59 (the
    // `useAgentAction` availability predicate) are two independent reads of the
    // same prop, and a capture of either one alone is a mutant the other
    // assertion cannot see.
    await waitFor(() => expect(screen.getByRole("button", { name: "Merge and clean up" })).toBeInTheDocument());
    await waitFor(() => expect(uiActionRegistry.available()).toContain("ticket.merge_pull_request"));
  });
});
