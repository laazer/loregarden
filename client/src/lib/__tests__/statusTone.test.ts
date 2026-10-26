/**
 * Every status vocabulary speaks through the six tones, so a hue means one
 * thing on every surface. Two failure modes are guarded here: a status that
 * falls through to the idle fallback because nobody mapped it, and green
 * creeping back in as a status colour (it is the accent's).
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";

import type { StageStatus, TicketActivity, TicketState } from "../../api/types";
import { STATUS_TONE_COLORS } from "../statusTone";
import {
  TICKET_ACTIVITY_COLORS,
  TICKET_STATE_COLORS,
  boardColumnColor,
  runStatusChip,
  stageStatusChip,
  stageStatusColor,
  ticketActivityChip,
  ticketStateChip,
} from "../ticketStates";

const TICKET_STATES: TicketState[] = ["backlog", "in_progress", "blocked", "parked", "done", "wont_do"];
const ACTIVITIES: TicketActivity[] = ["running", "awaiting", "queued", "idle"];
const STAGE_STATUSES: StageStatus[] = ["pending", "running", "blocked", "awaiting", "done", "wont_do"];
const RUN_STATUSES = [
  "queued",
  "running",
  "awaiting_permission",
  "succeeded",
  "failed",
  "cancelled",
  "blocked",
  "repairing",
];

describe("status tones", () => {
  it("names each meaning once: running, failed, waiting and needs-you", () => {
    expect(ticketActivityChip("running").tone).toBe("run");
    expect(runStatusChip("running").tone).toBe("run");
    expect(stageStatusChip("running").tone).toBe("run");

    expect(runStatusChip("failed").tone).toBe("bad");
    expect(ticketStateChip("blocked").tone).toBe("bad");
    expect(stageStatusChip("blocked").tone).toBe("bad");

    expect(runStatusChip("queued").tone).toBe("wait");
    expect(ticketActivityChip("queued").tone).toBe("wait");

    expect(runStatusChip("awaiting_permission").tone).toBe("you");
    expect(ticketActivityChip("awaiting").tone).toBe("you");
    expect(stageStatusChip("awaiting").tone).toBe("you");
    expect(ticketStateChip("parked").tone).toBe("you");

    expect(runStatusChip("succeeded").tone).toBe("done");
    expect(ticketStateChip("done").tone).toBe("done");
  });

  it("keeps an open ticket quiet: in progress is not running", () => {
    // Most in-progress tickets are idle between stages; activity says when one runs.
    expect(ticketStateChip("in_progress").tone).toBe("idle");
  });

  it("reads an unknown status as idle and still says what it is", () => {
    expect(runStatusChip("exploded")).toEqual({ tone: "idle", label: "exploded" });
  });

  it("never colours a status green", () => {
    const hues = [
      ...Object.values(STATUS_TONE_COLORS),
      ...TICKET_STATES.map((s) => TICKET_STATE_COLORS[s]),
      ...ACTIVITIES.map((a) => TICKET_ACTIVITY_COLORS[a]),
      ...STAGE_STATUSES.map((s) => stageStatusColor(s)),
      boardColumnColor("needs_you"),
      boardColumnColor("waiting"),
    ];
    for (const hue of hues) expect(hue).not.toMatch(/--(grn|grl|ac|ac2)\b/);
  });

  it("maps every run status, rather than leaving it to the fallback", () => {
    const fallthrough = RUN_STATUSES.filter(
      (status) => runStatusChip(status).tone === "idle" && status !== "cancelled",
    );
    expect(fallthrough).toEqual([]);
  });
});

// The chip's text sits on its own tint over every resting surface; each pairing
// must clear AA for body text. Values are read from index.css so a token change
// that breaks a pairing fails here.
const css = readFileSync(join(__dirname, "..", "..", "index.css"), "utf8");
const root = /:root\s*\{([\s\S]*?)\n\}/.exec(css)?.[1] ?? "";

function token(name: string): string {
  const match = new RegExp(`--${name}:\\s*(#[0-9a-fA-F]{6});`).exec(root);
  if (!match) throw new Error(`index.css :root defines no hex --${name}`);
  return match[1];
}

function channels(hex: string): number[] {
  return [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
}

function luminance(rgb: number[]): number {
  const [r, g, b] = rgb.map((v) => {
    const c = v / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrast(a: number[], b: number[]): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

/** `color-mix(in srgb, hue p%, transparent)` composited over a surface. */
function tint(hue: string, percent: number, surface: string): number[] {
  const h = channels(hue);
  return channels(surface).map((s, i) => h[i] * (percent / 100) + s * (1 - percent / 100));
}

// [tone, text token, hue token, tint %], as StatusChip.css sets them.
const TINTED: Array<[string, string, string, number]> = [
  ["run", "bll", "blue", 14],
  ["bad", "rdl", "red", 14],
  ["wait", "aml", "amb", 14],
  ["you", "vil", "vio", 16],
];

describe.each(["bg0", "bg1", "bg2", "bg3"])("a chip on --%s", (surface) => {
  it.each(TINTED)("%s text (--%s on --%s at %i%%) clears 4.5:1", (_tone, text, hue, percent) => {
    expect(contrast(channels(token(text)), tint(token(hue), percent, token(surface)))).toBeGreaterThanOrEqual(4.5);
  });

  it("done text (--txm on --bg3) clears 4.5:1", () => {
    expect(contrast(channels(token("txm")), channels(token("bg3")))).toBeGreaterThanOrEqual(4.5);
  });

  it("idle text (--txl, untinted) clears 4.5:1", () => {
    expect(contrast(channels(token("txl")), channels(token(surface)))).toBeGreaterThanOrEqual(4.5);
  });
});
