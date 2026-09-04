import { memo, useMemo } from "react";

import type { LogLine } from "../../api/types";
import { logTagVariant } from "../../lib/logLineStyle";
import type { LogBodyFormat } from "../../lib/logLineFormat";
import { splitLogLine } from "../../lib/logLineFormat";
import { MarkdownContent } from "../chat/MarkdownContent";

function LogLineBodyView({ body, format }: { body: string; format: LogBodyFormat }) {
  if (!body) return null;
  if (format === "json") return <pre className="log-line__json">{body}</pre>;
  if (format === "markdown") {
    // normalize=false: this is a tool's own output, and the chat normalizer
    // rewrites spacing on the assumption that it is authored prose.
    return <MarkdownContent content={body} className="log-line__md" normalize={false} />;
  }
  return <>{body}</>;
}

export const LogLineRow = memo(
  function LogLineRow({ line }: { line: LogLine }) {
    const variant = logTagVariant(line.tag);
    const { headline, body, format } = useMemo(() => splitLogLine(line), [line]);
    return (
      <div className={`log-line log-line--${variant}`}>
        <span className="log-line__time">{line.time}</span>
        <span className={`log-line__tag log-line__tag--${variant}`}>{line.tag}</span>
        <div className="log-line__text">
          {headline ? <div className="log-line__headline">{headline}</div> : null}
          <LogLineBodyView body={body} format={format} />
        </div>
      </div>
    );
  },
  // The feed refetches every 2s and hands back fresh objects for unchanged
  // lines. Without this, every poll re-parses and re-renders the whole history.
  (prev, next) =>
    prev.line.time === next.line.time &&
    prev.line.tag === next.line.tag &&
    prev.line.text === next.line.text,
);

export function LiveLogLine({ text }: { text: string }) {
  return (
    <div className="log-line log-line--live">
      <span className="log-line__time">now</span>
      <span className="log-line__tag log-line__tag--run log-line__tag--live">RUN</span>
      <span className="log-line__text">
        {text}
        <span className="log-line__cursor" aria-hidden>
          ▊
        </span>
      </span>
    </div>
  );
}
