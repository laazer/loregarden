import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

import type {
  DiskCleanupResult,
  DiskNode,
  DiskOverview,
} from "../../api/diskTypes";
import {
  useDiskHistory,
  useDiskOverview,
  useDiskTree,
  useInvalidateDisk,
} from "../../hooks/useDiskUsage";
import {
  DISK_KIND_LABELS,
  DISK_LEGEND_KINDS,
  formatDiskBytes,
  outermostPaths,
} from "../../lib/diskUsage";
import { describeError } from "../../state/toastStore";
import { formatRelativeAge } from "../../lib/timestamps";
import { Button } from "../ui/Button";
import { Input } from "../ui/Input";
import { CapacityMeter, type CapacityTone } from "../ui/CapacityMeter";
import { PaneSkeleton } from "../ui/PaneSkeleton";
import { DiskCleanupModal } from "./DiskCleanupModal";
import { DiskEntriesTable } from "./DiskEntriesTable";
import { DiskFoldersModal } from "./DiskFoldersModal";
import { DiskTreemap } from "./DiskTreemap";
import "./DiskUsageView.css";

const ALL_FOLDERS = "All scanned folders";

function volumeTone(free: number, total: number): CapacityTone {
  if (total <= 0) return "unknown";
  if (free / total < 0.05) return "full";
  if (free / total < 0.15) return "tight";
  return "normal";
}

interface Crumb {
  label: string;
  path: string;
}

/** From "All scanned folders" down to the focus, through the root that holds it. */
function crumbsFor(path: string, overview: DiskOverview | undefined): Crumb[] {
  const crumbs: Crumb[] = [{ label: ALL_FOLDERS, path: "" }];
  if (!path) return crumbs;
  const root = overview?.scan?.roots
    .filter((r) => path === r.path || path.startsWith(`${r.path}/`))
    .sort((a, b) => b.path.length - a.path.length)[0];
  if (!root) return [...crumbs, { label: path, path }];
  crumbs.push({ label: root.label, path: root.path });
  let current = root.path;
  for (const segment of path
    .slice(root.path.length)
    .split("/")
    .filter(Boolean)) {
    current = `${current}/${segment}`;
    crumbs.push({ label: segment, path: current });
  }
  return crumbs;
}

/**
 * Machine → Disk: "what is filling my disk, and what can safely go?"
 *
 * The mosaic answers the first half at a glance; the table beside it gives
 * exact sizes and the checkboxes; the cleanup review answers the second half on
 * the server, path by path, before anything moves (to the Trash, never away).
 * The focused folder lives in the URL (`?path=`), so Back walks up the tree.
 */
