import { useMemo, useState, type CSSProperties } from "react";
import { useQuery } from "@tanstack/react-query";

import { api } from "../../api/client";
import type { TicketArtifactItem } from "../../api/types";

/**
 * Shared by header + every row. Time is 24h (no AM/PM) so it fits;
 * kind uses ch units on the mono font so short labels don't leave a dead zone.
 */
const ROW_GRID = "72px 13ch minmax(0, 1fr) 56px";
const ROW_GAP = 16;

const cellMono: CSSProperties = {
  fontFamily: "var(--mono)",
  fontVariantNumeric: "tabular-nums",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
  minWidth: 0,
};

function formatWhen(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  // 24h keeps the time column a fixed width; AM/PM overflowed the old 88px track.
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
}

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

function artifactMatches(item: TicketArtifactItem, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  const haystacks = [item.kind, item.title, item.id, item.run_id ?? "", item.evidence_kind, item.commit_sha];
  if (haystacks.some((h) => h.toLowerCase().includes(q))) return true;
  try {
    return JSON.stringify(item.content).toLowerCase().includes(q);
  } catch {
    return String(item.content).toLowerCase().includes(q);
  }
}

function ArtifactRow({
  item,
  expanded,
  onToggle,
  onOpenRunLog,
}: {
  item: TicketArtifactItem;
  expanded: boolean;
  onToggle: () => void;
  onOpenRunLog?: (runId: string) => void;
}) {
  const preview = useMemo(() => {
    try {
      return JSON.stringify(item.content, null, 2);
    } catch {
      return String(item.content);
    }
  }, [item.content]);

  return (
    <li style={{ paddingTop: 6 }}>
      <button
        type="button"
        className={`list-btn${expanded ? " active" : ""}`}
        onClick={onToggle}
        aria-expanded={expanded}
        style={{
          display: "grid",
          gridTemplateColumns: ROW_GRID,
          columnGap: ROW_GAP,
          alignItems: "center",
          width: "100%",
          boxSizing: "border-box",
          margin: 0,
          textAlign: "left",
          padding: "11px 16px",
          cursor: "pointer",
        }}
      >
        <span style={{ ...cellMono, fontSize: 11, color: "var(--txl)" }}>{formatWhen(item.created_at)}</span>
        <span
          title={item.kind}
          style={{ ...cellMono, fontSize: 11, color: "var(--ac2, var(--tx))", textTransform: "lowercase" }}
        >
          {item.kind}
        </span>
        <span style={{ ...cellMono, fontSize: 12.5, color: "var(--tx)" }} title={item.title || item.kind}>
          {item.title || "(untitled)"}
        </span>
        <span style={{ ...cellMono, fontSize: 11, color: "var(--txl)", textAlign: "right" }}>
          {formatBytes(item.content_bytes)}
        </span>
      </button>
      {expanded && (
        <div style={{ padding: "10px 16px 12px", display: "flex", flexDirection: "column", gap: 8 }}>
          <div
            style={{
              display: "flex",
              flexWrap: "wrap",
              gap: 10,
              fontFamily: "var(--mono)",
              fontSize: 10.5,
              color: "var(--txl)",
            }}
          >
            <span>id {item.id.slice(0, 8)}</span>
            {item.run_id && onOpenRunLog ? (
              <button
                type="button"
                className="btn-secondary"
                style={{ fontSize: 10.5, padding: "2px 8px" }}
                onClick={() => onOpenRunLog(item.run_id!)}
              >
                View run log
              </button>
            ) : item.run_id ? (
              <span>run {item.run_id.slice(0, 8)}</span>
            ) : null}
            {item.evidence_kind ? <span>evidence {item.evidence_kind}</span> : null}
            {item.commit_sha ? <span>sha {item.commit_sha.slice(0, 8)}</span> : null}
          </div>
          <pre
            style={{
              margin: 0,
              padding: 12,
              borderRadius: 6,
              background: "var(--bg2, rgba(0,0,0,.2))",
              border: "1px solid var(--bd)",
              fontFamily: "var(--mono)",
              fontSize: 11.5,
              lineHeight: 1.5,
              whiteSpace: "pre-wrap",
              wordBreak: "break-word",
              maxHeight: 360,
              overflow: "auto",
              color: "var(--tx)",
            }}
          >
            {preview}
          </pre>
        </div>
      )}
    </li>
  );
}

/**
 * Raw artifact feed — every attach, including ad-hoc kinds Diff/Context tabs ignore.
 * Polls while a run is active so local-model attach storms are visible as they land.
 */
