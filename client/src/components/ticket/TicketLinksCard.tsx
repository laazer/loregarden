import { useQuery } from "@tanstack/react-query";
import { useId, useState } from "react";
import { createPortal } from "react-dom";
import { Link } from "react-router-dom";

import { api } from "../../api/client";
import type { TicketDetail, TicketState } from "../../api/client";
import type { TicketDependencyRef } from "../../api/types";
import { ticketPath } from "../../lib/appNavigation";
import { prerequisiteStatus } from "../../lib/prerequisiteStatus";
import { ticketStateChip } from "../../lib/ticketStates";
import { IconCloseButton } from "../IconCloseButton";
import { TicketDependencies } from "../TicketDependencies";
import { TicketRelations } from "../TicketRelations";
import { Button } from "../ui/Button";
import { ModalShell } from "../ui/ModalShell";
import { StatusChip } from "../ui/StatusChip";

import "./TicketLinksCard.css";

type LinkKind = "parent" | "waiting" | "depends" | "blocks" | "related";

const KIND_LABEL: Record<LinkKind, string> = {
  parent: "Parent",
  waiting: "Waiting on",
  depends: "Depends on",
  blocks: "Blocks",
  related: "Related",
};

interface LinkRow {
  kind: LinkKind;
  id: string;
  externalId: string;
  title: string;
  state: TicketState;
  /** Set when the far end lives in another workspace. */
  workspace: string;
  /** Why a prerequisite still holds this ticket, when it does. */
  note: string;
}

/** Past this many rows the rest wait behind "Show all": the largest ticket in use has 19. */
const SHOWN = 8;

function edgeRow(kind: LinkKind, ref: TicketDependencyRef, ownWorkspace: string | undefined, note = ""): LinkRow {
  return {
    kind,
    id: ref.id,
    externalId: ref.external_id,
    title: ref.title,
    state: ref.state,
    workspace: ref.workspace_slug && ref.workspace_slug !== ownWorkspace ? ref.workspace_slug : "",
    note,
  };
}

/**
 * Every ticket this one is tied to, in the order they matter: its parent, the
 * prerequisites (the ones still holding it first), what it holds up, then
 * what is related only for context. Each row opens that ticket's page.
 */
function linkRows(ticket: TicketDetail, parent: TicketDetail | undefined): LinkRow[] {
  const own = ticket.workspace_slug;
  const rows: LinkRow[] = [];
  if (ticket.parent_ticket_id) {
    rows.push({
      kind: "parent",
      id: ticket.parent_ticket_id,
      externalId: parent?.external_id ?? "",
      title: parent?.title ?? "",
      state: parent?.state ?? "backlog",
      workspace: "",
      note: "",
    });
  }
  const prerequisites = (ticket.dependencies ?? []).map((dep) => ({ dep, status: prerequisiteStatus(dep) }));
  for (const { dep, status } of prerequisites.filter((p) => p.status.holding)) {
    rows.push(edgeRow("waiting", dep, own, status.text));
  }
  for (const { dep } of prerequisites.filter((p) => !p.status.holding)) {
    rows.push(edgeRow("depends", dep, own));
  }
  for (const dep of ticket.dependents ?? []) rows.push(edgeRow("blocks", dep, own));
  for (const rel of ticket.related ?? []) rows.push(edgeRow("related", rel, own));
  return rows;
}

/**
 * "What is this ticket tied to, and is any of it holding it up?" — one row per
 * linked ticket with how it is linked and where that ticket stands; a row opens
 * its page. "Link" opens the dependency and relation editors in a dialog.
 */
