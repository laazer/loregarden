/**
 * A ticket's run log in a container.
 *
 * ## The blocker this removes
 *
 * `LogsPanel` was on `excludedPanels` with a specific reason: it takes a whole
 * `TicketDetail` plus its run list, and a container's settings map can carry an
 * id and nothing more. That was true, and the answer was never to reshape the
 * panel — it was the wrapper named in the same sentence. `api.ticket(id)`
 * *is* the id-to-detail step, and the run list is already the panel's own
 * `useQuery`. So this file is a fetch and five states; the panel is untouched.
 *
 * The run ledger primitive does the same thing one layer earlier — its panel
 * happened to take an id — which is why the two sit beside each other here and
 * read almost the same. That similarity is the point: a container primitive is
 * a settings parse and a fetch, and anything more elaborate is a sign the panel
 * is holding state that belongs to a page.
 *
 * ## The five states
 *
 * 1. **Loading** — a `code` skeleton, because log lines are what is coming.
 * 2. **Empty** — two different empties, kept apart. No ticket chosen is
 *    `Unconfigured`, which points at the settings control. A ticket with no
 *    lines yet is `LogsPanel`'s own "No log lines yet", which is the honest
 *    answer and already written.
 * 3. **Error** — the fetch failed, said in the pane. A toast would be wrong
 *    here: the pane is still on screen and would otherwise sit blank while the
 *    explanation floated away.
 * 4. **In flight** — the panel polls its ledger on its own interval and the
 *    detail query refetches with it; nothing here is a button, so there is no
 *    double-submit to guard.
 * 5. **Keyboard** — the panel's own lane tabs and approval controls are already
 *    reachable; this wrapper adds no new control to reach.
 *
 * ## Why the error text is inline and not `describeError`
 *
 * `describeError` lives in `state/toastStore`, and a container primitive may not
 * import `state/` — a zustand read outside a provider returns a value instead of
 * throwing, so that coupling would not surface as a failure. The pane says what
 * failed in its own words instead.
 */

import { useQuery } from "@tanstack/react-query";

import { api } from "../../../api/client";
import { LogsPanel } from "../../LogsPanel";
import { PaneSkeleton } from "../../ui/PaneSkeleton";
import { definePrimitive } from "./definePrimitive";
import { Unconfigured } from "./Unconfigured";
import {
  PICKER_SCOPE_HELP,
  WORKSPACE_SCOPE_KEY,
  workspaceScopeField,
} from "./workspaceScope";

type TicketLogsSettings = {
  ticketId: string;
};

function TicketLogsPane({ ticketId }: TicketLogsSettings) {
  const ticket = useQuery({
    // The same key every other reader of a ticket detail uses, so a pane beside
    // a ticket page costs nothing and both see one answer.
    queryKey: ["ticket", ticketId],
    queryFn: () => api.ticket(ticketId),
    // The panel polls its own ledger every 2s; the detail carries the buffered
    // log lines, so it has to move too or the pane shows a live lane above a
    // frozen transcript.
    refetchInterval: 5000,
    // Rendered below, in the pane. A global toast on top of a state that is
    // already on screen is an alarm for something the operator can see.
    meta: { suppressErrorToast: true },
  });

  if (ticket.isLoading) {
    return <PaneSkeleton variant="code" label="Loading the run log…" />;
  }

  if (ticket.data === undefined) {
    // Covers the error and the "resolved to nothing" case together, because the
    // operator's next move is the same for both: check the id. Naming the id is
    // what makes that actionable — a view can outlive the ticket it points at.
    return <Unconfigured>{`The log for ${ticketId} could not be loaded.`}</Unconfigured>;
  }

  return <LogsPanel ticket={ticket.data} />;
}

export const ticketLogsPrimitive = definePrimitive<TicketLogsSettings>({
  id: "ticket_logs",
  displayName: "Run Log",
  icon: "⎙",
  category: "Tickets",
  containerKind: "panel",
  settingsFields: [
    workspaceScopeField(PICKER_SCOPE_HELP),
    {
      key: "ticket_id",
      kind: "choice",
      source: "ticket",
      workspaceFrom: WORKSPACE_SCOPE_KEY,
      label: "Ticket",
      default: "",
      help: "The ticket whose run output this pane streams.",
    },
  ],
  parseSettings: (raw) => ({
    ticketId: typeof raw.ticket_id === "string" ? raw.ticket_id : "",
  }),
  Component: ({ settings }) => {
    // `api.ticket("")` asks for a ticket that cannot exist, and the request
    // would 404 on every poll for a pane the operator has only just dropped in.
    if (settings.ticketId === "") {
      return <Unconfigured>This run log has no ticket yet.</Unconfigured>;
    }
    return <TicketLogsPane ticketId={settings.ticketId} />;
  },
});
