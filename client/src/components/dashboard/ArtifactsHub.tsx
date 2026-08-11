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
  created_at?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
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
        className="artifacts-subtabs"
        role="tablist"
        aria-label="Artifact sections"
      >
        {ARTIFACTS_SUB_TABS.map((tab) => {
          const selected = subTab === tab;
          return (
            <button
              key={tab}
              type="button"
              role="tab"
              aria-selected={selected}
              className={`artifacts-subtab${selected ? " active" : ""}`}
              onClick={() => navigateToTicketTab(ticket.id, tab)}
              style={tab === "errors" && hasRunErrors && !selected ? { color: "var(--rdl)" } : undefined}
            >
              <span>{ARTIFACTS_SUB_TAB_LABELS[tab]}</span>
              {tab === "errors" && hasRunErrors ? (
                <span className="artifacts-subtab-dot" aria-hidden />
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