export function ArtifactsPanel({
  ticketId,
  isActive,
  onOpenRunLog,
}: {
  ticketId: string;
  isActive?: boolean;
  onOpenRunLog?: (runId: string) => void;
}) {
  const [kindFilter, setKindFilter] = useState<string>("all");
  const [search, setSearch] = useState("");
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const feed = useQuery({
    queryKey: ["ticket-artifacts", ticketId],
    queryFn: () => api.ticketArtifacts(ticketId),
    refetchInterval: isActive ? 2000 : false,
  });

  const kinds = useMemo(() => {
    const counts = new Map<string, number>();
    for (const item of feed.data?.items ?? []) {
      counts.set(item.kind, (counts.get(item.kind) ?? 0) + 1);
    }
    return [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  }, [feed.data?.items]);

  const items = useMemo(() => {
    const all = feed.data?.items ?? [];
    return all.filter((item) => {
      if (kindFilter !== "all" && item.kind !== kindFilter) return false;
      return artifactMatches(item, search);
    });
  }, [feed.data?.items, kindFilter, search]);

  if (feed.isPending) {
    return <div style={{ padding: 16, color: "var(--txl)" }}>Loading artifacts…</div>;
  }
  if (feed.isError) {
    return (
      <div style={{ padding: 16, color: "var(--txl)" }}>Could not load this ticket&rsquo;s artifacts.</div>
    );
  }
  if (!feed.data?.items.length) {
    return (
      <div
        style={{
          height: "100%",
          minHeight: 340,
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          color: "var(--txl)",
          gap: 12,
          padding: 24,
        }}
      >
        <div style={{ fontFamily: "var(--dp)", fontSize: 14, color: "var(--txm)" }}>No artifacts yet</div>
        <div style={{ fontSize: 12.5, textAlign: "center", maxWidth: 320, lineHeight: 1.55 }}>
          Attachments from agent runs show up here as they land — including custom kinds that never
          appear under Diff or Context.
        </div>
      </div>
    );
  }

  const filteredLabel =
    items.length === feed.data.total
      ? `${feed.data.total} artifact${feed.data.total === 1 ? "" : "s"}`
      : `${items.length} of ${feed.data.total}`;

  return (
    <div style={{ padding: 16, display: "flex", flexDirection: "column", gap: 12, minHeight: 0 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <div style={{ fontFamily: "var(--dp)", fontSize: 13, color: "var(--txm)", whiteSpace: "nowrap" }}>
          {filteredLabel}
          {isActive ? " · live" : ""}
        </div>
        <input
          type="search"
          className="ticket-search"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search title, kind, content…"
          aria-label="Search artifacts"
          style={{ flex: "1 1 180px", minWidth: 160, maxWidth: 360, padding: "6px 10px" }}
        />
      </div>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        <button
          type="button"
          className="btn-secondary"
          aria-pressed={kindFilter === "all"}
          style={{ fontSize: 10.5, padding: "2px 8px" }}
          onClick={() => setKindFilter("all")}
        >
          all
        </button>
        {kinds.map(([kind, count]) => (
          <button
            key={kind}
            type="button"
            className="btn-secondary"
            aria-pressed={kindFilter === kind}
            style={{ fontSize: 10.5, padding: "2px 8px", fontFamily: "var(--mono)" }}
            onClick={() => setKindFilter(kind)}
          >
            {kind} · {count}
          </button>
        ))}
      </div>
      <div
        aria-hidden
        style={{
          display: "grid",
          gridTemplateColumns: ROW_GRID,
          columnGap: ROW_GAP,
          alignItems: "center",
          padding: "0 16px 4px",
          fontFamily: "var(--mono)",
          fontSize: 10,
          color: "var(--txl)",
          textTransform: "uppercase",
          letterSpacing: "0.04em",
        }}
      >
        <span>Time</span>
        <span>Kind</span>
        <span>Title</span>
        <span style={{ textAlign: "right" }}>Size</span>
      </div>
      {items.length === 0 ? (
        <div style={{ padding: "24px 0", color: "var(--txl)", fontSize: 12.5, textAlign: "center" }}>
          No artifacts match this filter.
        </div>
      ) : (
        <ul style={{ margin: 0, padding: 0, listStyle: "none" }}>
          {items.map((item) => (
            <ArtifactRow
              key={item.id}
              item={item}
              expanded={expandedId === item.id}
              onToggle={() => setExpandedId((cur) => (cur === item.id ? null : item.id))}
              onOpenRunLog={onOpenRunLog}
            />
          ))}
        </ul>
      )}
    </div>
  );
}
