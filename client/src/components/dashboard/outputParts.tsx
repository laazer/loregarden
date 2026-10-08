import type { TicketArtifactItem } from "../../api/types";
import { verdictTone } from "../../lib/ticketOutputs";
import { isErrorOutput, reportVerdict } from "../../lib/ticketTimeline";

/**
 * The pieces the Timeline and Outputs views share for one artifact row, so an
 * output reads the same wherever it is met.
 */

export function KindTag({ item }: { item: TicketArtifactItem }) {
  return (
    <span className={`out-kind${isErrorOutput(item) ? " out-kind--error" : ""}`} title={item.kind}>
      {item.kind}
    </span>
  );
}

/** A stage report's conclusion — the line most worth reading in it. */
export function VerdictPill({ item }: { item: TicketArtifactItem }) {
  const verdict = reportVerdict(item.content);
  if (!verdict) return null;
  const tone = verdictTone(verdict.status);
  return (
    <span className={`out-verdict out-verdict--${tone}`}>
      {verdict.status}
      {verdict.confidence !== null ? ` ${verdict.confidence.toFixed(2)}` : ""}
    </span>
  );
}
