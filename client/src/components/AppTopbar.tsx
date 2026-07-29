import { AppTopbarActions } from "./AppTopbarActions";
import { useChatWorkspace } from "../hooks/useChatWorkspace";
import { useAppPage } from "../lib/useAppNavigation";
import { useUiStore } from "../state/uiStore";

const PAGE_SUBTITLES: Partial<Record<string, string>> = {
  dashboard: "Agent SDLC · Console",
  chat: "Agent SDLC · Chat",
  home: "Agent SDLC · Home",
};

/** Chat answers from one workspace, so the topbar names which one. */
function ChatWorkspacePicker() {
  const { slug, setSlug, workspaces } = useChatWorkspace();

  return (
    <label className="topbar-workspace-picker">
      <span className="topbar-workspace-picker-label">Workspace</span>
      <select
        className="btn-secondary topbar-workspace-picker-select"
        value={slug}
        disabled={!workspaces.length}
        aria-label="Chat workspace"
        onChange={(event) => setSlug(event.target.value)}
      >
        {workspaces.length ? null : <option value="">No workspaces</option>}
        {workspaces.map((ws) => (
          <option key={ws.slug} value={ws.slug}>
            {ws.name}
          </option>
        ))}
      </select>
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
      <div className="topbar-spacer" />
      {isChat ? (
        <>
          <ChatWorkspacePicker />
          <button
            type="button"
            className="btn-secondary topbar-action-btn"
            aria-pressed={historyOpen}
            onClick={() => toggleBaxterHistory()}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" aria-hidden>
              <path d="M3 12a9 9 0 1 0 3-6.7L3 8" />
              <path d="M3 3v5h5M12 7v5l3 2" />
            </svg>
            History
          </button>
          <button
            type="button"
            className="btn-secondary topbar-action-btn"
            onClick={() => requestBaxterChatReset()}
          >
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
              <path d="M12 5v14M5 12h14" />
            </svg>
            New chat
          </button>
        </>
      ) : null}
      <AppTopbarActions />
    </header>
  );
}
