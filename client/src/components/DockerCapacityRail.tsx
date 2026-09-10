/**
 * The rail half of the Docker tab: how much room there is, and how much the
 * number is worth.
 *
 * The queue itself — who holds capacity, who is behind them, how long until
 * their turn — is the board in the main area. This is the summary beside it,
 * the same split the Review tab uses: the rail carries the index and the
 * caveats, the main area carries the thing you came to look at. An earlier cut
 * put the whole queue in here, which made a cramped sidebar of what the board
 * already knows how to draw.
 *
 * What survives here is the part a summary is actually good at: the ceiling,
 * where it came from, and anything that makes it less trustworthy than it looks
 * — a stale measurement, an unmeasured machine, a lease nobody could verify.
 */

import type { DockerCapacityStatus } from "../api/dockerTypes";
import "./DockerCapacityRail.css";

/** The one line worth saying about a ceiling that is not a fresh measurement. */
function ceilingCaveat(status: DockerCapacityStatus): { tone: string; text: string } | null {
  const { ceiling } = status;
  if (ceiling.source === "stale_probe") {
    return {
      tone: "warn",
      text: `Docker is not answering. Showing the last good measurement${
        ceiling.error ? ` — ${ceiling.error}` : ""
      }`,
    };
  }
  if (ceiling.source === "unknown") {
    return {
      tone: "bad",
      text: `Capacity has never been measured, so reservations are being refused${
        ceiling.error ? ` — ${ceiling.error}` : ""
      }`,
    };
  }
  if (ceiling.source === "config_override") {
    return { tone: "info", text: "Ceiling set by configuration rather than measured." };
  }
  return null;
}

function formatCpus(value: number): string {
  const rounded = Math.round(value * 10) / 10;
  return `${rounded} ${rounded === 1 ? "cpu" : "cpus"}`;
}

export function DockerCapacityRail({
  status,
  error,
  loading,
}: {
  status: DockerCapacityStatus | null;
  error: string;
  loading: boolean;
}) {
  if (loading && !status) {
    return (
      <>
        <div className="queue-rail-heading">Docker capacity</div>
        <p className="queue-rail-empty" role="status">
          Reading the ledger…
        </p>
      </>
    );
  }

  // A failed read and an idle daemon are kept apart: they look identical if you
  // only check whether the lists came back empty.
  if (error) {
    return (
      <>
        <div className="queue-rail-heading">Docker capacity</div>
        <p className="queue-rail-empty">{error}</p>
      </>
    );
  }

  if (!status || !status.enabled) {
    return (
      <>
        <div className="queue-rail-heading">Docker capacity</div>
        <p className="queue-rail-empty">
          The capacity ledger is switched off. Nothing is being tracked or limited.
        </p>
      </>
    );
  }

  const caveat = ceilingCaveat(status);
  const { available, ceiling } = status;

  return (
    <>
      <div className="queue-rail-heading">Docker capacity</div>

      {caveat ? (
        <p className={`docker-caveat docker-caveat--${caveat.tone}`} role="status">
          {caveat.text}
        </p>
      ) : null}

      {/* Headline is what is FREE: the reader is deciding whether to start
          something, not auditing utilisation. */}
      <div className="docker-headline">
        <span className="docker-headline-value">{formatCpus(available.cpus)}</span>
        <span className="docker-headline-label">free now</span>
      </div>

      <div className="queue-rail-grid">
        <div className="queue-rail-tile">
          <div className="queue-rail-tile-label">Holding</div>
          <div className="queue-rail-tile-value">
            {status.holders.length}/{ceiling.leases || "—"}
          </div>
        </div>
        <div className="queue-rail-tile">
          <div className="queue-rail-tile-label">Waiting</div>
          <div className="queue-rail-tile-value">{status.waiting.length}</div>
        </div>
      </div>

      {status.unverifiable.length ? (
        <p className="docker-caveat docker-caveat--bad">
          {status.unverifiable.length} lease(s) could not be verified, so their capacity is
          held rather than guessed at.
        </p>
      ) : null}

      <p className="queue-rail-empty">Who holds it, and who is waiting, is on the board →</p>
    </>
  );
}
