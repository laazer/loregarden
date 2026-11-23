import { PrimitiveSlot } from "./PrimitiveSlot";
import { renderChatPart } from "./registry";
import type { ChatPart, LaterUserReply, UnknownPart } from "./types";
import { isChatPart } from "./types";
import "./PrimitiveCard.css";

export function PrimitiveParts({
  parts,
  onSubmit,
  laterUserReply,
}: {
  parts: Array<ChatPart | UnknownPart | unknown> | undefined;
  onSubmit?: (content: string) => void;
  /**
   * The first user message after the message these parts came from, or null.
   *
   * A card cannot see the message after its own, so the thread works it out and
   * hands it down — the same rule `living_map/records.py` settles the Map on.
   * Only the `qa` card reads it. Required, not optional: an optional prop lets
   * a caller omit the fact silently, which is how the artifacts panel came to
   * disagree with the thread about the same card.
   */
  laterUserReply: LaterUserReply | null;
}) {
  if (!parts?.length) return null;
  const nodes = parts.flatMap((part, index) => {
    if (!isChatPart(part)) return [];
    if (part.primitive === "text" && !part.content?.trim()) return [];
    const key = `${part.primitive}-${index}`;
    return [
      <PrimitiveSlot key={key} kind={part.primitive}>
        {renderChatPart(part, key, { onSubmit, laterUserReply })}
      </PrimitiveSlot>,
    ];
  });
  if (!nodes.length) return null;
  return <div className="lg-primitive-parts">{nodes}</div>;
}
