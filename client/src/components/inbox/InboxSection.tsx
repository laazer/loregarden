import { useEffect, useId, useRef, type ReactNode } from "react";

import { scrollIntoScroller } from "../../lib/scrollWithin";
import { Button } from "../ui/Button";
import "./InboxSection.css";

/**
 * One section of the inbox: a heading, its count, and what it counts.
 *
 * With `onToggle` the whole heading row folds the section. The body is hidden
 * rather than unmounted, so a half-typed answer on a card survives, and the
 * count stays in the heading because a folded section must still say something
 * is waiting in it. Without `onToggle` the heading is plain text: a pane with
 * nowhere to keep the choice gets no control rather than one that forgets.
 */
export function InboxSection({
  title,
  label,
  count,
  heading: Heading,
  collapsed = false,
  onToggle,
  notice,
  actions,
  children,
}: {
  title: string;
  /** The region's accessible name. */
  label: string;
  count: number;
  /** `h2` for the drawer's own sections, `h3` for a workspace inside one. */
  heading: "h2" | "h3";
  collapsed?: boolean;
  onToggle?: () => void;
  /** Said in the heading while folded, for a fact the hidden body would have told. */
  notice?: string;
  /** Controls beside the heading, usable folded or open. */
  actions?: ReactNode;
  children: ReactNode;
}) {
  const bodyId = useId();
  const sectionRef = useRef<HTMLElement>(null);
  const wasCollapsed = useRef(collapsed);

  // Folding a section from its stuck heading takes away the content that was
  // holding the scroll position, so bring the heading back to where it was.
  useEffect(() => {
    const folded = collapsed && !wasCollapsed.current;
    wasCollapsed.current = collapsed;
    const section = sectionRef.current;
    if (folded && section) scrollIntoScroller(section, "start", { onlyIfAbove: true });
  }, [collapsed]);

  return (
    <section ref={sectionRef} className="inbox-section" aria-label={label}>
      <div className={`inbox-section-header${onToggle ? " inbox-section-header--toggle" : ""}`}>
        <Heading className="inbox-section-title">
          {onToggle ? (
            <Button
              variant="plain"
              className="inbox-section-toggle"
              aria-expanded={!collapsed}
              aria-controls={bodyId}
              onClick={onToggle}
            >
              <svg
                className="inbox-section-chevron"
                width="10"
                height="10"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2.5"
                strokeLinecap="round"
                strokeLinejoin="round"
                aria-hidden
              >
                <path d="M6 9l6 6 6-6" />
              </svg>
              {title}
            </Button>
          ) : (
            title
          )}
        </Heading>
        <span className="count-pill">{count}</span>
        {collapsed && notice ? <span className="inbox-section-notice">{notice}</span> : null}
        {actions ? <div className="inbox-section-actions">{actions}</div> : null}
      </div>
      {onToggle ? (
        <div id={bodyId} hidden={collapsed}>
          {children}
        </div>
      ) : (
        children
      )}
    </section>
  );
}
