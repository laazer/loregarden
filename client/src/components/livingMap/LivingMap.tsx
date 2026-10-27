import { useQuery } from "@tanstack/react-query";
import { useId, useMemo, useState } from "react";

import { livingMapApi, type MapItem, type MapScope } from "../../api/livingMapApi";
import {
  LENSES,
  MAP_ROW_PX,
  inLens,
  itemMark,
  lensCounts,
  tendrilPath,
  type MapLens,
} from "../../lib/livingMap";
import { formatRelativeAge } from "../../lib/timestamps";
import { describeError } from "../../state/toastStore";
import { Button } from "../ui/Button";
import { Input } from "../ui/Input";
import { PaneSkeleton } from "../ui/PaneSkeleton";
import { VineNode } from "../vine/VineNode";

import "./LivingMap.css";

/** Past this many items a filter sits beside the lenses. */
const FILTER_THRESHOLD = 50;
/** The tendril gutter's width, right of the rows. */
const GUTTER_PX = 14;

const EMPTY_TEXT: Record<MapScope, string> = {
  chat: "Nothing on this map yet. Plans, questions and the tickets this chat talks about appear here as they happen.",
  ticket:
    "Nothing on this map yet. Questions, sign-offs, agent runs and the assumptions they record appear here as the ticket runs.",
};

const KIND_LABEL: Record<MapItem["kind"], string> = {
  topic: "Topic",
  question: "Question",
  task: "Task",
  wait: "Waiting on you",
  assumption: "Assumption",
  change: "Run",
};

const STATE_LABEL: Record<MapItem["state"], string> = {
  open: "Open",
  waiting: "Waits on you",
  settled: "Settled",
  dropped: "Dropped",
  failed: "Failed",
};

/**
 * A conversation's living map: what waits on the operator, what is open, and
 * what was settled, drawn as a vine in the order it happened, with each topic
 * a tendril tying its items together.
 *
 * Rows are one fixed height and drawn in `seq` order, so where a row sits
 * follows from the data alone and a new item only extends the tip. A lens
 * hides what does not answer its question; focusing a topic dims the rest and
 * draws its tendril, and moves nothing.
 */