export function DiskUsageView() {
  const { overview, scan } = useDiskOverview();
  const [searchParams, setSearchParams] = useSearchParams();
  const focusPath = searchParams.get("path") ?? "";
  const data = overview.data;
  const scannedAt = data?.scan?.finished_at ?? null;
  const tree = useDiskTree(focusPath, scannedAt);
  const invalidate = useInvalidateDisk();

  const [marked, setMarked] = useState<ReadonlyMap<string, number>>(new Map());
  const [inspected, setInspected] = useState<DiskNode | null>(null);
  const [cleanupPaths, setCleanupPaths] = useState<string[] | null>(null);
  const [foldersOpen, setFoldersOpen] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [filter, setFilter] = useState("");
  const history = useDiskHistory(historyOpen);

  const rootPaths = useMemo(
    () => new Set(data?.scan?.roots.map((r) => r.path) ?? []),
    [data],
  );
  const focus: DiskNode | null = useMemo(() => {
    const nodes = tree.data;
    if (!nodes) return null;
    if (focusPath) return nodes[0] ?? null;
    return {
      name: ALL_FOLDERS,
      path: "",
      size_bytes: nodes.reduce((sum, n) => sum + n.size_bytes, 0),
      kind: "directory",
      is_dir: true,
      file_count: nodes.reduce((sum, n) => sum + n.file_count, 0),
      children: nodes,
      folded_count: 0,
    };
  }, [tree.data, focusPath]);

  const markedPaths = outermostPaths(marked.keys());
  const markedBytes = markedPaths.reduce(
    (sum, p) => sum + (marked.get(p) ?? 0),
    0,
  );

  const openPath = (path: string) => {
    setInspected(null);
    setFilter("");
    setSearchParams(path ? { path } : {});
  };
  const openNode = (node: DiskNode) => openPath(node.path);
  const toggleMark = (node: DiskNode) =>
    setMarked((previous) => {
      const next = new Map(previous);
      if (next.has(node.path)) next.delete(node.path);
      else next.set(node.path, node.size_bytes);
      return next;
    });
  const onCleaned = (result: DiskCleanupResult) => {
    const gone = new Set(
      result.results.filter((r) => r.outcome === "trashed").map((r) => r.path),
    );
    setMarked(
      (previous) =>
        new Map(
          [...previous].filter(
            ([p]) => ![...gone].some((g) => p === g || p.startsWith(`${g}/`)),
          ),
        ),
    );
    invalidate();
  };

  if (overview.isPending)
    return <PaneSkeleton variant="block" label="Loading disk usage…" />;
  if (overview.isError || !data) {
    return (
      <div className="disk-view disk-view--message" role="alert">
        <p>Could not read disk usage: {describeError(overview.error)}</p>
        <Button variant="secondary" onClick={() => void overview.refetch()}>
          Try again
        </Button>
      </div>
    );
  }

  const { volume, status } = data;
  const scanning = status.state === "running" || scan.isPending;
  const summary = data.scan;

  return (
    <div className="disk-view">
      <header className="disk-head">
        <div className="disk-volume">
          <CapacityMeter
            label="Disk"
            used={volume.used_bytes}
            total={volume.total_bytes}
            format={formatDiskBytes}
            tone={volumeTone(volume.free_bytes, volume.total_bytes)}
          />
          <p className="disk-volume-free">
            {formatDiskBytes(volume.free_bytes)} free
          </p>
        </div>
        <div className="disk-actions">
          <p className="disk-scan-state" role="status" aria-live="polite">
            {scanning
              ? `Scanning… ${status.entries.toLocaleString()} entries${status.current_root ? ` · ${status.current_root}` : ""}`
              : summary
                ? `Scanned ${formatRelativeAge(summary.finished_at)} · ${formatDiskBytes(summary.total_bytes)} across ${summary.roots.length} folders`
                : "Not scanned yet"}
          </p>
          <Button
            variant="secondary"
            disabled={scanning}
            onClick={() => setFoldersOpen(true)}
            aria-expanded={foldersOpen}
          >
            Folders…
          </Button>
          <Button
            variant="primary"
            disabled={scanning}
            onClick={() => scan.mutate()}
          >
            {scanning ? "Scanning…" : summary ? "Rescan" : "Scan now"}
          </Button>
        </div>
      </header>

      {status.state === "failed" ? (
        <p className="disk-notice is-error" role="alert">
          The last scan failed: {status.error} The map shows the scan before it.
        </p>
      ) : null}
      {summary ? <ScanNotices summary={summary} /> : null}

      {!summary ? (
        <div className="disk-empty">
          <h2>Where did the disk go?</h2>
          <p>
            A scan reads the names and sizes of everything in your workspaces,
            their worktrees and any folders you add. It never opens a file.
            About a minute per million files.
          </p>
          <Button
            variant="primary"
            disabled={scanning}
            onClick={() => scan.mutate()}
          >
            {scanning ? "Scanning…" : "Scan now"}
          </Button>
        </div>
      ) : (
        <>
          <nav className="disk-crumbs" aria-label="Folder path">
            <ol>
              {crumbsFor(focusPath, data).map((crumb, index, all) => (
                <li key={crumb.path || "all"}>
                  {index === all.length - 1 ? (
                    <span aria-current="location">{crumb.label}</span>
                  ) : (
                    <Button
                      variant="plain"
                      className="disk-crumb"
                      onClick={() => openPath(crumb.path)}
                    >
                      {crumb.label}
                    </Button>
                  )}
                </li>
              ))}
            </ol>
          </nav>

          {tree.isError ? (
            <div className="disk-notice is-error" role="alert">
              {describeError(tree.error, "Could not read this folder.")}{" "}
              <Button
                variant="plain"
                className="disk-crumb"
                onClick={() => openPath("")}
              >
                Back to all folders
              </Button>
            </div>
          ) : !focus ? (
            <PaneSkeleton variant="block" label="Loading the map…" />
          ) : (
            <div className="disk-browse">
              <label className="disk-find">
                <span className="disk-sr-only">
                  Find by name in {focus.name}
                </span>
                <Input
                  type="search"
                  value={filter}
                  placeholder={`Find in ${focus.children.length} entries`}
                  onChange={(event) => setFilter(event.target.value)}
                />
              </label>
              <div className="disk-main">
                <section className="disk-map-pane" aria-label="Disk map">
                  <DiskTreemap
                    focus={focus}
                    selected={new Set(marked.keys())}
                    onOpen={openNode}
                    onInspect={setInspected}
                    dimmed={tree.isFetching}
                    filter={filter}
                  />
                  <p className="disk-readout" aria-live="polite">
                    {inspected
                      ? `${inspected.name} · ${formatDiskBytes(inspected.size_bytes)} · ${DISK_KIND_LABELS[inspected.kind]} · ${inspected.file_count.toLocaleString()} files`
                      : `${focus.name} · ${formatDiskBytes(focus.size_bytes)} · ${focus.file_count.toLocaleString()} files`}
                  </p>
                  <ul className="disk-legend" aria-label="Colour key">
                    {DISK_LEGEND_KINDS.map((kind) => (
                      <li key={kind}>
                        <span
                          className={`disk-swatch disk-tile--${kind}`}
                          aria-hidden
                        />
                        {DISK_KIND_LABELS[kind]}
                      </li>
                    ))}
                  </ul>
                </section>
                <section className="disk-table-pane" aria-label="Entries">
                  <DiskEntriesTable
                    focus={focus}
                    rootPaths={rootPaths}
                    selected={new Set(marked.keys())}
                    onToggle={toggleMark}
                    onOpen={openNode}
                    filter={filter}
                  />
                </section>
              </div>
            </div>
          )}

          <details
            className="disk-history"
            onToggle={(event) => setHistoryOpen(event.currentTarget.open)}
          >
            <summary>Recently moved to the Trash</summary>
            {history.isPending ? (
              <p className="disk-history-empty">Loading…</p>
            ) : history.isError ? (
              <p className="disk-notice is-error" role="alert">
                {describeError(
                  history.error,
                  "Could not read the cleanup history.",
                )}
              </p>
            ) : history.data.length === 0 ? (
              <p className="disk-history-empty">
                Nothing yet. Items you clean up are listed here so you can find
                them in the Trash.
              </p>
            ) : (
              <ul className="disk-history-list">
                {history.data.map((event) => (
                  <li key={`${event.created_at}:${event.path}`}>
                    <span className="disk-folders-path">{event.path}</span>
                    <span>{formatDiskBytes(event.size_bytes)}</span>
                    <span className={`disk-outcome is-${event.outcome}`}>
                      {event.outcome}
                    </span>
                    <span>{formatRelativeAge(event.created_at)}</span>
                  </li>
                ))}
              </ul>
            )}
          </details>
        </>
      )}

      {markedPaths.length > 0 ? (
        <div
          className="disk-selection"
          role="region"
          aria-label="Marked for cleanup"
        >
          <span>
            {markedPaths.length} marked · {formatDiskBytes(markedBytes)}
          </span>
          <Button
            variant="secondary"
            compact
            onClick={() => setMarked(new Map())}
          >
            Clear
          </Button>
          <Button
            variant="primary"
            compact
            aria-expanded={cleanupPaths !== null}
            onClick={() => setCleanupPaths([...marked.keys()])}
          >
            Review cleanup…
          </Button>
        </div>
      ) : null}

      <DiskCleanupModal
        open={cleanupPaths !== null}
        paths={cleanupPaths ?? []}
        onClose={() => setCleanupPaths(null)}
        onCleaned={onCleaned}
      />
      <DiskFoldersModal
        open={foldersOpen}
        onClose={() => setFoldersOpen(false)}
        scannedRoots={summary?.roots ?? []}
        added={data.added_roots}
      />
    </div>
  );
}

function ScanNotices({
  summary,
}: {
  summary: NonNullable<DiskOverview["scan"]>;
}) {
  const notices: string[] = [];
  if (summary.truncated)
    notices.push(
      `The scan stopped at ${summary.entries.toLocaleString()} entries; totals are partial.`,
    );
  if (summary.unreadable) {
    notices.push(
      `${summary.unreadable} folder${summary.unreadable === 1 ? " was" : "s were"} unreadable, so totals are a lower bound (${summary.unreadable_samples.slice(0, 2).join("; ")}).`,
    );
  }
  if (summary.unreadable_workspaces.length) {
    notices.push(
      `Could not list worktrees of ${summary.unreadable_workspaces.join(", ")}; ones outside their checkout are missing.`,
    );
  }
  if (summary.missing_added.length) {
    notices.push(
      `Added folder${summary.missing_added.length === 1 ? "" : "s"} no longer there: ${summary.missing_added.join(", ")}.`,
    );
  }
  if (notices.length === 0) return null;
  return (
    <ul className="disk-notice">
      {notices.map((notice) => (
        <li key={notice}>{notice}</li>
      ))}
    </ul>
  );
}
