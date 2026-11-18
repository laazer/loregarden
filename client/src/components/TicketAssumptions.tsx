import type { OperatorQuestionRow } from "../api/operatorQuestionApi";
import { useTicketOperatorQuestions } from "../hooks/useTicketOperatorQuestions";
import { describeError } from "../state/toastStore";

function answerLabel(row: OperatorQuestionRow): string {
  if (row.status === "ignored") return "Ignored — default stood";
  if (row.outcome === "overridden") return `Overridden: ${row.answer}`;
  return "Confirmed";
}

/**
 * "What did agents assume on this ticket, and what did I decide?" Settled
 * questions, each with the agent's default and the operator's answer; open ones
 * are answered in the Needs you section above, so they are only counted here.
 *
 * Loading and a ticket nobody asked about render nothing: most tickets have no
 * questions, and the modal is about the ticket. A failed fetch is said out loud.
 */
export function TicketAssumptions({ ticketId }: { ticketId: string }) {
  const feed = useTicketOperatorQuestions(ticketId);

  if (feed.isError) {
    return (
      <div className="state-card" role="alert">
        <div className="state-label">Assumptions</div>
        <p className="modal-hint" style={{ color: "var(--red)", margin: 0 }}>
          Could not load this ticket&rsquo;s questions: {describeError(feed.error, "the request failed")}.
          Retrying shortly.
        </p>
      </div>
    );
  }

  const settled = (feed.data?.questions ?? []).filter((row) => row.status !== "pending");
  // ux-ok: no settled questions (or still loading) is the common case and renders nothing on purpose; open ones show under Needs you.
  if (settled.length === 0) return null;
  const open = feed.data?.open ?? 0;

  return (
    <section className="state-card" aria-label="Agent assumptions">
      <div className="state-label">Assumptions ({settled.length})</div>
      {open > 0 && (
        <p className="modal-hint" style={{ marginTop: 0 }}>
          {open} more waiting on you under Needs you.
        </p>
      )}
      <div className="sc-table-wrap">
        <table className="sc-table">
          <thead>
            <tr>
              <th scope="col">Question</th>
              <th scope="col">Agent assumed</th>
              <th scope="col">Your answer</th>
              <th scope="col">Asked at</th>
            </tr>
          </thead>
          <tbody>
            {settled.map((row) => (
              <tr key={row.id}>
                <td>{row.question}</td>
                <td>{row.default}</td>
                <td>{answerLabel(row)}</td>
                <td style={{ fontFamily: "var(--mono)" }}>{row.stage_key ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
