import { useEffect, useId, useRef, useState } from "react";

import { Button } from "../ui/Button";
import { parseThinkingBlocks, summarizeThinking } from "../../lib/thinkingTrace";

import "./LiveThinkingStream.css";

/**
 * Baxter's reasoning while he is still reasoning, as one line.
 *
 * The line says what he is doing and how far along he is ("Reading runner.py
 * and cli.py · 6 steps"). The operator wants progress and then the answer, not
 * the transcript, so the raw trace (his prose and every tool call, flags and
 * all) stays folded behind the line until asked for.
 *
 * Opened, the trace scrolls itself, and only while the operator has not
 * scrolled it: a panel that yanks itself back to the bottom is unreadable the
 * moment you try to read something that has already gone past.
 *
 * Remembers nothing across turns: each turn starts folded. The settled version
 * of this lands as a collapsed thinking part on the reply.
 */
export function LiveThinkingStream({
  content,
  activity,
  label = "Baxter",
}: {
  content: string;
  activity: string;
  /** Whose reasoning this is, for the accessible name. */
  label?: string;
}) {
  const [collapsed, setCollapsed] = useState(true);
  const bodyRef = useRef<HTMLDivElement | null>(null);
  const pinnedRef = useRef(true);
  const bodyId = useId();

  useEffect(() => {
    const body = bodyRef.current;
    if (!body || collapsed || !pinnedRef.current) return;
    body.scrollTop = body.scrollHeight;
  }, [content, collapsed]);

  if (!content.trim() && !activity) return null;

  const blocks = parseThinkingBlocks(content);
  const summary = summarizeThinking(content, activity);

  return (
    <section
      className="lg-thinking-stream"
      aria-label={`${label} is thinking`}
      // The busy block around this is an `aria-live` region, and this text
      // changes several times a second — announcing every revision would talk
      // over the answer the operator is waiting for.
      aria-live="off"
      data-collapsed={collapsed ? "true" : "false"}
    >
      <Button
        variant="plain"
        className="lg-thinking-stream-head"
        onClick={() => setCollapsed((was) => !was)}
        aria-expanded={!collapsed}
        aria-controls={collapsed ? undefined : bodyId}
        title={activity || undefined}
      >
        <span className="lg-thinking-stream-pulse" aria-hidden />
        <span className="lg-thinking-stream-activity">{summary}</span>
        <span className="lg-thinking-stream-toggle" aria-hidden>
          {collapsed ? "Show trace" : "Hide trace"}
        </span>
      </Button>

      {collapsed ? null : (
        <div
          id={bodyId}
          className="lg-thinking-stream-body"
          ref={bodyRef}
          onScroll={(event) => {
            const el = event.currentTarget;
            // Re-pin once the operator scrolls back down to the end, so
            // following along again does not need a reload.
            pinnedRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
          }}
        >
          {blocks.map((block, index) =>
            block.kind === "step" ? (
              <p key={index} className="lg-thinking-stream-step">
                {block.text}
              </p>
            ) : (
              <p key={index} className="lg-thinking-stream-prose">
                {block.text}
              </p>
            ),
          )}
        </div>
      )}
    </section>
  );
}
