/**
 * One record's page in Studio (an agent, a workflow): a header that names it,
 * says how it has fared and holds its actions, then one tab of detail at a
 * time. It replaced a three-column layout (a list rail, every form card at
 * once, a live preview) that showed everything and answered nothing first; the
 * index page is the list now, one link back.
 */

import { useId, useRef, type KeyboardEvent, type ReactNode } from "react";
import { Link } from "react-router-dom";

import { Button } from "../ui/Button";
import "./StudioEditorShell.css";

export interface EditorTab<T extends string> {
  id: T;
  label: string;
}

interface Props<T extends string> {
  backTo: string;
  backLabel: string;
  title: string;
  /** The record's id (slug), shown after its name. */
  slug?: string;
  /** Short tags after the slug: "built-in", "custom", "unsaved changes". */
  tags?: ReactNode;
  /** The one line under the title that says how this record has fared. */
  summary?: ReactNode;
  actions?: ReactNode;
  tabs: EditorTab<T>[];
  activeTab: T;
  onTab: (tab: T) => void;
  children: ReactNode;
}

export function StudioEditorShell<T extends string>({
  backTo,
  backLabel,
  title,
  slug,
  tags,
  summary,
  actions,
  tabs,
  activeTab,
  onTab,
  children,
}: Props<T>) {
  const baseId = useId();
  const tabRefs = useRef(new Map<T, HTMLButtonElement>());
  const tabId = (tab: T) => `${baseId}-tab-${tab}`;
  const panelId = `${baseId}-panel`;

  // Arrow keys move between tabs, as a tablist does (WAI-ARIA, automatic activation).
  const onTabKey = (event: KeyboardEvent<HTMLDivElement>) => {
    const step = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
    if (!step) return;
    event.preventDefault();
    const index = tabs.findIndex((tab) => tab.id === activeTab);
    const next = tabs[(index + step + tabs.length) % tabs.length];
    onTab(next.id);
    tabRefs.current.get(next.id)?.focus();
  };

  return (
    <section className="studio-record" aria-labelledby={`${baseId}-title`}>
      <header className="studio-record-head">
        <Link className="studio-record-back" to={backTo}>
          ← {backLabel}
        </Link>
        <div className="studio-record-title-row">
          <h2 id={`${baseId}-title`} className="studio-record-title">
            {title}
          </h2>
          {slug ? <span className="studio-record-slug">{slug}</span> : null}
          {tags}
          {actions ? <div className="studio-record-actions">{actions}</div> : null}
        </div>
        {summary ? <div className="studio-record-summary">{summary}</div> : null}
      </header>

      <div className="studio-record-tabs" role="tablist" aria-label={`${title} sections`} onKeyDown={onTabKey}>
        {tabs.map((tab) => (
          <Button
            variant="plain"
            key={tab.id}
            ref={(node) => {
              if (node) tabRefs.current.set(tab.id, node);
              else tabRefs.current.delete(tab.id);
            }}
            role="tab"
            id={tabId(tab.id)}
            aria-selected={tab.id === activeTab}
            aria-controls={panelId}
            tabIndex={tab.id === activeTab ? 0 : -1}
            className={`studio-record-tab${tab.id === activeTab ? " is-active" : ""}`}
            onClick={() => onTab(tab.id)}
          >
            {tab.label}
          </Button>
        ))}
      </div>

      <div className="studio-record-panel" role="tabpanel" id={panelId} aria-labelledby={tabId(activeTab)}>
        {children}
      </div>
    </section>
  );
}

/** A tag after a record's slug. */
export function RecordTag({ tone = "neutral", children }: { tone?: "neutral" | "warn"; children: ReactNode }) {
  return <span className={`studio-record-tag studio-record-tag--${tone}`}>{children}</span>;
}
