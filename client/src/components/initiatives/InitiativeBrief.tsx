import { useLayoutEffect, useRef, useState } from "react";

import { openReader } from "../../state/readerStore";
import { MarkdownContent } from "../chat/MarkdownContent";
import { Button } from "../ui/Button";

/**
 * An initiative's brief at a glance: three lines, then "Read brief".
 *
 * Briefs run to pages of headings and tables, and printed in full on a card
 * they push the milestones (what the operator came to act on) below the fold.
 * Three lines say what the initiative is; the reader shows the rest at full
 * size. "Read brief" appears only when there is more to read, measured from
 * the rendered text rather than guessed from its length.
 */
export function InitiativeBrief({ content, title, subtitle }: { content: string; title: string; subtitle: string }) {
  const boxRef = useRef<HTMLDivElement>(null);
  const [overflows, setOverflows] = useState(false);

  useLayoutEffect(() => {
    const box = boxRef.current;
    if (!box) return;
    const measure = () => setOverflows(box.scrollHeight > box.clientHeight + 1);
    measure();
    // Re-measured as the card's width changes: a brief that fits at 1440px
    // can run past three lines in a narrow column.
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(box);
    return () => observer.disconnect();
  }, [content]);

  return (
    <div className="initiative-brief">
      <div ref={boxRef} className={`initiative-brief-text${overflows ? " is-clamped" : ""}`}>
        <MarkdownContent content={content} className="initiative-desc" expandable={false} />
      </div>
      {overflows ? (
        <Button
          variant="plain"
          className="initiative-brief-more"
          aria-label={`Read the brief for ${title}`}
          onClick={() => openReader({ title, subtitle, content })}
        >
          Read brief
        </Button>
      ) : null}
    </div>
  );
}
