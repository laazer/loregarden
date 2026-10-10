/**
 * The rail half of the Machine tab: how much room there is, and how much the
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
 *
 * Two pools, nested: the machine (host) is what every lease is charged to, and
 * the Docker VM is a smaller pool inside it that container claims also need
 * room in. The headline is the machine's, because that is what a test run or a
 * build is waiting on; Docker's is a tile beside it.
 */

import type { CapacityPool, DockerCapacityStatus, DockerCeiling } from "../api/dockerTypes";
import "./DockerCapacityRail.css";

interface Caveat {
  tone: string;
  text: string;
}

interface PoolWords {
  stale: string;
  unknown: string;
  override: string;
}

/** How each pool's ceiling is described when it is not a fresh measurement. */
const POOL_WORDS: Record<CapacityPool, PoolWords> = {
  host: {
    stale: "This machine's size could not be read",
    unknown: "This machine's size has never been read, so every reservation is being refused",
    override: "Machine ceiling set by configuration rather than measured.",
  },
  docker: {
    stale: "Docker is not answering",
    unknown:
      "Docker capacity has never been measured, so docker reservations are being refused",
    override: "Docker ceiling set by configuration rather than measured.",
  },
};

/** Free cpus, or a dash for a ceiling nobody has measured — never a plausible zero. */
function describeFree(ceiling: DockerCeiling, cpus: number): string {
  return ceiling.source === "unknown" ? "—" : formatCpus(cpus);
}

/** The one line worth saying about a ceiling that is not a fresh measurement. */
function ceilingCaveat(ceiling: DockerCeiling, words: PoolWords): Caveat | null {
  const reason = ceiling.error ? ` — ${ceiling.error}` : "";
  if (ceiling.source === "stale_probe") {
    return { tone: "warn", text: `${words.stale}. Showing the last good measurement${reason}` };
  }
  if (ceiling.source === "unknown") {
    return { tone: "bad", text: `${words.unknown}${reason}` };
  }
  if (ceiling.source === "config_override") {
    return { tone: "info", text: words.override };
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
        <div className="queue-rail-heading">Machine capacity</div>
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
        <div className="queue-rail-heading">Machine capacity</div>
        <p className="queue-rail-empty">{error}</p>
      </>
    );
  }

  if (!status || !status.enabled) {
    return (
      <>
        <div className="queue-rail-heading">Machine capacity</div>
        <p className="queue-rail-empty">
          The capacity ledger is switched off. Nothing is being tracked or limited.
        </p>
      </>
    );
  }

  const { host } = status;
  const caveats = [
    ceilingCaveat(host.ceiling, POOL_WORDS.host),
    ceilingCaveat(status.ceiling, POOL_WORDS.docker),
  ].filter((caveat): caveat is Caveat => caveat !== null);

  return (
    <>
      <div className="queue-rail-heading">Machine capacity</div>

      {caveats.map((caveat) => (
        <p
          key={caveat.text}
          className={`docker-caveat docker-caveat--${caveat.tone}`}
          role="status"
        >
          {caveat.text}
        </p>
      ))}

      {/* Headline is what is FREE: the reader is deciding whether to start
          something, not auditing utilisation. */}
      <div className="docker-headline">
        <span className="docker-headline-value">
          {describeFree(host.ceiling, host.available.cpus)}
        </span>
        <span className="docker-headline-label">free on this machine</span>
      </div>

      <div className="queue-rail-grid">
        <div className="queue-rail-tile">
          <div className="queue-rail-tile-label">Slots held</div>
          <div className="queue-rail-tile-value">
            {host.in_use.leases}/{host.ceiling.leases || "—"}
          </div>
        </div>
        <div className="queue-rail-tile">
          <div className="queue-rail-tile-label">Docker free</div>
          <div className="queue-rail-tile-value">
            {describeFree(status.ceiling, status.available.cpus)}
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
