import { useMemo } from "react";

import { chunkStreamingReply } from "../../lib/streamingReply";
import { MarkdownContent } from "./MarkdownContent";
import { agentPlanIdentity } from "./primitives/agentPlan";
import { PrimitiveParts } from "./primitives/PrimitiveParts";
import type { TodoListPart } from "./primitives/types";

function cardName(primitive: string | null): string {
  return primitive ? `${primitive.replace(/_/g, " ")} card` : "card";
}

/**
 * The reply as it forms, readable while it forms.
 *
 * Finished blocks render as the settled reply will, a finished plan card
 * renders as the card, and only the block still being written is plain text
 * (with the caret). See `chunkStreamingReply` for where the cuts fall.
 */
export function StreamingReply({
  answer,
  plans,
  threadPlanIdentities,
  onPrimitiveSubmit,
}: {
  answer: string;
  /** Server-validated plans from this turn's stream. */
  plans: TodoListPart[];
  /** Plans already on a card in the thread; that card shows the live copy. */
  threadPlanIdentities: Set<string>;
  onPrimitiveSubmit?: (content: string) => void;
}) {
  const chunks = useMemo(() => chunkStreamingReply(answer, plans), [answer, plans]);

  return (
    <div className="lg-chat-streaming">
      {chunks.map((chunk, index) => {
        switch (chunk.kind) {
          case "markdown":
            return (
              <div key={index} className="lg-chat-reply">
                <MarkdownContent content={chunk.text} expandable={false} />
              </div>
            );
          case "card": {
            const identity = chunk.plan ? agentPlanIdentity(chunk.plan) : null;
            if (chunk.plan && !(identity && threadPlanIdentities.has(identity))) {
              return <PrimitiveParts key={index} parts={[chunk.plan]} onSubmit={onPrimitiveSubmit} />;
            }
            return (
              <p key={index} className="lg-chat-streaming-note">
                {chunk.plan
                  ? `Updated “${chunk.plan.title || "the plan"}” above`
                  : `A ${cardName(chunk.primitive)} — shown when the reply finishes`}
              </p>
            );
          }
          case "card-forming":
            return (
              <p key={index} className="lg-chat-streaming-note lg-chat-streaming-note--forming">
                Writing a {cardName(chunk.primitive)}…
              </p>
            );
          case "tail":
            return (
              <div key={index} className="lg-chat-reply lg-chat-reply--streaming">
                {chunk.text}
              </div>
            );
        }
      })}
    </div>
  );
}
