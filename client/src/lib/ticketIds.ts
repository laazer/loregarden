/** Telling the two ticket identifiers apart on the client.
 *
 * A ticket has a UUID (`Ticket.id`, what every API path is keyed by) and a
 * shareable external id (`lor-mcp-gateway-142`). Routes accept either, so the
 * one thing the client needs to decide is which of the two it is holding —
 * everything else about the spelling is the server's business.
 */

const TICKET_UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** True when `value` is a ticket UUID, i.e. already the canonical route id. */
export function looksLikeTicketUuid(value: string): boolean {
  return TICKET_UUID_RE.test(value.trim());
}
