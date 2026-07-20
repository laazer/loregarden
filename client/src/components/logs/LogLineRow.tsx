import type { LogLine } from "../../api/types";
import { logTagVariant } from "../../lib/logLineStyle";

export function LogLineRow({ line }: { line: LogLine }) {
  const variant = logTagVariant(line.tag);
  return (
    <div className="log-line">
      <span className="log-line__time">{line.time}</span>
      <span className={`log-line__tag log-line__tag--${variant}`}>{line.tag}</span>
      <span className="log-line__text">{line.text}</span>
    </div>
  );
}

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
