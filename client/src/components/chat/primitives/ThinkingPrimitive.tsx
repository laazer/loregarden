import { BaxterAvatar } from "../BaxterAvatar";
import type { ThinkingPart } from "./types";
import { PrimitiveCard } from "./PrimitiveCard";

export function ThinkingPrimitive({ part }: { part: ThinkingPart }) {
  return (
    <PrimitiveCard
      title="Thinking"
      icon={<BaxterAvatar variant="head" state="thinking" size={28} label="Thinking" />}
      collapsible
      defaultCollapsed={part.collapsed !== false}
      tone="accent"
    >
      <div className="lg-primitive-thinking">{part.content}</div>
    </PrimitiveCard>
  );
}
