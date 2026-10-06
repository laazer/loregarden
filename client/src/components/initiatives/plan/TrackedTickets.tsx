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

function TicketButton({ item }: { item: InitiativeMilestone }) {
  return (
    <Button variant="plain" className="plan-link" title={`Open ${item.external_id}`} onClick={() => navigateToTicket(item.id)}>
      <span className="plan-mono">{item.external_id}</span> {item.title}
    </Button>
  );
}

/**
 * Tickets this initiative tracks without owning them.
 *
 * Answers "what work outside this initiative's milestones is in its plan, and
 * where does it live?". A member keeps its parent, milestone, workspace and the
 * integration branch it lands on; it only joins this plan, with its subtree.
 * The actions: add an existing ticket of any type but an initiative, open one,
 * or remove one — which leaves the ticket itself untouched.
 */
export function TrackedTickets({ initiativeId, onChanged }: { initiativeId: string; onChanged: () => void }) {
  const qc = useQueryClient();
  const queryKey = ["initiatives", initiativeId, "view"] as const;
  const [text, setText] = useState("");
  const search = useDebounced(text.trim(), 250);

  const view = useQuery({
    queryKey,
    queryFn: () => api.initiative(initiativeId),
    // Rendered in the panel, with a retry.
    meta: { suppressErrorToast: true },
  });
  const candidates = useQuery({
    queryKey: ["initiatives", initiativeId, "member-candidates", search],
    queryFn: () => api.initiativeMemberCandidates(initiativeId, search),
    enabled: search.length >= 2,
    // The failure renders under the field, where the operator is typing.
    meta: { suppressErrorToast: true },
  });

  const settle = () => {
    void qc.invalidateQueries({ queryKey: ["initiatives", initiativeId, "member-candidates"] });
    onChanged();
  };
  const add = useMutation({
    meta: { errorTitle: "Add a ticket to this initiative" },
    mutationFn: (ticketId: string) => api.addInitiativeMember(initiativeId, ticketId),
    onSuccess: (next) => {
      qc.setQueryData(queryKey, next);
      setText("");
      settle();
    },
  });
  const remove = useMutation({
    meta: { errorTitle: "Remove a ticket from this initiative" },
    mutationFn: (ticketId: string) => api.removeInitiativeMember(initiativeId, ticketId),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey });
      settle();
    },
  });
  const busy = add.isPending || remove.isPending;
  const members = (view.data?.milestones ?? []).filter((m) => m.member);
  const rows = candidates.data ?? [];
  const inputId = `plan-member-search-${initiativeId}`;

  return (
    <section className="plan-autopilot" aria-labelledby="plan-members-title" aria-busy={view.isPending}>
      <div>
        <h2 id="plan-members-title" className="plan-section-title">
          Tracked tickets{members.length > 0 ? ` (${members.length})` : ""}
        </h2>
        <p className="plan-muted">
          Work this initiative plans without owning it. Each keeps its milestone, workspace and the branch it lands on,
          and brings everything under it into the plan.
        </p>
      </div>

      {view.isError ? (
        <p className="plan-alert" role="alert">
          The tracked tickets could not be loaded: {describeError(view.error)}{" "}
          <Button variant="plain" className="plan-inline-btn" onClick={() => void view.refetch()}>
            Retry
          </Button>
        </p>
      ) : view.isPending ? null : members.length === 0 ? (
        <p className="plan-muted">
          None yet. Add a ticket below to plan it here — a feature from another milestone, a capability, a task — without
          moving it.
        </p>
      ) : (
        <ul className="plan-list" aria-label="Tracked tickets">
          {members.map((m) => (
            <li key={m.id}>
              <TicketButton item={m} />
              <span className="plan-muted"> · {whereItLives(m)}</span>
              <Button
                variant="plain"
                className="plan-inline-btn"
                aria-label={`Stop tracking ${m.external_id} in this initiative`}
                disabled={busy}
                onClick={() => remove.mutate(m.id)}
              >
                {remove.isPending && remove.variables === m.id ? "Removing…" : "Remove"}
              </Button>
            </li>
          ))}
        </ul>
      )}

      <div>
        <label htmlFor={inputId} className="plan-subtitle">
          Add a ticket
        </label>
        <Input
          id={inputId}
          type="search"
          className="btn-secondary filter-select plan-member-search"
          value={text}
          placeholder="Search any workspace by title or id"
          onChange={(e) => setText(e.target.value)}
        />
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
                <TicketButton item={t} />
                <span className="plan-muted"> · {whereItLives(t)}</span>
                <Button
                  variant="plain"
                  className="plan-inline-btn"
                  aria-label={`Track ${t.external_id} in this initiative`}
                  disabled={busy}
                  onClick={() => add.mutate(t.id)}
                >
                  {add.isPending && add.variables === t.id ? "Adding…" : "Add"}
                </Button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}
