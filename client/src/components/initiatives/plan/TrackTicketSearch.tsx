import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../../../api/client";
import type { InitiativeMilestone } from "../../../api/initiativeApi";
import { useDebounced } from "../../../hooks/useDebounced";
import { navigateToTicket } from "../../../lib/useAppNavigation";
import { describeError } from "../../../state/toastStore";
import { Button } from "../../ui/Button";
import { Input } from "../../ui/Input";

/** Where a ticket lives: its milestone (if any) and its workspace. */
function whereItLives(item: InitiativeMilestone): string {
  return [item.work_item_type, item.home_milestone ? `in ${item.home_milestone}` : "", item.workspace_slug]
    .filter(Boolean)
    .join(" · ");
}

/**
 * Adds a ticket the initiative tracks without owning it.
 *
 * Answers "what else should this plan cover?". A tracked ticket keeps its
 * parent, milestone, workspace and the integration branch it lands on; it only
 * joins the schedule above as a phase, with its subtree. Any type but an
 * initiative can be tracked; the schedule row stops tracking it.
 */
export function TrackTicketSearch({ initiativeId, onChanged }: { initiativeId: string; onChanged: () => void }) {
  const qc = useQueryClient();
  const [text, setText] = useState("");
  const search = useDebounced(text.trim(), 250);

  const candidates = useQuery({
    queryKey: ["initiatives", initiativeId, "member-candidates", search],
    queryFn: () => api.initiativeMemberCandidates(initiativeId, search),
    enabled: search.length >= 2,
    // The failure renders under the field, where the operator is typing.
    meta: { suppressErrorToast: true },
  });

  const add = useMutation({
    meta: { errorTitle: "Add a ticket to this initiative" },
    mutationFn: (ticketId: string) => api.addInitiativeMember(initiativeId, ticketId),
    onSuccess: () => {
      setText("");
      void qc.invalidateQueries({ queryKey: ["initiatives", initiativeId, "member-candidates"] });
      onChanged();
    },
  });
  const rows = candidates.data ?? [];
  const inputId = `plan-member-search-${initiativeId}`;

  return (
    <section className="plan-members">
      <div>
        <label htmlFor={inputId} className="plan-subtitle">
          Track a ticket
        </label>
        <p className="plan-muted">
          Plan work this initiative does not own — a feature from another milestone, a capability, a task. It joins the
          schedule as a phase and keeps its milestone, workspace and the branch it lands on.
        </p>
        <Input
          id={inputId}
          type="search"
          className="btn-secondary filter-select plan-member-search"
          value={text}
          placeholder="Search any workspace by title or id"
          onChange={(e) => setText(e.target.value)}
        />
      </div>
      {search.length < 2 ? null : candidates.isPending ? (
        <p className="plan-hint" aria-busy="true">
          Searching…
        </p>
      ) : candidates.isError ? (
        <p className="plan-hint" role="alert">
          Search failed: {describeError(candidates.error)}
        </p>
      ) : rows.length === 0 ? (
        <p className="plan-hint">
          Nothing matches “{search}” that this initiative does not already cover. Initiatives cannot be added.
        </p>
      ) : (
        <ul className="plan-list" aria-label="Tickets to add">
          {rows.map((t) => (
            <li key={t.id}>
              <Button
                variant="plain"
                className="plan-link"
                title={`Open ${t.external_id}`}
                onClick={() => navigateToTicket(t.id)}
              >
                <span className="plan-mono">{t.external_id}</span> {t.title}
              </Button>
              <span className="plan-muted"> · {whereItLives(t)}</span>
              <Button
                variant="plain"
                className="plan-inline-btn"
                aria-label={`Track ${t.external_id} in this initiative`}
                disabled={add.isPending}
                onClick={() => add.mutate(t.id)}
              >
                {add.isPending && add.variables === t.id ? "Adding…" : "Add"}
              </Button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
