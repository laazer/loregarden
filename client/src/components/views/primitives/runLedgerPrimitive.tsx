/**
 * A ticket's run ledger in a container.
 *
 * `RunLedgerPanel` was already prop-driven — it takes a ticket id and fetches
 * its own ledger through react-query — so the primitive is a settings parse and
 * nothing else. The panel's optional run-log callback is deliberately not
 * wired: opening a run log is page navigation, and a container has no route.
 */

import { RunLedgerPanel } from "../../RunLedgerPanel";
import { definePrimitive } from "./definePrimitive";
import { Unconfigured } from "./Unconfigured";

type RunLedgerSettings = {
  ticketId: string;
  live: boolean;
};

export const runLedgerPrimitive = definePrimitive<RunLedgerSettings>({
  id: "run_ledger",
  displayName: "Run Ledger",
  icon: "≡",
  category: "Tickets",
  containerKind: "panel",
  settingsFields: [
    {
      key: "ticket_id",
      kind: "string",
      label: "Ticket",
      default: "",
      help: "The ticket whose stage visits this pane lists.",
    },
    {
      key: "live",
      kind: "boolean",
      label: "Poll while the ticket is running",
      default: false,
    },
  ],
  parseSettings: (raw) => ({
    ticketId: typeof raw.ticket_id === "string" ? raw.ticket_id : "",
    live: typeof raw.live === "boolean" ? raw.live : false,
  }),
  Component: ({ settings }) => {
    // `api.ticketLedger("")` asks for a ticket that cannot exist; a container
    // the operator has only just dropped in has no ticket yet.
    if (settings.ticketId === "") {
      return <Unconfigured>This run ledger has no ticket yet.</Unconfigured>;
    }
    return <RunLedgerPanel ticketId={settings.ticketId} isActive={settings.live} />;
  },
});
