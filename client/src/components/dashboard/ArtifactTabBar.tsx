import { useEffect, useRef } from "react";

import { isArtifactsSubTab, PRIMARY_ARTIFACT_TABS, type ArtifactTab } from "../../lib/appNavigation";
import { navigateToTicketTab } from "../../lib/useAppNavigation";

function tabLabel(tab: ArtifactTab): string {
  if (tab === "pr") return "PR";
  return tab.charAt(0).toUpperCase() + tab.slice(1);
}

function Dot({ color }: { color: string }) {
  return (
    <span
      style={{
        marginLeft: 6,
        minWidth: 8,
        height: 8,
        borderRadius: "50%",
        background: color,
        display: "inline-block",
      }}
    />
  );
}

function CountPill({ count }: { count: number }) {
  return (
    <span className="count-pill" style={{ marginLeft: 6, fontSize: 9 }}>
      {count}
    </span>
  );
}

/**
 * The artifact pane's tab strip.
 *
 * It owns the scroll-into-view of the active tab: the strip scrolls
 * horizontally when the pane is narrow, and a deep link can select a tab that
 * is currently off-screen.
 */
export function ArtifactTabBar({
  artifactTab,
  selectedId,
  hasRunErrors,
  artifactCount,
  approvalCount,
  hasPr,
}: {
  artifactTab: ArtifactTab;
  selectedId: string | null;
  hasRunErrors: boolean;
  artifactCount: number;
  approvalCount: number;
  hasPr: boolean;
}) {
  const tabRefs = useRef<Partial<Record<string, HTMLButtonElement>>>({});

  useEffect(() => {
    tabRefs.current[isArtifactsSubTab(artifactTab) ? "artifacts" : artifactTab]?.scrollIntoView?.({
      block: "nearest",
      inline: "center",
    });
  }, [artifactTab]);

  return (
    <div className="tab-bar-scroll" role="tablist" aria-label="Artifact views">
      {PRIMARY_ARTIFACT_TABS.map((t) => {
        const selected = t === "artifacts" ? isArtifactsSubTab(artifactTab) : artifactTab === t;
        return (
          <button
            key={t}
            ref={(el) => {
              if (el) tabRefs.current[t] = el;
            }}
            role="tab"
            aria-selected={selected}
            className={`tab-btn ${selected ? "active" : ""}`}
            onClick={() => selectedId && navigateToTicketTab(selectedId, t)}
            style={t === "artifacts" && hasRunErrors ? { color: "var(--rdl)" } : undefined}
          >
            {tabLabel(t)}
            {t === "artifacts" && hasRunErrors && <Dot color="var(--red)" />}
            {t === "artifacts" && artifactCount > 0 && <CountPill count={artifactCount} />}
            {t === "approvals" && approvalCount > 0 && <CountPill count={approvalCount} />}
            {t === "pr" && hasPr && <Dot color="var(--ac2)" />}
          </button>
        );
      })}
    </div>
  );
}
