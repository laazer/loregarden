import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../../api/client";
import type { TicketDetail } from "../../api/client";
import type { TicketDependencyRef, WaitingOnRef } from "../../api/types";
import { navigateToTicket } from "../../lib/useAppNavigation";
import { notifyIfQueued } from "../../lib/notifyIfQueued";
import { ReasonConfirmDialog } from "../memory/ReasonConfirmDialog";
import { Button } from "../ui/Button";
import { WaitingOnLinks } from "./WaitingOnLinks";

/** A prerequisite that still holds this ticket: unmet and not started past. */
function holding(dependency: TicketDependencyRef): boolean {
  return Boolean(dependency.unmet_reason) && !dependency.overridden;
}

/** `refs` with the ones in `first` moved to the front, order otherwise kept. */
function leading(refs: WaitingOnRef[], first: Set<string>): WaitingOnRef[] {
  return [...refs.filter((ref) => first.has(ref.id)), ...refs.filter((ref) => !first.has(ref.id))];
}

/**
 * What this ticket waits on, which of those has to move first, and the way
 * past them when a person decides to go anyway.
 *
 * The operator's question is "what has to happen before this one moves, and is
 * it mine?" Live chains run fifteen deep, so the direct prerequisite is often
 * not the answer: a ticket waiting on 1314 which waits on 1237 is unblocked by
 * opening 1237. The row leads with the root of each chain (`root_blockers`)
 * and names the direct prerequisites it is held through.
 *
 * A prerequisite that is done but not landed reads as met everywhere a list is
 * judged by state; the detail view judges it the way a dispatch does, so it is
 * named here with the branch it has to land on.
 *
 * "Start anyway" is the operator's override: dispatch holds the ticket until
 * its prerequisites land, and starting past them is recorded on its history.
 * Renders nothing when the ticket waits on nothing.
 */
export function TicketWaitingOn({ ticket }: { ticket: TicketDetail }) {
  const qc = useQueryClient();
  const [confirming, setConfirming] = useState(false);
  const startAnyway = useMutation({
    meta: { errorTitle: "Start past prerequisites" },
    mutationFn: () => api.orchestrate(ticket.id, { ignore_prerequisites: true }),
    onSuccess: (data) => {
      notifyIfQueued(data);
      qc.invalidateQueries({ queryKey: ["ticket", ticket.id] });
      qc.invalidateQueries({ queryKey: ["ticket-tree"] });
      setConfirming(false);
    },
  });

  const direct = ticket.waiting_on ?? [];
  const roots = ticket.root_blockers ?? [];
  const prerequisites = direct.filter((ref) => ref.reason === "prerequisite");
  const notLanded = (ticket.dependencies ?? []).filter(
    (dep) => holding(dep) && dep.unmet_reason === "not_landed",
  );
  const held = (ticket.dependencies ?? []).filter(holding);
  if (direct.length === 0 && notLanded.length === 0) return null;

  const rootIds = new Set(roots.map((ref) => ref.id));
  const directIds = new Set(prerequisites.map((ref) => ref.id));
  // A root beneath the direct prerequisites leads; a root that is itself direct
  // just goes first, so no ticket is named twice.
  const deeper = roots.some((ref) => !directIds.has(ref.id));
  const through = prerequisites.filter((ref) => !rootIds.has(ref.id));
  const running = ticket.activity === "running" || ticket.activity === "queued";
  const canStartAnyway = held.length > 0 && !running && ticket.state !== "done";

  return (
    <>
      {direct.length > 0 ? (
        <div className="attn-row attn-row--wait">
          {/* The spaces are for the accessible text: a flex row draws none between items. */}
          <span className="attn-label">Waiting on</span>{" "}
          <span className="attn-links">
            {deeper ? (
              <>
                <WaitingOnLinks refs={roots} here={ticket.workspace_slug} />{" "}
                <span className="attn-age">through</span>{" "}
                <WaitingOnLinks refs={through} here={ticket.workspace_slug} shown={2} />
              </>
            ) : (
              <WaitingOnLinks refs={leading(direct, rootIds)} here={ticket.workspace_slug} />
            )}
          </span>
          {canStartAnyway ? (
            <Button
              variant="plain"
              className="attn-action"
              disabled={startAnyway.isPending}
              onClick={() => setConfirming(true)}
            >
              Start anyway
            </Button>
          ) : null}
        </div>
      ) : null}

      {notLanded.length > 0 ? (
        <div className="attn-row attn-row--warn">
          <span className="attn-label">Not landed</span>{" "}
          <span className="attn-links">
            {notLanded.map((dep) => (
              <span key={dep.id}>
                <Button
                  variant="plain"
                  className="wait-link"
                  title={dep.title}
                  onClick={() => navigateToTicket(dep.id)}
                >
                  {dep.external_id}
                </Button>
                <span className="attn-age">
                  {" "}
                  is done; its work is not on {dep.landing_target || "this ticket's target branch"}
                </span>
              </span>
            ))}
          </span>
          {canStartAnyway && direct.length === 0 ? (
            <Button
              variant="plain"
              className="attn-action"
              disabled={startAnyway.isPending}
              onClick={() => setConfirming(true)}
            >
              Start anyway
            </Button>
          ) : null}
        </div>
      ) : null}

      <ReasonConfirmDialog
        open={confirming}
        eyebrow="Prerequisites"
        title={`Start ${ticket.external_id} anyway?`}
        description={
          <>
            <p>
              It waits on {held.map((dep) => dep.external_id).join(", ")}. Started now, its
              stages run without their work, and the start is recorded on its history.
            </p>
            <p>A prerequisite linked after this holds it again.</p>
          </>
        }
        confirmLabel={startAnyway.isPending ? "Starting…" : "Start anyway"}
        isSaving={startAnyway.isPending}
        onClose={() => setConfirming(false)}
        onConfirm={() => startAnyway.mutate()}
      />
    </>
  );
}
