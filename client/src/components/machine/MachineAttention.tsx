/**
 * The top of Machine → Capacity: what on this machine needs a person, and the
 * one thing to do about each. The rules are in `lib/machineAttention`.
 *
 * Ending a lease goes through the board's own confirm (`onEndLease`), so the
 * band and the holder card beside it cannot release the same lease two ways.
 * Empty is the normal case and says so in a line, rather than a blank gap that
 * reads as a band that failed to load.
 */

import { Link } from "react-router-dom";

import type { DockerLeaseRow } from "../../api/dockerTypes";
import { machinePath, ticketPath } from "../../lib/appNavigation";
import { formatDiskBytes } from "../../lib/diskUsage";
import { diskUsedFraction, type AttentionItem } from "../../lib/machineAttention";
import { Button } from "../ui/Button";
import "./MachineAttention.css";

function formatHours(seconds: number): string {
  return `${Math.round((seconds / 3600) * 10) / 10} hours`;
}

function formatCpus(value: number): string {
  const rounded = Math.round(value * 10) / 10;
  return `${rounded} ${rounded === 1 ? "cpu" : "cpus"}`;
}

function formatMemory(mb: number): string {
  if (mb >= 1024) return `${Math.round((mb / 1024) * 10) / 10} GB`;
  return `${Math.round(mb)} MB`;
}

/** "horizon-client POC checks · risk-downside-measures · pid 32624 · 1 cpu, 1 GB" */
function describeLease(lease: DockerLeaseRow): string {
  const { what, branch, pid } = lease.holder;
  return [
    what || lease.holder_label || "unlabelled lease",
    branch,
    pid ? `pid ${pid}` : "",
    `${formatCpus(lease.cpus)}, ${formatMemory(lease.memory_mb)}`,
  ]
    .filter(Boolean)
    .join(" · ");
}

export interface DiskScanControl {
  /** Whether the disk has ever been scanned. */
  scanned: boolean;
  scanning: boolean;
  onScan: () => void;
}

interface Props {
  items: AttentionItem[];
  disk: DiskScanControl;
  /** Set when the disk read failed: the band says so rather than reading as "disk fine". */
  diskError: string;
  onRetryDisk: () => void;
  /** Either read is still on its first fetch: "nothing needs you" would be a guess. */
  checking: boolean;
  onEndLease: (lease: DockerLeaseRow) => void;
  pendingLeaseId: string | null;
}

function LeaseEnd({
  lease,
  pending,
  onEnd,
}: {
  lease: DockerLeaseRow;
  pending: boolean;
  onEnd: (lease: DockerLeaseRow) => void;
}) {
  return (
    <>
      {lease.ticket_id ? (
        <Link
          className="machine-attention-link"
          to={ticketPath(lease.ticket_id, lease.agent_run_id ? "timeline" : "diff")}
        >
          {lease.agent_run_id ? "Open run" : "Open ticket"}
        </Link>
      ) : null}
      <Button
        variant="secondary"
        compact
        disabled={pending}
        aria-label={`Release…: ${lease.holder.what || "unlabelled lease"}`}
        onClick={() => onEnd(lease)}
      >
        {pending ? "Ending…" : "Release…"}
      </Button>
    </>
  );
}

