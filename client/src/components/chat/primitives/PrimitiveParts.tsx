import { PrimitiveSlot } from "./PrimitiveSlot";
import { renderChatPart } from "./registry";
import type { ChatPart, UnknownPart } from "./types";
import { isChatPart } from "./types";
import "./PrimitiveCard.css";

export function PrimitiveParts({
  parts,
}: {
  parts: Array<ChatPart | UnknownPart | unknown> | undefined;
}) {
  if (!parts?.length) return null;
  const nodes = parts.flatMap((part, index) => {
    if (!isChatPart(part)) return [];
    if (part.primitive === "text" && !part.content?.trim()) return [];
    const key = `${part.primitive}-${index}`;
    return [
      <PrimitiveSlot key={key} kind={part.primitive}>
        {renderChatPart(part, key)}
      </PrimitiveSlot>,
    ];
  });
  if (!nodes.length) return null;
  return <div className="lg-primitive-parts">{nodes}</div>;
}
