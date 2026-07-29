import type { ComponentType } from "react";

import { AgentPrimitive } from "./AgentPrimitive";
import { CalendarEventPrimitive, CalendarPrimitive } from "./CalendarPrimitive";
import { EditPrimitive } from "./EditPrimitive";
import { GatePrimitive } from "./GatePrimitive";
import { KanbanPrimitive, StatusColumnPrimitive } from "./KanbanPrimitive";
import { ParentTicketPrimitive } from "./ParentTicketPrimitive";
import { PrimitiveCard } from "./PrimitiveCard";
import { TerminalPrimitive } from "./TerminalPrimitive";
import { ThinkingPrimitive } from "./ThinkingPrimitive";
import { TicketListPrimitive } from "./TicketListPrimitive";
import { TicketPrimitive } from "./TicketPrimitive";
import { TicketWorkflowPrimitive } from "./TicketWorkflowPrimitive";
import type { ChatPart, PrimitiveKind, UnknownPart } from "./types";
import { WorkflowPrimitive } from "./WorkflowPrimitive";
import { MarkdownContent } from "../MarkdownContent";

type Renderer = ComponentType<{ part: never }>;

function TextPrimitive({ part }: { part: Extract<ChatPart, { primitive: "text" }> }) {
  return <MarkdownContent content={part.content} />;
}

export function UnknownPrimitiveCard({ part }: { part: UnknownPart }) {
  return (
    <PrimitiveCard
      title={`Unknown primitive: ${part.primitive}`}
      tone="warn"
      subtitle="This client does not know how to render this card."
    >
      <pre className="lg-primitive-thinking">{JSON.stringify(part, null, 2)}</pre>
    </PrimitiveCard>
  );
}

export const PRIMITIVE_RENDERERS: Record<PrimitiveKind, Renderer> = {
  text: TextPrimitive as Renderer,
  thinking: ThinkingPrimitive as Renderer,
  ticket: TicketPrimitive as Renderer,
  ticket_workflow: TicketWorkflowPrimitive as Renderer,
  parent_ticket: ParentTicketPrimitive as Renderer,
  ticket_list: TicketListPrimitive as Renderer,
  status_column: StatusColumnPrimitive as Renderer,
  kanban: KanbanPrimitive as Renderer,
  filterable_kanban: KanbanPrimitive as Renderer,
  agent: AgentPrimitive as Renderer,
  workflow: WorkflowPrimitive as Renderer,
  gate: GatePrimitive as Renderer,
  terminal: TerminalPrimitive as Renderer,
  edit: EditPrimitive as Renderer,
  calendar: CalendarPrimitive as Renderer,
  calendar_event: CalendarEventPrimitive as Renderer,
};

export function renderChatPart(part: ChatPart | UnknownPart, key: string) {
  const kind = part.primitive as PrimitiveKind;
  const Renderer = PRIMITIVE_RENDERERS[kind];
  if (!Renderer) {
    return <UnknownPrimitiveCard key={key} part={part as UnknownPart} />;
  }
  const Comp = Renderer as ComponentType<{ part: ChatPart }>;
  return <Comp key={key} part={part as ChatPart} />;
}