function ItemBody({ item, ...props }: { item: AttentionItem } & Omit<Props, "items" | "diskError" | "onRetryDisk" | "checking">) {
  switch (item.kind) {
    case "disk": {
      const { volume } = item;
      const percent = Math.round(diskUsedFraction(volume) * 100);
      return (
        <>
          <div className="machine-attention-copy">
            <div className="machine-attention-title">The disk is {percent}% full</div>
            <div className="machine-attention-detail">
              {formatDiskBytes(volume.free_bytes)} free of {formatDiskBytes(volume.total_bytes)}.{" "}
              {props.disk.scanned
                ? "The last scan shows what is using it, and what can safely go."
                : "It has never been scanned, so nothing yet says what is using it."}
            </div>
          </div>
          <div className="machine-attention-actions">
            {props.disk.scanned ? (
              <Link className="machine-attention-link" to={machinePath("disk")}>
                Where did the disk go?
              </Link>
            ) : (
              <Button
                variant="primary"
                compact
                disabled={props.disk.scanning}
                onClick={props.disk.onScan}
              >
                {props.disk.scanning ? "Scanning…" : "Scan now"}
              </Button>
            )}
          </div>
        </>
      );
    }
    case "long_held":
      return (
        <>
          <div className="machine-attention-copy">
            <div className="machine-attention-title">
              A lease has been held for {formatHours(item.lease.held_seconds ?? 0)}
            </div>
            <div className="machine-attention-detail">{describeLease(item.lease)}</div>
          </div>
          <div className="machine-attention-actions">
            <LeaseEnd
              lease={item.lease}
              pending={props.pendingLeaseId === item.lease.lease_id}
              onEnd={props.onEndLease}
            />
          </div>
        </>
      );
    case "orphaned":
      return (
        <>
          <div className="machine-attention-copy">
            <div className="machine-attention-title">
              {item.lease.holder.what || item.lease.holder_label || "A lease"} expired with its
              containers still up
            </div>
            <div className="machine-attention-detail">
              {item.lease.running_container_count ?? "Some"} of its containers are running. The
              ledger will not stop them: stop the stack yourself if it is stale, or renew the
              lease if the work is still wanted.
            </div>
            {item.lease.compose_project ? (
              <code className="machine-attention-cmd">
                docker compose -p {item.lease.compose_project} down
              </code>
            ) : null}
          </div>
          <div className="machine-attention-actions">
            <LeaseEnd
              lease={item.lease}
              pending={props.pendingLeaseId === item.lease.lease_id}
              onEnd={props.onEndLease}
            />
          </div>
        </>
      );
    case "unverified":
      return (
        <div className="machine-attention-copy">
          <div className="machine-attention-title">
            Lease {item.entry.lease_id.slice(0, 8)} could not be verified
          </div>
          <div className="machine-attention-detail">
            The reaper could not reach Docker to check it, so its capacity is held rather than
            guessed free.
          </div>
          <code className="machine-attention-cmd">{item.entry.error || item.entry.outcome}</code>
        </div>
      );
    case "ceiling":
      return (
        <div className="machine-attention-copy">
          <div className="machine-attention-title">{item.text}</div>
        </div>
      );
  }
}

export function MachineAttention({ items, diskError, onRetryDisk, checking, ...props }: Props) {
  return (
    <section className="machine-attention" aria-labelledby="machine-attention-heading">
      <h2 id="machine-attention-heading" className="machine-attention-heading">
        Needs attention
        {items.length ? <span className="machine-attention-count">{items.length}</span> : null}
      </h2>
      {diskError ? (
        <div className="machine-attention-item machine-attention-item--warn" role="alert">
          <div className="machine-attention-copy">
            <div className="machine-attention-title">Could not read the disk</div>
            <div className="machine-attention-detail">{diskError}</div>
          </div>
          <div className="machine-attention-actions">
            <Button variant="secondary" compact onClick={onRetryDisk}>
              Try again
            </Button>
          </div>
        </div>
      ) : null}
      {items.length === 0 ? (
        diskError ? null : checking ? (
          <p className="machine-attention-empty" role="status">
            Checking the disk and the capacity ledger…
          </p>
        ) : (
          <p className="machine-attention-empty">
            Nothing needs you: the disk has room, and every lease is accounted for.
          </p>
        )
      ) : (
        <ul className="machine-attention-list">
          {items.map((item) => (
            <li
              key={item.id}
              className={`machine-attention-item machine-attention-item--${item.tone}`}
              data-testid={`attention-${item.id}`}
            >
              <ItemBody item={item} {...props} />
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
