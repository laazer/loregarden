/**
 * The one adapter between a chat primitive and a view container (557).
 *
 * ## What actually has to be bridged
 *
 * A chat primitive renders a `part` — a payload an agent wrote inside a turn. A
 * view container renders from `settings` and fetches its own data (436's
 * decision, and what lets the grid and the canvas stay ignorant of what a pane
 * holds). So the bridge is three things, and this module is all three of them
 * exactly once rather than thirteen times:
 *
 *   1. **settings → part.** Each registration supplies `toPart`, which is the
 *      only per-primitive code: a hand-written literal, so the part it builds
 *      is checked against the chat vocabulary by the compiler and no entry
 *      needs a cast.
 *   2. **an identifier the operator has not supplied yet.** `missing` returns
 *      the sentence a pane shows before it is configured. Without it, a
 *      freshly dropped `ticket` pane would call `api.ticket("")` — a request
 *      for a ticket that cannot exist — and then render a "not found" error
 *      that reads like a bug rather than an empty field.
 *   3. **navigation the pane must not offer.** See `resourceNavigation`: an
 *      `Open ticket` control inside a pane navigates the whole app off
 *      `/view/:viewId` and takes every other pane in the composed view with
 *      it. The provider here is what turns those controls off, and it is set
 *      once here so no registration can forget it.
 *
 * ## Sizing
 *
 * `.pane-chat-primitive` is the fill-and-scroll box, in `paneChrome.css` beside
 * the components — the suites that pin pane sizing walk that directory and
 * parse the CSS there. A chat card was laid out for a thread's reading measure
 * and has no height of its own, so the box supplies `min-height: 0` and lets
 * the card reflow inside it. Nothing here asserts a pixel height.
 *
 * ## Why it is not a re-export
 *
 * Reusing the chat component rather than reimplementing it keeps one answer to
 * "what does a ticket card look like". Thirteen reimplementations would be
 * ~1700 lines that drift apart the first time either surface is restyled.
 */

import type { ComponentType } from "react";

import { ResourceNavigationContext } from "../../chat/primitives/resourceNavigation";
import type { ChatPart, PrimitiveKind } from "../../chat/primitives/types";
import "../paneChrome.css";
import { definePrimitive } from "./definePrimitive";
import type { ParsedSettings, RegisteredPrimitive, SettingsField } from "./types";
import { Unconfigured } from "./Unconfigured";

/** The chat part a registration of `TKind` builds and its component consumes. */
type PartOf<TKind extends PrimitiveKind> = Extract<ChatPart, { primitive: TKind }>;

/**
 * The registry id a chat primitive is registered under.
 *
 * Prefixed rather than reusing the bare kind, because the two vocabularies
 * already collide: `terminal` names a chat card that replays a recorded
 * transcript *and* 436's primitive that attaches to a live shell, and the
 * registry is keyed on one string. Derived rather than hand-written so an entry
 * cannot claim to adapt one kind under another's id.
 */
export function chatPaneId(kind: PrimitiveKind): string {
  return `chat_${kind}`;
}

export interface ChatPanePrimitiveSpec<
  TSettings extends ParsedSettings,
  TKind extends PrimitiveKind,
> {
  /**
   * The chat primitive this pane adapts. It fixes the registry id, and — via
   * `PartOf` — the exact part `toPart` must build and `Chat` must accept, so a
   * registration cannot pair one component with another's payload.
   */
  kind: TKind;
  displayName: string;
  icon: string;
  category: string;
  settingsFields: SettingsField[];
  parseSettings: (raw: Record<string, unknown>) => TSettings;
  /** The chat component this pane renders, unmodified. */
  Chat: ComponentType<{ part: PartOf<TKind> }>;
  /** The part that component is handed, built from the pane's own settings. */
  toPart: (settings: TSettings) => PartOf<TKind>;
  /**
   * What the pane is still waiting for, or `null` when it can render.
   *
   * A sentence rather than a boolean because each primitive is waiting for a
   * different thing, and "not configured" says less than "has no ticket yet".
   */
  missing: (settings: TSettings) => string | null;
}

/**
 * Register a chat primitive as a view container.
 *
 * Generic over both the parsed settings and the chat kind, so `toPart` is
 * checked against that kind's own part type at the definition site. The generics
 * are erased by `definePrimitive`, which is still the only place that happens.
 *
 * Every primitive built this way is a `panel`: it draws a card, not a shell and
 * not a frame.
 */
export function defineChatPanePrimitive<
  TSettings extends ParsedSettings,
  TKind extends PrimitiveKind,
>(spec: ChatPanePrimitiveSpec<TSettings, TKind>): RegisteredPrimitive {
  const { Chat, toPart, missing } = spec;

  return definePrimitive<TSettings>({
    id: chatPaneId(spec.kind),
    displayName: spec.displayName,
    icon: spec.icon,
    category: spec.category,
    containerKind: "panel",
    settingsFields: spec.settingsFields,
    parseSettings: spec.parseSettings,
    Component: ({ settings }) => {
      const waitingFor = missing(settings);
      if (waitingFor !== null) return <Unconfigured>{waitingFor}</Unconfigured>;

      return (
        <ResourceNavigationContext.Provider value={false}>
          <div className="pane-chat-primitive">
            <Chat part={toPart(settings)} />
          </div>
        </ResourceNavigationContext.Provider>
      );
    },
  });
}

/** The stored value of `key`, when it is a string. Otherwise `""`. */
export function settingString(raw: Record<string, unknown>, key: string): string {
  const value = raw[key];
  return typeof value === "string" ? value : "";
}

/**
 * The stored value of `key` as a positive whole count, or `fallback`.
 *
 * A count is the only numeric setting any of these primitives take, and every
 * one of them is a `limit` on a fetch. A stored `0`, a negative, or the `null`
 * 444 established the server hands back for a non-finite number would each ask
 * for a page of nothing, so they all fall back rather than reaching the wire.
 */
export function settingCount(raw: Record<string, unknown>, key: string, fallback: number): number {
  const value = raw[key];
  if (typeof value !== "number" || !Number.isFinite(value) || value < 1) return fallback;
  return Math.floor(value);
}

/** Settings text an operator has not filled in yet, once. */
export function blank(value: string): boolean {
  return value.trim() === "";
}