export function LivingMap({ scope, scopeRef }: { scope: MapScope; scopeRef: string }) {
  const map = useQuery({
    queryKey: ["living-map", scope, scopeRef],
    queryFn: () => livingMapApi.get(scope, scopeRef),
    enabled: Boolean(scopeRef),
    refetchInterval: 15_000,
  });
  const [lens, setLens] = useState<MapLens>("all");
  const [filter, setFilter] = useState("");
  const [topicId, setTopicId] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const lensGroupId = useId();
  const detailId = useId();

  const items = useMemo(() => map.data?.items ?? [], [map.data]);
  const counts = useMemo(() => lensCounts(items), [items]);
  const topics = useMemo(() => new Map(items.filter((i) => i.kind === "topic").map((i) => [i.id, i])), [items]);
  const needle = filter.trim().toLowerCase();
  const rows = items.filter((item) => inLens(item, lens) && (!needle || item.title.toLowerCase().includes(needle)));
  const selected = items.find((item) => item.id === selectedId) ?? null;

  if (map.isLoading) return <PaneSkeleton variant="list" label="Reading this map…" />;
  if (map.error) {
    return (
      <div className="living-map-failed" role="alert">
        <p>{describeError(map.error, "Couldn't load this map")}</p>
        <Button variant="secondary" compact onClick={() => void map.refetch()}>
          Try again
        </Button>
      </div>
    );
  }

  const problems = (map.data?.sources ?? []).filter((source) => !source.ok);
  const topicRow = topicId ? rows.findIndex((row) => row.id === topicId) : -1;
  const memberRows = topicId
    ? rows.flatMap((row, index) => (row.topic_id === topicId ? [index] : []))
    : [];

  return (
    <section className="living-map" aria-label={`Living map: ${map.data?.title ?? ""}`}>
      {items.length === 0 ? (
        <p className="living-map-empty">{EMPTY_TEXT[scope]}</p>
      ) : (
        <>
          <div className="living-map-tools">
            <div className="living-map-lenses" role="group" aria-labelledby={lensGroupId}>
              <span id={lensGroupId} className="living-map-sr">
                Show
              </span>
              {/* ux-ok: LENSES is a fixed list of five, never fetched. */}
              {LENSES.map((option) => (
                <Button
                  key={option.key}
                  variant="plain"
                  className={`living-map-lens${lens === option.key ? " is-on" : ""}`}
                  aria-pressed={lens === option.key}
                  title={option.question}
                  onClick={() => setLens(option.key)}
                >
                  {option.label}
                  <span className="living-map-lens-count">{counts[option.key]}</span>
                </Button>
              ))}
            </div>
            {items.length > FILTER_THRESHOLD ? (
              <Input
                className="living-map-filter"
                aria-label="Filter the map"
                placeholder="Filter by words"
                value={filter}
                onChange={(event) => setFilter(event.target.value)}
              />
            ) : null}
          </div>

          {rows.length === 0 ? (
            <p className="living-map-empty">
              {needle ? `Nothing here matches “${filter}”.` : `Nothing here: ${LENSES.find((l) => l.key === lens)?.question.toLowerCase()}.`}
            </p>
          ) : (
            <div className="living-map-vine" style={{ height: rows.length * MAP_ROW_PX }}>
              <ol className="living-map-rows" aria-label={`${rows.length} of ${items.length} items, in the order they happened`}>
                {rows.map((item, index) => {
                  const topic = item.topic_id ? topics.get(item.topic_id) : undefined;
                  const dim = topicId !== null && item.id !== topicId && item.topic_id !== topicId;
                  return (
                    <li
                      key={item.id}
                      className={`living-map-row is-${item.state}${dim ? " is-dim" : ""}${item.id === selectedId ? " is-selected" : ""}`}
                      style={{ height: MAP_ROW_PX }}
                    >
                      <VineNode index={item.seq} mark={itemMark(item)} last={index === rows.length - 1} />
                      <Button
                        variant="plain"
                        className={`living-map-title${item.kind === "topic" ? " is-topic" : ""}`}
                        aria-expanded={item.id === selectedId}
                        aria-controls={item.id === selectedId ? detailId : undefined}
                        title={item.title}
                        onClick={() => {
                          setSelectedId(item.id === selectedId ? null : item.id);
                          if (item.kind === "topic") setTopicId(item.id === topicId ? null : item.id);
                        }}
                      >
                        <span className="living-map-sr">{`${KIND_LABEL[item.kind]}, ${STATE_LABEL[item.state]}: `}</span>
                        {item.title}
                      </Button>
                      {topic ? (
                        <Button
                          variant="plain"
                          className={`living-map-topic${topic.id === topicId ? " is-on" : ""}`}
                          aria-pressed={topic.id === topicId}
                          title={`Show what hangs off ${topic.title}`}
                          onClick={() => setTopicId(topic.id === topicId ? null : topic.id)}
                        >
                          {topic.title}
                        </Button>
                      ) : null}
                      <span className="living-map-age">{formatRelativeAge(item.happened_at)}</span>
                    </li>
                  );
                })}
              </ol>
              {topicRow >= 0 ? (
                <svg
                  className="living-map-tendril"
                  width={GUTTER_PX}
                  height={rows.length * MAP_ROW_PX}
                  aria-hidden
                >
                  <path d={tendrilPath(topicRow, memberRows, GUTTER_PX - 2)} />
                </svg>
              ) : null}
            </div>
          )}

          {selected ? (
            <div id={detailId} className="living-map-detail">
              <dl>
                <dt>What</dt>
                <dd>
                  {KIND_LABEL[selected.kind]} · {STATE_LABEL[selected.state]}
                </dd>
                <dt>When</dt>
                <dd>{new Date(selected.happened_at).toLocaleString()}</dd>
                {selected.topic_id && topics.get(selected.topic_id) ? (
                  <>
                    <dt>Topic</dt>
                    <dd>{topics.get(selected.topic_id)?.title}</dd>
                  </>
                ) : null}
              </dl>
              {selected.detail && selected.detail !== selected.title ? (
                <p className="living-map-detail-text">{selected.detail}</p>
              ) : null}
            </div>
          ) : null}
        </>
      )}

      {problems.length ? (
        <ul className="living-map-problems" role="status">
          {/* ux-ok: shown only when a source failed; the map above has its own empty state. */}
          {problems.map((source) => (
            <li key={source.name}>{source.error}</li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}
