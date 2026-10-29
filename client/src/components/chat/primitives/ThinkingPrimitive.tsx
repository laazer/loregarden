import { useState } from "react";

import { summarizeTrace } from "../../../lib/thinkingTrace";
import { Button } from "../../ui/Button";
import type { ThinkingPart } from "./types";

import "./ThinkingPrimitive.css";

/**
 * The reasoning behind a reply, folded above it to one line:
 * "›_ Read runner.py and cli.py · 6 tool calls ▾".
 *
 * Tool calls stay one line until opened. The line says what the turn spent
 * its time on and how many calls it made; the full trace (prose and every
 * call, flags and all) is there for the asking. Not Baxter's face: this is
 * part of the reply under it, not a second speaker.
 */
export function ThinkingPrimitive({ part }: { part: ThinkingPart }) {
  const [open, setOpen] = useState(part.collapsed === false);
  const { lead, toolCalls } = summarizeTrace(part.content);
  return (
    <div className="lg-thinking-line">
      <Button
        variant="plain"
        className="lg-thinking-line-head"
        aria-expanded={open}
        onClick={() => setOpen((was) => !was)}
      >
        <code className="lg-thinking-line-glyph" aria-hidden>
          ›_
        </code>
        <span className="lg-thinking-line-lead">{lead}</span>
        {toolCalls > 0 ? (
          <span className="lg-thinking-line-count">
            · {toolCalls} tool {toolCalls === 1 ? "call" : "calls"}
          </span>
        ) : null}
        <span className="lg-thinking-line-caret" aria-hidden>
          {open ? "▴" : "▾"}
        </span>
      </Button>
      {open ? <div className="lg-primitive-thinking lg-thinking-line-body">{part.content}</div> : null}
    </div>
  );
}