export function TicketLinksCard({ ticket }: { ticket: TicketDetail }) {
  const headingId = useId();
  const [showAll, setShowAll] = useState(false);
  const [editing, setEditing] = useState(false);
  // The breadcrumb reads the parent under this key, so this is a cache hit.
  const parentId = ticket.parent_ticket_id;
  const parent = useQuery({
    queryKey: ["ticket", parentId],
    queryFn: () => api.ticket(parentId!),
    enabled: Boolean(parentId),
  });

  const rows = linkRows(ticket, parent.data);
  const shown = showAll ? rows : rows.slice(0, SHOWN);
  const holding = rows.filter((row) => row.kind === "waiting").length;

  return (
    <section className="tlk" aria-labelledby={headingId}>
      <div className="tlk-head">
        <h2 id={headingId} className="tlk-title">
          Linked tickets
        </h2>
        {rows.length > 0 ? <span className="tlk-count">{rows.length}</span> : null}
        {holding > 0 ? <span className="tlk-holding">{holding} holding it</span> : null}
        <Button
          variant="secondary"
          compact
          className="tlk-add"
          aria-haspopup="dialog"
          aria-expanded={editing}
          title="Add or remove the tickets this one waits for or relates to"
          onClick={() => setEditing(true)}
        >
          + Link
        </Button>
      </div>

      {rows.length === 0 ? (
        <p className="tlk-empty">
          Not linked to any ticket. Link one it should wait for, or one to keep beside it for context.
        </p>
      ) : (
        <ul className="tlk-list">
          {shown.map((row) => {
            const chip = ticketStateChip(row.state);
            return (
              <li key={`${row.kind}-${row.id}`}>
                <Link
                  className="tlk-row"
                  to={ticketPath(row.id)}
                  title={row.note ? `${KIND_LABEL[row.kind]} — ${row.note}` : undefined}
                >
                  <span className={`tlk-rel tlk-rel--${row.kind}`}>{KIND_LABEL[row.kind]}</span>
                  <span className="tlk-name">{row.title}</span>
                  <TicketIdText workspace={row.workspace} externalId={row.externalId} />
                  {row.externalId ? <StatusChip tone={chip.tone} label={chip.label} className="tlk-chip" /> : null}
                </Link>
              </li>
            );
          })}
        </ul>
      )}
      {rows.length > SHOWN ? (
        <Button variant="plain" className="tlk-more" aria-expanded={showAll} onClick={() => setShowAll((v) => !v)}>
          {showAll ? "Show fewer" : `Show all ${rows.length}`}
        </Button>
      ) : null}

      <LinkTicketsModal open={editing} ticket={ticket} onClose={() => setEditing(false)} />
    </section>
  );
}

/** An id that shortens from the front, so its number — what tells siblings apart — always shows. */
function TicketIdText({ workspace, externalId }: { workspace: string; externalId: string }) {
  if (!externalId) return <span className="tlk-id">Loading…</span>;
  const cut = externalId.lastIndexOf("-") + 1;
  return (
    <span className="tlk-id">
      <span className="tlk-id-head">
        {workspace ? `${workspace}: ` : ""}
        {externalId.slice(0, cut)}
      </span>
      <span className="tlk-id-tail">{externalId.slice(cut)}</span>
    </span>
  );
}

/** The dependency and relation editors, kept apart because only a dependency holds a dispatch. */
function LinkTicketsModal({ open, ticket, onClose }: { open: boolean; ticket: TicketDetail; onClose: () => void }) {
  return createPortal(
    <ModalShell open={open} onDismiss={onClose} labelledBy="link-tickets-title">
      {open ? (
        <>
          <div className="modal-header">
            <div>
              <div className="state-label">{ticket.external_id}</div>
              <h2 id="link-tickets-title" className="modal-title">
                Link tickets
              </h2>
              <p className="modal-subtitle">
                A dependency holds this ticket until that one is done and landed. A related ticket is context only.
              </p>
            </div>
            <IconCloseButton onClick={onClose} />
          </div>
          <div className="modal-body">
            <TicketDependencies ticket={ticket} />
            <TicketRelations ticket={ticket} />
          </div>
          <div className="modal-footer">
            <Button variant="primary" onClick={onClose}>
              Done
            </Button>
          </div>
        </>
      ) : null}
    </ModalShell>,
    document.body,
  );
}
