import { request } from "./http";

/** Whose map: a Baxter chat or a ticket (`MapScope` on the server). */
export type MapScope = "chat" | "ticket";

/** `MapItemKind` on the server. */
export type MapItemKind = "topic" | "question" | "task" | "wait" | "assumption" | "change";

/** `MapItemState` on the server. */
export type MapItemState = "open" | "waiting" | "settled" | "dropped" | "failed";

export interface MapItem {
  id: string;
  /** Place on the vine: handed out on append, never changed. */
  seq: number;
  kind: MapItemKind;
  state: MapItemState;
  source: "record";
  title: string;
  detail: string;
  /** The topic item this one hangs off. */
  topic_id: string | null;
  happened_at: string;
}

/** One source the map was filed from: how many it gave, or why it gave none. */
export interface MapSource {
  name: string;
  count: number;
  ok: boolean;
  error: string;
}

export interface LivingMap {
  scope: MapScope;
  scope_id: string;
  title: string;
  /** In vine order. */
  items: MapItem[];
  sources: MapSource[];
}

export const livingMapApi = {
  get: (scope: MapScope, ref: string) =>
    request<LivingMap>(`/api/living-map/${scope}/${encodeURIComponent(ref)}`),
};
