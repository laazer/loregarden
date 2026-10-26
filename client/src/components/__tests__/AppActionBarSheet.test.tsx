/**
 * The bar at rest is one row; the per-conversation settings and the quick
 * prompts live in a sheet the composer opens on focus and closes on Escape or
 * when focus leaves it. Nothing is removed from the bar, only moved one focus
 * away.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { useActiveChatSession } from "../../hooks/useActiveChatSession";
import { useTerminalTarget } from "../../hooks/useTerminalTarget";
import { useUiStore } from "../../state/uiStore";
import { AppActionBar } from "../AppActionBar";

jest.mock("../../hooks/useActiveChatSession");
jest.mock("../../hooks/useTerminalTarget");
jest.mock("../../api/client", () => jest.requireActual("../../test/apiClientMock"));

const mockResolver = useActiveChatSession as jest.MockedFunction<typeof useActiveChatSession>;
const mockTerminal = useTerminalTarget as jest.MockedFunction<typeof useTerminalTarget>;

function session() {
  return {
    kind: "baxter-home" as const,
    id: "s1",
    messages: [],
    isBusy: false,
    activeTurnId: null,
    canAct: true,
    isLoading: false,
    error: null,
    loadError: false,
    send: jest.fn().mockResolvedValue({}),
    stop: jest.fn().mockResolvedValue({}),
    isStopping: false,
  };
}

function bind(overrides: Partial<ReturnType<typeof useActiveChatSession>> = {}) {
  return {
    session: session(),
    label: "loregarden",
    ticketId: null,
    pendingApprovals: [],
    branch: null,
    archive: null,
    composedOnScreen: false,
    screenPrompts: null,
    agents: null,
    assistantLabel: "Baxter",
    composerBlocked: null,
    promptKind: "baxter-home",
    ...overrides,
  } as ReturnType<typeof useActiveChatSession>;
}

function renderBar() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <AppActionBar />
        <button type="button">elsewhere</button>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return screen.getByRole("textbox", { name: "Message this conversation" });
}

const sheet = () => screen.queryByRole("group", { name: "Conversation options" });

beforeEach(() => {
  jest.clearAllMocks();
  useUiStore.setState({ copilotOpen: false, copilotHistoryOpen: false, terminalOpen: false });
  mockTerminal.mockReturnValue({ workspaceSlug: "loregarden", agent: "implementer" });
  mockResolver.mockReturnValue(bind());
});

test("at rest the row holds the composer, the conversation's state and the shell switch, and no settings", () => {
  renderBar();

  expect(sheet()).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Auto-approve" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: /Terminal/ })).toBeInTheDocument();
  expect(screen.getByText(/On loregarden/)).toBeInTheDocument();
});

test("focusing the composer opens the sheet with the settings and the quick prompts", () => {
  const composer = renderBar();

  act(() => composer.focus());

  const group = sheet();
  expect(group).toBeInTheDocument();
  expect(within(group as HTMLElement).getByRole("button", { name: "Auto-approve" })).toBeInTheDocument();
  expect(within(group as HTMLElement).getAllByRole("button").length).toBeGreaterThan(1);
});

test("Escape closes the sheet and leaves focus in the composer", () => {
  const composer = renderBar();
  act(() => composer.focus());

  fireEvent.keyDown(composer, { key: "Escape" });

  expect(sheet()).not.toBeInTheDocument();
  expect(composer).toHaveFocus();
});

test("Tab into the sheet keeps it open; focus leaving it closes it", () => {
  const composer = renderBar();
  act(() => composer.focus());
  const auto = screen.getByRole("button", { name: "Auto-approve" });

  act(() => auto.focus());
  expect(sheet()).toBeInTheDocument();

  act(() => screen.getByRole("button", { name: "elsewhere" }).focus());
  expect(sheet()).not.toBeInTheDocument();
});

test("a pointer press in the sheet keeps focus in the composer, so the sheet stays under the click", () => {
  const composer = renderBar();
  act(() => composer.focus());

  const pressed = fireEvent.mouseDown(screen.getByRole("button", { name: "Auto-approve" }));

  // `fireEvent` returns false when a handler called preventDefault.
  expect(pressed).toBe(false);
  expect(composer).toHaveFocus();
});

test("with no conversation to set, focusing the composer opens nothing", () => {
  mockResolver.mockReturnValue(bind({ session: null, label: "", promptKind: "" }));
  renderBar();

  // The composer is disabled with no conversation; nothing to open either way.
  expect(sheet()).not.toBeInTheDocument();
});

test("⌘J or Ctrl+J reaches the composer from anywhere", () => {
  const composer = renderBar();

  act(() => {
    fireEvent.keyDown(window, { key: "j", metaKey: true });
  });
  expect(composer).toHaveFocus();

  act(() => screen.getByRole("button", { name: "elsewhere" }).focus());
  act(() => {
    fireEvent.keyDown(window, { key: "J", ctrlKey: true });
  });
  expect(composer).toHaveFocus();
});

test("⌘J is left to the code editor while it has focus", () => {
  const composer = renderBar();
  const editor = document.createElement("div");
  editor.className = "monaco-editor";
  const field = document.createElement("textarea");
  editor.appendChild(field);
  document.body.appendChild(editor);
  field.focus();

  fireEvent.keyDown(field, { key: "j", metaKey: true });

  expect(field).toHaveFocus();
  expect(composer).not.toHaveFocus();
  editor.remove();
});

test("Escape with the / menu open closes the menu, and leaves the sheet open", () => {
  const composer = renderBar();
  act(() => composer.focus());
  fireEvent.change(composer, { target: { value: "/" } });
  expect(screen.getByRole("listbox")).toBeInTheDocument();

  fireEvent.keyDown(composer, { key: "Escape" });

  expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  expect(sheet()).toBeInTheDocument();
});
