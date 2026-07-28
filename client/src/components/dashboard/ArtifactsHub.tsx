import type { TicketDetail } from "../../api/client";
import {
  ARTIFACTS_SUB_TAB_LABELS,
  ARTIFACTS_SUB_TABS,
  type ArtifactsSubTab,
} from "../../lib/appNavigation";
import { navigateToTicketTab } from "../../lib/useAppNavigation";
import { ArtifactView } from "./ArtifactView";
import { ArtifactsPanel } from "./ArtifactsPanel";

type RunRow = {
  id: string;
  run_code: string;
  status: string;
  command: string;
  agent_id?: string;
  stage_key?: string;
  stderr?: string;
  stdout?: string;
};

/**
 * Artifacts top tab → Feed / Errors / Context / Ledger.
 * Deep links to /errors|/context|/ledger still work; the parent tab stays selected.
 */
export function ArtifactsHub({
  ticket,
  subTab,
  runs = [],
  isActive,
  hasRunErrors,
  onOpenRunLog,
}: {
  ticket: TicketDetail;
  subTab: ArtifactsSubTab;
  runs?: RunRow[];
  isActive?: boolean;
  hasRunErrors?: boolean;
  onOpenRunLog?: (runId: string) => void;
}) {
  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 0 }}>
      <div
        className="studio-subtabs tab-bar-scroll"
        role="tablist"
        aria-label="Artifact sections"
        style={{ padding: "8px 16px" }}
      >
        {ARTIFACTS_SUB_TABS.map((tab) => {
          const selected = subTab === tab;
          return (
            <button
              key={tab}
              type="button"
              role="tab"
              aria-selected={selected}
              className={`studio-subtab${selected ? " active" : ""}`}
              onClick={() => navigateToTicketTab(ticket.id, tab)}
              style={tab === "errors" && hasRunErrors ? { color: "var(--rdl)" } : undefined}
            >
              {ARTIFACTS_SUB_TAB_LABELS[tab]}
              {tab === "errors" && hasRunErrors ? (
                <span
                  style={{
                    marginLeft: 6,
                    minWidth: 8,
                    height: 8,
                    borderRadius: "50%",
                    background: "var(--red)",
                    display: "inline-block",
                  }}
                />
              ) : null}
            </button>
          );
        })}
      </div>
      <div style={{ flex: 1, overflow: "auto", minHeight: 0 }}>
        {subTab === "artifacts" ? (
          <ArtifactsPanel ticketId={ticket.id} isActive={isActive} onOpenRunLog={onOpenRunLog} />
        ) : (
          <ArtifactView tab={subTab} ticket={ticket} runs={runs} onOpenRunLog={onOpenRunLog} />
        )}
      </div>
    </div>
  );
}
