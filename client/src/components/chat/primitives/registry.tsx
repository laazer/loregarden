import type { ComponentType } from "react";

import { AgentPrimitive } from "./AgentPrimitive";
import { BtwPrimitive } from "./BtwPrimitive";
import { CalendarEventPrimitive, CalendarPrimitive } from "./CalendarPrimitive";
import { EditPrimitive } from "./EditPrimitive";
import { GatePrimitive } from "./GatePrimitive";
import { GiphyPrimitive } from "./GiphyPrimitive";
import { BranchHistoryPrimitive, CommitPrimitive } from "./GitPrimitive";
import { KanbanPrimitive, StatusColumnPrimitive } from "./KanbanPrimitive";
import { OperatorQuestionPrimitive } from "./OperatorQuestionPrimitive";
import { ParentTicketPrimitive } from "./ParentTicketPrimitive";
import { PrCyclePrimitive } from "./PrCyclePrimitive";
import { PrimitiveCard } from "./PrimitiveCard";
import { QAPrimitive } from "./QAPrimitive";
import { ResearchCandidatesPrimitive } from "./ResearchCandidatesPrimitive";
import { ResearchClaimPrimitive } from "./ResearchClaimPrimitive";
import { ResearchDiffPrimitive } from "./ResearchDiffPrimitive";
import { ResearchProjectPrimitive } from "./ResearchProjectPrimitive";
import { TerminalPrimitive } from "./TerminalPrimitive";
import { ThinkingPrimitive } from "./ThinkingPrimitive";
import { TicketListPrimitive } from "./TicketListPrimitive";
import { TicketPrimitive } from "./TicketPrimitive";
import { TicketWorkflowPrimitive } from "./TicketWorkflowPrimitive";
import { TodoListPrimitive } from "./TodoListPrimitive";
import type { ChatPart, LaterUserReply, PrimitiveKind, UnknownPart } from "./types";
import { WorkflowPrimitive } from "./WorkflowPrimitive";
import { WorkspacePrimitive } from "./WorkspacePrimitive";
import { MarkdownContent } from "../MarkdownContent";

type Renderer = ComponentType<{
  part: never;
  onSubmit?: (content: string) => void;
  laterUserReply?: LaterUserReply | null;
}>;

function TextPrimitive({ part }: { part: Extract<ChatPart, { primitive: "text" }> }) {
  return <MarkdownContent content={part.content} />;
}

export function UnknownPrimitiveCard({ part }: { part: UnknownPart }) {
  return (
    <PrimitiveCard
      kind={part.primitive}
      title={`Unknown primitive: ${part.primitive}`}
      status={{ tone: "wait", label: "Unknown" }}
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
  operator_question: OperatorQuestionPrimitive as Renderer,
  terminal: TerminalPrimitive as Renderer,
  edit: EditPrimitive as Renderer,
  calendar: CalendarPrimitive as Renderer,
  calendar_event: CalendarEventPrimitive as Renderer,
  workspace: WorkspacePrimitive as Renderer,
  todo_list: TodoListPrimitive as Renderer,
  branch_history: BranchHistoryPrimitive as Renderer,
  commit: CommitPrimitive as Renderer,
  qa: QAPrimitive as Renderer,
  btw: BtwPrimitive as Renderer,
  giphy: GiphyPrimitive as Renderer,
  research_candidates: ResearchCandidatesPrimitive as Renderer,
  research_claim: ResearchClaimPrimitive as Renderer,
  research_project: ResearchProjectPrimitive as Renderer,
  research_diff: ResearchDiffPrimitive as Renderer,
  pr_cycle: PrCyclePrimitive as Renderer,
};

/**
 * What a part is rendered *in*, beside the part itself.
 *
 * One object rather than trailing positional arguments, and `laterUserReply` is
 * required rather than optional, so a surface that renders cards has to say
 * what followed them. It is the omission this type exists to stop: the second
 * caller of `renderChatPart` was left behind when the fact was added, and every
 * `qa` card in the dock's artifacts panel went on asking a question the
 * operator had already answered. `null` is the honest answer where nothing did.
 */
export interface ChatPartContext {
  onSubmit?: (content: string) => void;
  laterUserReply: LaterUserReply | null;
}

export function renderChatPart(
  part: ChatPart | UnknownPart,
  key: string,
  { onSubmit, laterUserReply }: ChatPartContext,
) {
  const kind = part.primitive as PrimitiveKind;
  const Renderer = PRIMITIVE_RENDERERS[kind];
  if (!Renderer) {
    return <UnknownPrimitiveCard key={key} part={part as UnknownPart} />;
  }
  const Comp = Renderer as ComponentType<{
    part: ChatPart;
    onSubmit?: (content: string) => void;
    laterUserReply?: LaterUserReply | null;
  }>;
  return (
    <Comp key={key} part={part as ChatPart} onSubmit={onSubmit} laterUserReply={laterUserReply} />
  );
}
