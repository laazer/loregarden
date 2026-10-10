import type { ThinkingPart } from "./types";
import { PrimitiveCard } from "./PrimitiveCard";

/**
 * The reasoning behind a reply, folded above it.
 *
 * A glyph rather than Baxter's face: this is part of the reply under it, not a
 * second speaker. The avatar here read as someone else talking on surfaces
 * that draw none for replies, and as Baxter twice in one turn on those that do.
 */
export function ThinkingPrimitive({ part }: { part: ThinkingPart }) {
  return (
    <PrimitiveCard
      title="Thinking"
      icon={
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M9 18h6M10 22h4M12 2a7 7 0 0 0-4 12.7c.6.5 1 1.3 1 2.1V17h6v-.2c0-.8.4-1.6 1-2.1A7 7 0 0 0 12 2z" />
        </svg>
      }
      collapsible
      defaultCollapsed={part.collapsed !== false}
      tone="accent"
    >
      <div className="lg-primitive-thinking">{part.content}</div>
    </PrimitiveCard>
  );
}
