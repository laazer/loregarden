/**
 * The living map's rules, kept out of React so they can be tested alone.
 *
 * The vine is the conversation's order: items are drawn by `seq`, one fixed-
 * height row each, so a row's place is its index and nothing is measured. A
 * lens hides what does not answer its question; within a lens the order, and
 * so every row's place, still comes from `seq` alone (append-stable, as
 * living-map's layout is: `harshit-pi/living-map` at 81df5a7).
 */

import type { MapItem } from "../api/livingMapApi";
import type { VineMark } from "./vine";

export type MapLens = "all" | "waiting" | "open" | "decided" | "changed";

export const LENSES: { key: MapLens; label: string; question: string }[] = [
  { key: "all", label: "All", question: "Everything on this map, in the order it happened" },
  { key: "waiting", label: "Waiting", question: "What waits on you" },
  { key: "open", label: "Open", question: "What is still open" },
  { key: "decided", label: "Decided", question: "What was answered, decided or done" },
  { key: "changed", label: "Changed", question: "What the runs did" },
];

export function inLens(item: MapItem, lens: MapLens): boolean {
  switch (lens) {
    case "all":
      return true;
    case "waiting":
      return item.state === "waiting";
    case "open":
      return item.state === "open" && item.kind !== "topic" && item.kind !== "change";
    case "decided":
      return item.state === "settled" && item.kind !== "topic" && item.kind !== "change";
    case "changed":
      return item.kind === "change";
  }
}

export function lensCounts(items: readonly MapItem[]): Record<MapLens, number> {
  const counts = { all: items.length, waiting: 0, open: 0, decided: 0, changed: 0 };
  for (const item of items) {
    for (const { key } of LENSES) if (key !== "all" && inLens(item, key)) counts[key] += 1;
  }
  return counts;
}

/** A topic is a fork in the vine; every other item grows the mark its state says. */
export function itemMark(item: MapItem): VineMark {
  if (item.kind === "topic") return "fork";
  switch (item.state) {
    case "settled":
      return "leaf";
    case "waiting":
      return "bud-held";
    case "failed":
      return "bud-blocked";
    case "dropped":
      return "pruned";
    case "open":
      return "bud-open";
  }
}

/** Every row is this tall, so a row's top is its index times this. */
export const MAP_ROW_PX = 32;

/**
 * A topic's tendril: from the topic's row down the right-hand gutter, with a
 * curl into each row that hangs off it. Rows are given by their index in the
 * drawn list, so the path follows from the data alone; a topic whose rows are
 * all hidden by the lens draws nothing.
 */
export function tendrilPath(topicRow: number, memberRows: readonly number[], gutter: number): string {
  if (memberRows.length === 0) return "";
  const y = (row: number) => row * MAP_ROW_PX + MAP_ROW_PX / 2;
  const last = Math.max(...memberRows);
  const spine = `M 0 ${y(topicRow)} C ${gutter} ${y(topicRow)}, ${gutter} ${y(topicRow)}, ${gutter} ${y(topicRow) + MAP_ROW_PX / 2} V ${y(last) - MAP_ROW_PX / 2}`;
  const curls = memberRows
    .map((row) => `M ${gutter} ${y(row) - MAP_ROW_PX / 2} C ${gutter} ${y(row)}, ${gutter} ${y(row)}, 0 ${y(row)}`)
    .join(" ");
  return `${spine} ${curls}`;
}
