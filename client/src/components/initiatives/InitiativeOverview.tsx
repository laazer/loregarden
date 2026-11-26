import type { InitiativeView } from "../../api/client";
import { attentionOf } from "../../lib/initiativeAttention";
import { progressFillStyle } from "../../lib/progressFill";
import { ticketStateChip, ticketStateLabel } from "../../lib/ticketStates";
import { Button } from "../ui/Button";
import { StatusChip } from "../ui/StatusChip";

/**
 * Every initiative on one screen, one row each: how far along it is and what in it
 * needs someone. Choosing a row opens that initiative's card below the table.
 */
export function InitiativeOverview({
  initiatives,
  openId,
  cardId,
  onOpen,
}: {
  /** Already in display order (`sortByAttention`). */
  initiatives: readonly InitiativeView[];
  openId: string;
  /** The open card's element id, for `aria-controls`. */
  cardId: string;
  onOpen: (id: string) => void;
}) {
  return (
    <div className="initiative-table-wrap">
      <table className="initiative-table" aria-label="Initiatives, blocked and in-flight work first">
        <thead>
          <tr>
            <th scope="col">Initiative</th>
            <th scope="col">State</th>
            <th scope="col" className="initiative-num">
              Progress
            </th>
            <th scope="col">Needs you</th>
            <th scope="col">Workspaces</th>
          </tr>
        </thead>
        <tbody>
          {initiatives.map((initiative) => {
            const { resolved, total } = initiative.progress;
            const pct = total === 0 ? 0 : Math.round((resolved / total) * 100);
            const open = initiative.id === openId;
            const attention = attentionOf(initiative)
              .map(([state, n]) => `${n} ${ticketStateLabel(state).toLowerCase()}`)
              .join(" · ");
            return (
              <tr key={initiative.id} aria-current={open ? "true" : undefined} className={open ? "is-open" : undefined}>
                <td data-label="Initiative">
                  <Button
                    variant="plain"
                    className="initiative-table-open"
                    aria-expanded={open}
                    aria-controls={open ? cardId : undefined}
                    onClick={() => onOpen(initiative.id)}
                  >
                    <span className="initiative-mono initiative-muted">{initiative.external_id}</span>
                    <span className="initiative-table-title">{initiative.title}</span>
                  </Button>
                </td>
                <td data-label="State">
                  <StatusChip {...ticketStateChip(initiative.state)} />
                </td>
                <td data-label="Progress" className="initiative-num">
                  <span className="initiative-table-progress">
                    <span
                      className="initiative-progress"
                      role="progressbar"
                      aria-label={`${initiative.title} progress`}
                      aria-valuemin={0}
                      aria-valuemax={100}
                      aria-valuenow={pct}
                    >
                      <span className="initiative-progress-fill" style={progressFillStyle(pct)} />
                    </span>
                    <span>
                      {resolved}/{total}
                    </span>
                  </span>
                </td>
                <td data-label="Needs you" className={attention ? "initiative-group-attention" : "initiative-muted"}>
                  {attention || "—"}
                </td>
                <td data-label="Workspaces" className="initiative-muted">
                  {initiative.workspaces.join(", ") || "—"}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
