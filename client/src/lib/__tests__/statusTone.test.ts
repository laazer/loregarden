/**
 * Every status vocabulary speaks through the six tones, so a hue means one
 * thing on every surface. Two failure modes are guarded here: a status that
 * falls through to the idle fallback because nobody mapped it, and green
 * creeping back in as a status colour (it is the accent's).
 */

import type { StageStatus, TicketActivity, TicketState } from "../../api/types";
import { channels, contrast, tint, token } from "../../test/contrast";
import { STATUS_TONE_COLORS } from "../statusTone";
import {
  TICKET_ACTIVITY_COLORS,
  TICKET_STATE_COLORS,
  boardColumnColor,
  runStatusChip,
  stageStatusChip,
  stageStatusColor,
  stageStatusTextColor,
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

  it("keeps started work apart from backlog", () => {
    // Tree and work-items dots carry no word, so the hue alone must separate them.
    expect(ticketStateChip("in_progress").tone).toBe("run");
    expect(ticketStateChip("backlog").tone).toBe("idle");
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
// must clear AA for body text. The arithmetic and the token reads live in
// `src/test/contrast.ts`, shared with themeContrast.test.ts: three copies of a
// WCAG formula in one suite is three chances for them to disagree.

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

// Stage names are text, and the dot palette's quiet grey is a border token
// (2:1 on --bg0). Every stage status, and an unknown one, must name a text
// colour that clears AA on every resting surface.
describe.each(["bg0", "bg1", "bg2", "bg3"])("a stage name on --%s", (surface) => {
  it.each([...STAGE_STATUSES, "unmapped"])("%s clears 4.5:1", (status) => {
    const name = /^var\(--([a-z0-9]+)\)$/.exec(stageStatusTextColor(status))?.[1];
    if (!name) throw new Error(`stage text colour for ${status} is not a single token`);
    expect(contrast(channels(token(name)), channels(token(surface)))).toBeGreaterThanOrEqual(4.5);
  });
});
