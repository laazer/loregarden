/**
 * What is holding this machine's Docker daemon, and who is waiting for it.
 *
 * The lanes above bound how many agents run at once; they say nothing about the
 * containers those agents start. This is the other pool — one daemon, shared by
 * every worktree and every terminal — and the question a reader opens it with is
 * "can I start something right now, and if not, when?".
 *
 * So the panel leads with what is FREE rather than what is used. Utilisation is
 * the interesting number when you are tuning a machine; headroom is the
 * interesting number when you are deciding whether to run something, and that is
 * the decision being made here.
 *
 * **It declines to state what it does not know.** Three separate places:
 * an unmeasured ceiling draws a hatched meter reading "not measured" rather than
 * an empty bar that would read as idle; a wait with no history behind it says
 * "unknown" rather than a plausible number; and a wait derived from a lease's TTL
 * is prefixed "≤" because it is a bound the holder will probably beat, not a
 * forecast. Each is a number the backend deliberately refuses to invent, and the
 * UI would undo that by rendering a confident zero.
 *
 * A failed read and an empty ledger are kept apart for the reason the Review tab
 * already learned: they look identical if you only check the list length.
 */

import { useCallback, useEffect, useState } from "react";

import { dockerApi } from "../api/dockerApi";
import type { DockerCapacityStatus, DockerLeaseRow } from "../api/dockerTypes";
import { CapacityMeter } from "./ui/CapacityMeter";
import { describeError } from "../state/toastStore";
import "./DockerCapacityRail.css";

/** While the tab is open the ledger is worth re-reading; it is closed the rest of the time. */
const REFRESH_MS = 10_000;

function formatCpus(value: number): string {
  const rounded = Math.round(value * 10) / 10;
  return `${rounded} ${rounded === 1 ? "cpu" : "cpus"}`;
}

function formatMemory(mb: number): string {
  if (mb >= 1024) return `${Math.round((mb / 1024) * 10) / 10} GB`;
  return `${Math.round(mb)} MB`;
}

function formatLeases(value: number): string {
  return `${Math.round(value)} ${Math.round(value) === 1 ? "lease" : "leases"}`;
}

function formatDuration(seconds: number): string {
  if (seconds < 60) return `${Math.max(0, Math.round(seconds))}s`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  return `${Math.round((minutes / 60) * 10) / 10}h`;
}

/**
 * A projected wait, carrying how much it is worth.
 *
 * `≈` and `≤` are doing real work: the first is a median of what these leases
 * have cost, the second is a TTL nobody has beaten yet. Rendering them the same
 * way would present a ceiling as a prediction.
 */
function describeWait(row: DockerLeaseRow): string {
  if (row.estimated_wait_seconds === null) return "wait unknown";
  const amount = formatDuration(row.estimated_wait_seconds);
  return row.estimate_basis === "ttl_bound" ? `≤ ${amount}` : `≈ ${amount}`;
}

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

function LeaseRow({ row, kind }: { row: DockerLeaseRow; kind: "holder" | "waiter" }) {
  const orphaned = row.status === "orphaned";
  const unverified = Boolean(row.last_probe_outcome) && row.last_probe_outcome !== "ok";

  return (
    <li className="docker-lease" data-testid={`docker-lease-${row.lease_id}`}>
      <div className="docker-lease-head">
        <span className="docker-lease-label" title={row.holder_label}>
          {row.holder_label || "unlabelled"}
        </span>
        <span className="docker-lease-size">
          {formatCpus(row.cpus)} · {formatMemory(row.memory_mb)}
        </span>
      </div>
      <div className="docker-lease-meta">
        {kind === "waiter" ? (
          <>
            <span className="docker-lease-pos">#{row.position ?? "?"}</span>
            <span className="docker-lease-wait">{describeWait(row)}</span>
          </>
        ) : (
          <span className="docker-lease-wait">
            {row.expires_in_seconds === null
              ? "no expiry"
              : `expires in ${formatDuration(row.expires_in_seconds)}`}
          </span>
        )}
        {row.compose_project ? (
          <span className="docker-lease-project">{row.compose_project}</span>
        ) : null}
        {/* Badges say the word as well as carrying a colour — the state has to
            survive a viewer who cannot separate amber from red. */}
        {orphaned ? <span className="docker-badge docker-badge--warn">orphaned</span> : null}
        {unverified ? (
          <span className="docker-badge docker-badge--bad" title={row.last_probe_error}>
            unverified
          </span>
        ) : null}
      </div>
    </li>
  );
}

export function DockerCapacityRail() {
  const [status, setStatus] = useState<DockerCapacityStatus | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      const next = await dockerApi.capacity();
      setStatus(next);
      setError("");
    } catch (err) {
      setError(describeError(err, "Failed to read docker capacity"));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), REFRESH_MS);
    return () => window.clearInterval(timer);
  }, [load]);

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
  const { available, ceiling, in_use: inUse } = status;

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

      <div className="docker-meters">
        <CapacityMeter label="CPU" used={inUse.cpus} total={ceiling.cpus} format={formatCpus} />
        <CapacityMeter
          label="Memory"
          used={inUse.memory_mb}
          total={ceiling.memory_mb}
          format={formatMemory}
        />
        <CapacityMeter
          label="Leases"
          used={inUse.leases}
          total={ceiling.leases}
          format={formatLeases}
        />
      </div>

      <div className="queue-rail-divider" />

      <div className="docker-section-heading">
        Holding <span className="docker-count">{status.holders.length}</span>
      </div>
      {status.holders.length === 0 ? (
        <p className="queue-rail-empty">Nothing is holding docker capacity.</p>
      ) : (
        <ul className="docker-lease-list">
          {status.holders.map((row) => (
            <LeaseRow key={row.lease_id} row={row} kind="holder" />
          ))}
        </ul>
      )}

      <div className="docker-section-heading">
        Waiting <span className="docker-count">{status.waiting.length}</span>
      </div>
      {status.waiting.length === 0 ? (
        <p className="queue-rail-empty">Nobody is queued for capacity.</p>
      ) : (
        <ul className="docker-lease-list">
          {status.waiting.map((row) => (
            <LeaseRow key={row.lease_id} row={row} kind="waiter" />
          ))}
        </ul>
      )}
    </>
  );
}
