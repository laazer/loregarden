import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { api } from "../../api/client";
import type { TicketArtifactItem } from "../../api/types";
import { placeOutputs } from "../../lib/ticketTimeline";
import { formatLocalTimestamp, parseTimestamp } from "../../lib/timestamps";
import { describeError } from "../../state/toastStore";
import { Button } from "../ui/Button";
import { Input } from "../ui/Input";
import { PaneSkeleton } from "../ui/PaneSkeleton";
import { Select } from "../ui/Select";
import { formatBytes, formatShortTime, openOutput, outputTitle } from "../../lib/ticketOutputs";
import { KindTag, VerdictPill } from "./outputParts";
import "./ArtifactPane.css";

type SortKey = "time" | "kind" | "title" | "stage" | "size";
type SortDir = "ascending" | "descending";

const COLUMNS: { key: SortKey; label: string; numeric?: boolean }[] = [
  { key: "time", label: "Time" },
  { key: "kind", label: "Kind" },
  { key: "title", label: "Title" },
  { key: "stage", label: "Stage" },
  { key: "size", label: "Size", numeric: true },
];

interface Row {
  item: TicketArtifactItem;
  stage: string;
  at: number;
}

function matches(item: TicketArtifactItem, query: string): boolean {
  if (!query) return true;
  const fields = [item.kind, item.title, item.stage_key ?? "", item.evidence_kind, item.commit_sha];
  if (fields.some((field) => field.toLowerCase().includes(query))) return true;
  return JSON.stringify(item.content ?? null).toLowerCase().includes(query);
}

function compare(a: Row, b: Row, key: SortKey): number {
  switch (key) {
    case "time":
      return a.at - b.at;
    case "size":
      return a.item.content_bytes - b.item.content_bytes;
    case "kind":
      return a.item.kind.localeCompare(b.item.kind);
    case "title":
      return outputTitle(a.item).localeCompare(outputTitle(b.item));
    case "stage":
      return a.stage.localeCompare(b.stage);
  }
}

/**
 * "What did the agents produce on this ticket?"
 *
 * Every attachment as a table, newest first. The platform's own bookkeeping —
 * a dispatch marker per stage pass, a "Run context" and a log pointer per run —
 * is half of all rows on a worked ticket and none of the answer, so it is hidden
 * unless asked for. The Stage column says which stage was running when a row
 * landed (exact where the row records its run), so a plan or a piece of
 * evidence can be read against the work it came from.
 */
