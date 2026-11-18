import type { ChatWait, ChatWaitLanding } from "../../lib/chatWait";
import { formatWaitRemaining, waitCommandOneLine } from "../../lib/chatWait";
import { StatusChip } from "../ui/StatusChip";
import { PrimitiveKindIcon } from "./primitives/PrimitiveCard";

// Inline, not a class: the dock pins the face on these values (CopilotDock test).
const MONO = { fontFamily: "var(--mono)" } as const;

function LandingProgress({ landing }: { landing: ChatWaitLanding }) {
  const { done, total, summary } = landing;
  const counted = done !== null && total !== null && total > 0;
  return (
    <div className="lg-chat-wait-landing">
      <p className="lg-chat-wait-landing-summary">{summary}</p>
      {counted ? (
        <progress
          className="lg-chat-wait-bar"
          value={Math.min(done, total)}
          max={total}
          aria-label={`${done} of ${total} tests run`}
        />
      ) : null}
    </div>
  );
}

/**
 * Trailing card on a chat timeline while a turn waits on background work it
 * registered: what is waited on, what it is doing now (the ticket's landing,
 * with a bar when its tests report a count, and the log's last line), and how
 * long until the ceiling ends the wait.
 *
 * It wears a chat card's anatomy (`PrimitiveCard`'s classes and kind icon) but
 * none of its controls. It is a live region, which should announce and hold
 * nothing to operate, and it renders inside the conversation scroller, whose
 * own tab stop exists for threads with nothing focusable in them.
 */
export function ChatWaitStatus({ wait }: { wait: ChatWait }) {
  const command = waitCommandOneLine(wait.command);
  return (
    <div className="lg-chat-wait" role="status" aria-live="polite">
      <article className="lg-primitive-card">
        <header className="lg-primitive-card-header">
          <span className="lg-primitive-card-icon lg-primitive-card-icon--run" aria-hidden>
            <PrimitiveKindIcon kind="wait" />
          </span>
          <div className="lg-primitive-card-titles">
            <h3 className="lg-primitive-card-title">
              <span className="lg-primitive-card-title-text">Waiting on background work</span>
            </h3>
            <p className="lg-primitive-card-ref" title={wait.command}>
              {command}
            </p>
          </div>
          <div className="lg-primitive-card-header-actions">
            <StatusChip tone="run" label="Waiting" />
          </div>
        </header>
        <div className="lg-chat-wait-body">
          {wait.landing ? <LandingProgress landing={wait.landing} /> : null}
          <dl className="lg-chat-wait-facts">
            <dt>Last output</dt>
            <dd style={MONO}>{wait.log_line || "Nothing written yet"}</dd>
            <dt>Log</dt>
            <dd style={MONO}>{wait.log_path}</dd>
          </dl>
        </div>
        <footer className="lg-primitive-card-actions">
          <span className="lg-primitive-card-hint">
            {formatWaitRemaining(wait.deadline)} before this wait times out
          </span>
        </footer>
      </article>
    </div>
  );
}
