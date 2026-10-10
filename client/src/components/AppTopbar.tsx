import { AppTopbarActions } from "./AppTopbarActions";
import { useChatWorkbench } from "../hooks/useChatWorkbench";
import { useChatScope } from "../hooks/useChatScope";
import { GLOBAL_CHAT_SCOPE, initiativeChatScope } from "../lib/chatScope";
import { useAppPage } from "../lib/useAppNavigation";
import { useUiStore } from "../state/uiStore";
import { TopbarPageSlot } from "./TopbarPageSlot";
import { Button } from "./ui/Button";
import { Select } from "./ui/Select";

const PAGE_SUBTITLES: Partial<Record<string, string>> = {
  dashboard: "Agent SDLC · Console",
  chat: "Agent SDLC · Chat",
  home: "Agent SDLC · Home",
};

/** Beside History: the chat's other panel, toggled from the same place. */
function ChatWorkbenchToggle() {
  const { open, setOpen } = useChatWorkbench();
  return (
    <Button
      variant="secondary"
      className="topbar-action-btn topbar-action-btn--collapsible"
      aria-pressed={open}
      aria-label="Workbench"
      title={open ? "Hide the workbench" : "Show the workbench"}
      onClick={() => setOpen(!open)}
    >
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" aria-hidden>
        <rect x="3" y="4" width="18" height="16" rx="2" />
        <path d="M15 4v16" />
      </svg>
      <span className="topbar-action-label">Workbench</span>
    </Button>
  );
}

/**
 * What chat answers about: one workspace, or every workspace at once.
 *
 * The global options exist for initiatives, whose milestones span workspaces.
 * "All workspaces" is an unfocused global chat; picking an initiative focuses
 * one on that initiative's plan. Each option is its own archive of threads.
 */
function ChatScopePicker() {
  const { scope, setScope, workspaces, initiatives, initiativesLoaded, initiativesError } =
    useChatScope();

  return (
    <label className="topbar-workspace-picker topbar-workspace-picker--fixed">
      <span className="topbar-workspace-picker-label">Scope</span>
      <Select
        className="btn-secondary topbar-workspace-picker-select"
        value={scope}
        disabled={!workspaces.length}
        aria-label="Chat scope"
        onChange={(event) => setScope(event.target.value)}
      >
        {workspaces.length ? null : <option value="">No workspaces</option>}
        <optgroup label="Workspace">
          {workspaces.map((ws) => (
            <option key={ws.slug} value={ws.slug}>
              {ws.name}
            </option>
          ))}
        </optgroup>
        <optgroup label="Global">
          <option value={GLOBAL_CHAT_SCOPE}>All workspaces</option>
          {initiatives.map((initiative) => (
            <option key={initiative.id} value={initiativeChatScope(initiative.id)}>
              {`Initiative: ${initiative.external_id} · ${initiative.title}`}
            </option>
          ))}
          {!initiativesLoaded ? (
            <option value="" disabled>
              Loading initiatives…
            </option>
          ) : null}
          {initiativesError ? (
            <option value="" disabled>
              Couldn't load initiatives
            </option>
          ) : null}
        </optgroup>
      </Select>
    </label>
  );
}

export function AppTopbar() {
  const appPage = useAppPage();
  const search = useUiStore((s) => s.search);
  const setSearch = useUiStore((s) => s.setSearch);
  const requestBaxterChatReset = useUiStore((s) => s.requestBaxterChatReset);
  const historyOpen = useUiStore((s) => s.baxterHistoryOpen);
  const toggleBaxterHistory = useUiStore((s) => s.toggleBaxterHistory);
  const isConsole = appPage === "dashboard";
  const isChat = appPage === "chat";
  const subtitle = PAGE_SUBTITLES[appPage] ?? "Agent SDLC · Console";

  return (
    <header className="topbar app-topbar">
      <div className="ide-topbar-brand">
        <div className="brand-title">loregarden</div>
        <div className="brand-sub">{subtitle}</div>
      </div>
      {isConsole ? (
        <label className="topbar-search">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
            <circle cx="11" cy="11" r="7" />
            <path d="m20 20-3.5-3.5" />
          </svg>
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search tickets, agents, runs…"
            aria-label="Search tickets"
          />
          <kbd>⌘K</kbd>
        </label>
      ) : null}
      <TopbarPageSlot />
      <div className="topbar-spacer" />
      {isChat ? (
        <>
          <ChatScopePicker />
          <button
            type="button"
            className="btn-secondary topbar-action-btn topbar-action-btn--collapsible"
            aria-pressed={historyOpen}
            aria-label="History"
            title="History"
            onClick={() => toggleBaxterHistory()}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" aria-hidden>
              <path d="M3 12a9 9 0 1 0 3-6.7L3 8" />
              <path d="M3 3v5h5M12 7v5l3 2" />
            </svg>
            <span className="topbar-action-label">History</span>
          </button>
          <ChatWorkbenchToggle />
          <button
            type="button"
            className="btn-secondary topbar-action-btn topbar-action-btn--collapsible"
            aria-label="New chat"
            title="New chat"
            onClick={() => requestBaxterChatReset()}
          >
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
              <path d="M12 5v14M5 12h14" />
            </svg>
            <span className="topbar-action-label">New chat</span>
          </button>
        </>
      ) : null}
      <AppTopbarActions />
    </header>
  );
}
