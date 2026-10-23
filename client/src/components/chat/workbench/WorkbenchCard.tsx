import { useId, useState, type ReactNode } from "react";

import { Button } from "../../ui/Button";
import "./Workbench.css";

/**
 * One card in the chat workbench: a titled box that folds to its header.
 *
 * Folding is per visit. A card that remembered being folded would hide, say,
 * a proposal waiting on the operator the next time they came back.
 */
export function WorkbenchCard({
  title,
  meta,
  action,
  children,
}: {
  title: string;
  /** A short status beside the title (a count, a state), shown folded or not. */
  meta?: ReactNode;
  /** The card's one header action, e.g. "Edit". */
  action?: ReactNode;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(true);
  const bodyId = useId();
  return (
    <section className="chat-wb-card" aria-label={title}>
      <header className="chat-wb-card-head">
        <Button
          variant="plain"
          className="chat-wb-card-toggle"
          aria-expanded={open}
          aria-controls={bodyId}
          onClick={() => setOpen(!open)}
        >
          <h3>{title}</h3>
          <svg
            className="chat-wb-card-chevron"
            width="12"
            height="12"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            aria-hidden
          >
            <path d="m6 9 6 6 6-6" />
          </svg>
        </Button>
        {meta ? <span className="chat-wb-card-meta">{meta}</span> : null}
        {action}
      </header>
      {open ? (
        <div id={bodyId} className="chat-wb-card-body">
          {children}
        </div>
      ) : null}
    </section>
  );
}
