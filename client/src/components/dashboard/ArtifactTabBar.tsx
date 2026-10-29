import { useCallback, useEffect, useRef, useState } from "react";

import { useDismissOnOutside } from "../../hooks/useDismissOnOutside";
import { MORE_ARTIFACT_TABS, PRIMARY_ARTIFACT_TABS, type ArtifactTab } from "../../lib/appNavigation";
import { navigateToTicketTab } from "../../lib/useAppNavigation";
import { Button } from "../ui/Button";
import "./ArtifactPane.css";

const TAB_LABELS: Record<ArtifactTab, string> = {
  diff: "Diff",
  timeline: "Timeline",
  outputs: "Outputs",
  pr: "PR",
  approvals: "Approvals",
  hive: "Hive",
  monitor: "Monitor",
};

const MORE_HINTS: Partial<Record<ArtifactTab, string>> = {
  hive: "The office floor, animated from this ticket's runs",
  monitor: "Workflow findings across every ticket",
};

/**
 * The artifact pane's tab strip.
 *
 * Five tabs, each answering one question about the selected ticket; the two
 * views that are not about reading it (Hive, and the cross-ticket Monitor)
 * sit behind "More" so they stop competing with the ones that are. The More
 * menu lives outside the scrolling strip: an overflow container would clip it.
 *
 * It owns the scroll-into-view of the active tab: the strip scrolls
 * horizontally when the pane is narrow, and a deep link can select a tab that
 * is currently off-screen.
 */
export function ArtifactTabBar({
  artifactTab,
  selectedId,
  hasRunErrors,
  outputCount,
  approvalCount,
  hasPr,
  primaryTabs = PRIMARY_ARTIFACT_TABS,
  moreTabs = MORE_ARTIFACT_TABS,
}: {
  artifactTab: ArtifactTab;
  selectedId: string | null;
  hasRunErrors: boolean;
  outputCount: number;
  approvalCount: number;
  hasPr: boolean;
  /** The tabs in the strip; the ticket page lifts Diff and Hive into its own view tabs. */
  primaryTabs?: readonly ArtifactTab[];
  moreTabs?: readonly ArtifactTab[];
}) {
  const tabRefs = useRef<Partial<Record<string, HTMLButtonElement>>>({});

  useEffect(() => {
    tabRefs.current[artifactTab]?.scrollIntoView?.({ block: "nearest", inline: "center" });
  }, [artifactTab]);

  const go = (tab: ArtifactTab) => {
    if (selectedId) navigateToTicketTab(selectedId, tab);
  };

  return (
    <>
      <div className="tab-bar-scroll ap-tabs" role="tablist" aria-label="Ticket views">
        {primaryTabs.map((tab) => {
          const selected = artifactTab === tab;
          return (
            <Button
              key={tab}
              variant="plain"
              ref={(el) => {
                if (el) tabRefs.current[tab] = el;
              }}
              role="tab"
              aria-selected={selected}
              className={`tab-btn ${selected ? "active" : ""}`}
              disabled={!selectedId}
              onClick={() => go(tab)}
            >
              {TAB_LABELS[tab]}
              {tab === "timeline" && hasRunErrors ? (
                <span className="ap-tab-dot" role="img" aria-label="has errors" />
              ) : null}
              {tab === "outputs" && outputCount > 0 ? <span className="ap-tab-badge">{outputCount}</span> : null}
              {tab === "approvals" && approvalCount > 0 ? (
                <span className="ap-tab-badge ap-tab-badge--alert">{approvalCount}</span>
              ) : null}
              {tab === "pr" && hasPr ? <span className="ap-tab-badge">open</span> : null}
            </Button>
          );
        })}
      </div>
      {moreTabs.length > 0 ? (
        <MoreTabs tabs={moreTabs} artifactTab={artifactTab} disabled={!selectedId} onSelect={go} />
      ) : null}
    </>
  );
}

function MoreTabs({
  tabs,
  artifactTab,
  disabled,
  onSelect,
}: {
  tabs: readonly ArtifactTab[];
  artifactTab: ArtifactTab;
  disabled: boolean;
  onSelect: (tab: ArtifactTab) => void;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const close = useCallback(() => setOpen(false), []);
  useDismissOnOutside(open, rootRef, close);
  const current = tabs.includes(artifactTab) ? artifactTab : null;

  return (
    <div className="ap-more" ref={rootRef}>
      <Button
        variant="plain"
        className={`tab-btn ${current ? "active" : ""}`}
        aria-haspopup="menu"
        aria-expanded={open}
        disabled={disabled}
        onClick={() => setOpen((value) => !value)}
      >
        {current ? TAB_LABELS[current] : "More"} ▾
      </Button>
      {open ? (
        <div className="ap-more-menu" role="menu" aria-label="More ticket views">
          {tabs.map((tab) => (
            <Button
              key={tab}
              variant="plain"
              role="menuitemradio"
              aria-checked={artifactTab === tab}
              className="ap-more-item"
              onClick={() => {
                onSelect(tab);
                close();
              }}
            >
              {TAB_LABELS[tab]}
              <span className="ap-more-item-hint">{MORE_HINTS[tab]}</span>
            </Button>
          ))}
        </div>
      ) : null}
    </div>
  );
}