export function TicketOutputs({ ticketId, isActive }: { ticketId: string; isActive: boolean }) {
  const [kind, setKind] = useState("all");
  const [search, setSearch] = useState("");
  const [showSystem, setShowSystem] = useState(false);
  const [sort, setSort] = useState<{ key: SortKey; dir: SortDir }>({ key: "time", dir: "descending" });

  const feed = useQuery({
    queryKey: ["ticket-artifacts", ticketId],
    queryFn: () => api.ticketArtifacts(ticketId),
    refetchInterval: isActive ? 2000 : false,
  });
  const ledger = useQuery({
    queryKey: ["ticket-ledger", ticketId],
    queryFn: () => api.ticketLedger(ticketId),
    refetchInterval: isActive ? 2000 : false,
  });

  const items = useMemo(() => feed.data?.items ?? [], [feed.data?.items]);
  const visible = useMemo(() => items.filter((item) => showSystem || !item.system), [items, showSystem]);
  const systemCount = items.length - items.filter((item) => !item.system).length;

  const kinds = useMemo(() => {
    const counts = new Map<string, number>();
    for (const item of visible) counts.set(item.kind, (counts.get(item.kind) ?? 0) + 1);
    return [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  }, [visible]);

  const rows = useMemo<Row[]>(() => {
    const visits = ledger.data?.visits ?? [];
    const placement = placeOutputs(visits, visible);
    const query = search.trim().toLowerCase();
    const filtered = visible
      .filter((item) => (kind === "all" || item.kind === kind) && matches(item, query))
      .map((item) => {
        const index = placement.get(item.id) ?? -1;
        const visit = index === -1 ? null : visits[index];
        return {
          item,
          stage: visit ? `${visit.stage_key}${visit.visit_number > 1 ? ` (${visit.visit_number})` : ""}` : "",
          at: parseTimestamp(item.created_at)?.getTime() ?? 0,
        };
      });
    const sign = sort.dir === "ascending" ? 1 : -1;
    return filtered.sort((a, b) => sign * compare(a, b, sort.key) || b.at - a.at);
  }, [ledger.data?.visits, visible, kind, search, sort]);

  if (feed.isPending) return <PaneSkeleton variant="list" rows={8} label="Loading outputs…" />;
  if (feed.isError) {
    return (
      <div className="ap-state" role="alert">
        <div className="ap-state-title">Could not load this ticket&rsquo;s outputs</div>
        <div className="ap-state-body">{describeError(feed.error, "The artifact feed request failed.")}</div>
        <Button variant="secondary" compact onClick={() => void feed.refetch()}>
          Try again
        </Button>
      </div>
    );
  }
  if (items.length === 0) {
    return (
      <div className="ap-state">
        <div className="ap-state-title">No outputs yet</div>
        <div className="ap-state-body">
          Plans, stage reports, handoffs, test results and evidence appear here as agents attach them.
        </div>
      </div>
    );
  }

  const filtering = kind !== "all" || search.trim() !== "";
  const toggleSort = (key: SortKey) =>
    setSort((cur) =>
      cur.key === key
        ? { key, dir: cur.dir === "ascending" ? "descending" : "ascending" }
        : { key, dir: key === "time" || key === "size" ? "descending" : "ascending" },
    );

  return (
    <div className="out">
      <div className="out-toolbar">
        <Input
          type="search"
          className="out-search"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="Search titles and content"
          aria-label="Search outputs"
        />
        <Select aria-label="Filter by kind" value={kind} onChange={(event) => setKind(event.target.value)}>
          <option value="all">All kinds ({visible.length})</option>
          {kinds.map(([name, count]) => (
            <option key={name} value={name}>
              {name} ({count})
            </option>
          ))}
        </Select>
        {systemCount > 0 ? (
          <label className="out-system">
            <Input type="checkbox" checked={showSystem} onChange={(event) => setShowSystem(event.target.checked)} />
            Show {systemCount} system record{systemCount === 1 ? "" : "s"}
          </label>
        ) : null}
        <span className="out-count" aria-live="polite">
          {filtering ? `${rows.length} of ${visible.length}` : `${visible.length}`} output
          {visible.length === 1 ? "" : "s"}
        </span>
      </div>

      {ledger.isError ? (
        <div className="out-note" role="status">
          Stages unavailable — the run ledger did not load ({describeError(ledger.error, "request failed")}).
        </div>
      ) : null}

      {rows.length === 0 ? (
        <div className="ap-state ap-state--inline">
          <div className="ap-state-body">
            {visible.length === 0 ? "Every row on this ticket is a system record." : "No outputs match these filters."}
          </div>
          {filtering ? (
            <Button
              variant="secondary"
              compact
              onClick={() => {
                setKind("all");
                setSearch("");
              }}
            >
              Clear filters
            </Button>
          ) : null}
        </div>
      ) : (
        <table className="out-table">
          <thead>
            <tr>
              {COLUMNS.map((column) => (
                <th
                  key={column.key}
                  scope="col"
                  className={`out-col-${column.key}`}
                  aria-sort={sort.key === column.key ? sort.dir : "none"}
                >
                  <Button variant="plain" className="out-sort" onClick={() => toggleSort(column.key)}>
                    {column.label}
                    <span aria-hidden className="out-sort-mark">
                      {sort.key === column.key ? (sort.dir === "ascending" ? "↑" : "↓") : ""}
                    </span>
                  </Button>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map(({ item, stage }) => (
              <tr key={item.id} className={item.system ? "out-row--system" : undefined}>
                <td className="out-col-time" title={formatLocalTimestamp(item.created_at)}>
                  {formatShortTime(item.created_at)}
                </td>
                <td className="out-col-kind">
                  <KindTag item={item} />
                </td>
                <td className="out-col-title">
                  <Button variant="plain" className="out-title" onClick={() => openOutput(item, stage)}>
                    <span className="out-title-text">{outputTitle(item)}</span>
                    <VerdictPill item={item} />
                  </Button>
                </td>
                <td className="out-col-stage">{stage || "—"}</td>
                <td className="out-col-size">{formatBytes(item.content_bytes)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
