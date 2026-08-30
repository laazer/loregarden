import { useEffect, useRef, useState } from "react";

import { TerminalPanel } from "./TerminalPanel";
import "./TerminalWorkspace.css";
import { AddToTabMenu } from "./AddToTabMenu";

interface TerminalWorkspaceProps {
  workspaceSlug: string;
  visible: boolean;
  onEmpty: () => void;
}

interface TerminalTab {
  id: number;
  label: string;
  panes: number[];
  activePaneId: number;
}

let nextTabId = 1;
let nextPaneId = 1;

function createTab(labelNumber: number): TerminalTab {
  const id = nextTabId++;
  const paneId = nextPaneId++;
  return { id, label: `Terminal ${labelNumber}`, panes: [paneId], activePaneId: paneId };
}

/**
 * Owns the independent shell processes shown as tabs and split panes.
 *
 * Inactive tabs stay mounted so changing tabs never resets cwd, jobs, or
 * scrollback. Closing a tab or pane is the explicit action that reaps its shell.
 */
export function TerminalWorkspace({
  workspaceSlug,
  visible,
  onEmpty,
}: TerminalWorkspaceProps) {
  const [tabs, setTabs] = useState<TerminalTab[]>(() => [createTab(1)]);
  const [activeTabId, setActiveTabId] = useState(() => tabs[0].id);
  const wasVisible = useRef(visible);
  const nextLabelNumber = useRef(2);
  const tabRefs = useRef(new Map<number, HTMLDivElement>());

  /**
   * The tab strip scrolls horizontally rather than wrapping (see
   * TerminalWorkspace.css), and its scrollbar is hidden — so a new or
   * newly-active tab past the visible edge is not just off-screen, it is
   * invisible with no affordance that it exists. Every activation carries the
   * strip to it.
   */
  useEffect(() => {
    tabRefs.current.get(activeTabId)?.scrollIntoView?.({ block: "nearest", inline: "nearest" });
  }, [activeTabId]);

  useEffect(() => {
    const opening = visible && !wasVisible.current;
    wasVisible.current = visible;
    if (!opening || tabs.length > 0) return;
    const tab = createTab(nextLabelNumber.current++);
    setTabs([tab]);
    setActiveTabId(tab.id);
  }, [tabs.length, visible]);

  const addTab = () => {
    const tab = createTab(nextLabelNumber.current++);
    setTabs((current) => [...current, tab]);
    setActiveTabId(tab.id);
  };

  const closeTab = (tabId: number) => {
    const remaining = tabs.filter((tab) => tab.id !== tabId);
    setTabs(remaining);
    if (remaining.length === 0) {
      onEmpty();
      return;
    }
    if (activeTabId === tabId) {
      setActiveTabId(remaining[remaining.length - 1].id);
    }
  };

  const splitActiveTab = () => {
    const paneId = nextPaneId++;
    setTabs((current) =>
      current.map((tab) =>
        tab.id === activeTabId
          ? { ...tab, panes: [...tab.panes, paneId], activePaneId: paneId }
          : tab,
      ),
    );
  };

  const closePane = (tabId: number, paneId: number) => {
    const tab = tabs.find((candidate) => candidate.id === tabId);
    if (!tab) return;
    if (tab.panes.length === 1) {
      closeTab(tabId);
      return;
    }

    const panes = tab.panes.filter((candidate) => candidate !== paneId);
    setTabs((current) =>
      current.map((candidate) =>
        candidate.id === tabId
          ? {
              ...candidate,
              panes,
              activePaneId:
                candidate.activePaneId === paneId
                  ? panes[Math.max(0, tab.panes.indexOf(paneId) - 1)]
                  : candidate.activePaneId,
            }
          : candidate,
      ),
    );
  };

  const setActivePane = (tabId: number, paneId: number) => {
    setActiveTabId(tabId);
    setTabs((current) =>
      current.map((tab) => (tab.id === tabId ? { ...tab, activePaneId: paneId } : tab)),
    );
  };

  return (
    <section className="terminal-workspace" aria-label={`Terminals for ${workspaceSlug}`}>
      <header className="terminal-workspace-toolbar">
        <div className="terminal-tabs" role="tablist" aria-label="Terminal tabs">
          {tabs.map((tab) => {
            const active = tab.id === activeTabId;
            return (
              <div
                className={`terminal-tab${active ? " is-active" : ""}`}
                key={tab.id}
                ref={(el) => {
                  if (el) tabRefs.current.set(tab.id, el);
                  else tabRefs.current.delete(tab.id);
                }}
              >
                <button
                  type="button"
                  className="terminal-tab-select"
                  role="tab"
                  aria-selected={active}
                  aria-controls={`terminal-tabpanel-${tab.id}`}
                  id={`terminal-tab-${tab.id}`}
                  onClick={() => setActiveTabId(tab.id)}
                >
                  <TerminalIcon />
                  <span>{tab.label}</span>
                  {tab.panes.length > 1 && (
                    <span className="terminal-tab-count" aria-label={`${tab.panes.length} panes`}>
                      {tab.panes.length}
                    </span>
                  )}
                </button>
                <button
                  type="button"
                  className="terminal-tab-close"
                  aria-label={`Close ${tab.label}`}
                  title={`Close ${tab.label}`}
                  onClick={() => closeTab(tab.id)}
                >
                  <CloseIcon />
                </button>
              </div>
            );
          })}
        </div>

        <div className="terminal-toolbar-actions">
          <span
            className="terminal-workspace-safety"
            title="No sandbox: these shells have the same privileges as the control plane."
          >
            {workspaceSlug}
          </span>
          <button
            type="button"
            className="terminal-toolbar-button"
            aria-label="New terminal"
            title="New terminal"
            onClick={addTab}
          >
            <PlusIcon />
          </button>
          <button
            type="button"
            className="terminal-toolbar-button"
            aria-label="Split terminal"
            title="Split terminal"
            disabled={tabs.length === 0}
            onClick={splitActiveTab}
          >
            <SplitIcon />
          </button>
          {/* A terminal pane opens a shell in one workspace, which is the one
              thing this toolbar has and the pane's settings otherwise ask an
              operator to type. */}
          <AddToTabMenu
            primitiveId="terminal"
            values={new Map([["workspace_slug", workspaceSlug]])}
            title={`${workspaceSlug} shell`}
            label="Add a shell for this workspace to a tab"
          />
        </div>
      </header>

      <div className="terminal-tabpanels">
        {tabs.map((tab) => {
          const active = tab.id === activeTabId;
          return (
            <div
              className={`terminal-tabpanel${active ? " is-active" : ""}`}
              id={`terminal-tabpanel-${tab.id}`}
              role="tabpanel"
              aria-labelledby={`terminal-tab-${tab.id}`}
              hidden={!active}
              key={tab.id}
            >
              {tab.panes.map((paneId) => (
                <div
                  className={`terminal-split-pane${
                    tab.activePaneId === paneId ? " is-active" : ""
                  }`}
                  key={paneId}
                  onFocusCapture={() => setActivePane(tab.id, paneId)}
                  onMouseDown={() => setActivePane(tab.id, paneId)}
                >
                  <TerminalPanel workspaceSlug={workspaceSlug} />
                  <button
                    type="button"
                    className="terminal-pane-close"
                    aria-label={`Close pane in ${tab.label}`}
                    title="Close terminal pane"
                    onClick={() => closePane(tab.id, paneId)}
                  >
                    <CloseIcon />
                  </button>
                </div>
              ))}
            </div>
          );
        })}
      </div>
    </section>
  );
}

function TerminalIcon() {
  return (
    <svg viewBox="0 0 16 16" aria-hidden>
      <path d="m3.2 4.25 3.15 3.1-3.15 3.1M7.7 10.5h4.6" />
    </svg>
  );
}

function PlusIcon() {
  return (
    <svg viewBox="0 0 16 16" aria-hidden>
      <path d="M8 3v10M3 8h10" />
    </svg>
  );
}

function SplitIcon() {
  return (
    <svg viewBox="0 0 16 16" aria-hidden>
      <rect x="2.5" y="3" width="11" height="10" rx="1.25" />
      <path d="M8 3v10M10.5 6h1.5M11.25 5.25v1.5" />
    </svg>
  );
}

function CloseIcon() {
  return (
    <svg viewBox="0 0 16 16" aria-hidden>
      <path d="m4.5 4.5 7 7m0-7-7 7" />
    </svg>
  );
}
