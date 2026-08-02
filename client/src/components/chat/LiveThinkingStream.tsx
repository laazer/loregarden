import { useEffect, useRef, useState } from "react";

import "./LiveThinkingStream.css";

/** A tool step, or a run of reasoning prose. */
type ThinkingBlock = { kind: "step"; text: string } | { kind: "prose"; text: string };

/**
 * Split the transcript back into what the server interleaved into it.
 *
 * The wire format is one string on purpose — reasoning and tool calls happen
 * in an order that matters, and two parallel lists would have to re-derive it.
 * Tool steps are written as `· label` on their own line, which is the only
 * structure this has to recover.
 */
function parseThinkingBlocks(content: string): ThinkingBlock[] {
  const blocks: ThinkingBlock[] = [];
  let prose: string[] = [];

  const flushProse = () => {
    const text = prose.join("\n").trim();
    if (text) blocks.push({ kind: "prose", text });
    prose = [];
  };

  for (const line of content.split("\n")) {
    if (line.startsWith("· ")) {
      flushProse();
      blocks.push({ kind: "step", text: line.slice(2).trim() });
      continue;
    }
    prose.push(line);
  }
  flushProse();
  return blocks;
}

/**
 * Baxter's reasoning while he is still reasoning.
 *
 * Scrolls itself, and only while the operator has not scrolled it: a panel
 * that yanks itself back to the bottom is unreadable the moment you try to
 * read something that has already gone past.
 *
 * Collapsible, and remembers nothing across turns. Reasoning is worth watching
 * live and worth almost nothing afterwards, which is why the settled version of
 * this lands as a collapsed thinking part on the reply rather than staying open
 * here.
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
  const [collapsed, setCollapsed] = useState(false);
  const bodyRef = useRef<HTMLDivElement | null>(null);
  const pinnedRef = useRef(true);

  useEffect(() => {
    const body = bodyRef.current;
    if (!body || collapsed || !pinnedRef.current) return;
    body.scrollTop = body.scrollHeight;
  }, [content, collapsed]);

  if (!content.trim() && !activity) return null;

  const blocks = parseThinkingBlocks(content);

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
      <button
        type="button"
        className="lg-thinking-stream-head"
        onClick={() => setCollapsed((was) => !was)}
        aria-expanded={!collapsed}
      >
        <span className="lg-thinking-stream-pulse" aria-hidden />
        <span className="lg-thinking-stream-activity">{activity || "Thinking"}</span>
        <span className="lg-thinking-stream-toggle" aria-hidden>
          {collapsed ? "Show" : "Hide"}
        </span>
      </button>

      {collapsed ? null : (
        <div
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
