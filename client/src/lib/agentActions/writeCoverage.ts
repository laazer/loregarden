/**
 * How an agent reaches every write the UI can make — or why it may not.
 *
 * One entry per non-GET method of `api` (api/client.ts). A new write endpoint
 * fails `writeCoverage.test.ts` until it is classified here, which is the one
 * place the question has a precise answer: whether a write needs an agent
 * action depends on the method, not on which component happens to call it.
 * (A file-level gate was measured first and was ~40% noise for exactly that
 * reason — lg-ux-enforcement-852.)
 *
 * - `agent_action` — a registered UI action drives it (`useAgentAction`).
 * - `mcp_tool`     — a loregarden_* MCP tool performs the same write directly.
 * - `human_only`   — a person's call by design: it passes a gate meant for a
 *                    person, deletes permanently, or changes the agents, gates
 *                    and tools that run and check agents.
 * - `not_a_write`  — a POST that computes or drafts; nothing is saved.
 * - `not_agent_driven` — a write agents have a better path to, or no business in.
 * - `gap`          — an agent with permission should be able to drive this and
 *                    cannot yet. These are the adoption list.
 */

import type { api } from "../../api/client";
import type { UiActionName } from "./catalog";

export type WriteCoverage =
  | { kind: "agent_action"; action: UiActionName }
  | { kind: "mcp_tool"; tool: `loregarden_${string}` }
  | { kind: "human_only"; reason: string }
  | { kind: "not_a_write"; reason: string }
  | { kind: "not_agent_driven"; reason: string }
  | { kind: "gap" };

const GATE = "passes a gate that exists for a person to pass";
const DELETES = "deletes permanently";
const OVERSIGHT = "changes the agents, workflows, gates or tools that run and check agents";
const CONVERSATION =
  "a conversation with an agent in the operator's chat pane; an agent has no business speaking in it";
const DRAFT = "computes or drafts; nothing is saved";

export const API_WRITE_COVERAGE = {
  // Tickets
  createTicket: { kind: "mcp_tool", tool: "loregarden_create_ticket" },
  updateTicket: { kind: "agent_action", action: "ticket.update" },
  deleteTicket: { kind: "human_only", reason: `${DELETES}; an agent retires a ticket with loregarden_supersede_ticket` },
  // The bulk form of the same write: an agent creates the tickets directly,
  // without the import dialog's file-preview step.
  importTickets: { kind: "mcp_tool", tool: "loregarden_create_ticket" },
  previewTicketImport: { kind: "not_a_write", reason: DRAFT },
  previewTicketImportPaths: { kind: "not_a_write", reason: DRAFT },
  orchestrate: { kind: "mcp_tool", tool: "loregarden_start_orchestration" },
  startRun: { kind: "gap" },
  stopTicket: { kind: "gap" },
  routeWorkflow: { kind: "gap" },
  advance: { kind: "human_only", reason: GATE },
  openPr: { kind: "not_agent_driven", reason: "publishes to the remote; the pipeline's publish chain does this for agents" },
  commitPush: { kind: "not_agent_driven", reason: "publishes to the remote; the pipeline's publish chain does this for agents" },
  buildTerminalHandoffCommand: { kind: "not_a_write", reason: DRAFT },
  buildExternalHarnessPrompt: { kind: "not_a_write", reason: DRAFT },
  setTicketRuntime: { kind: "gap" },
  setTriageRuntime: { kind: "gap" },
  sendTriageMessage: { kind: "not_agent_driven", reason: CONVERSATION },
  stopTriageTurn: { kind: "not_agent_driven", reason: CONVERSATION },
  askAside: { kind: "not_agent_driven", reason: CONVERSATION },
  escalateAside: { kind: "not_agent_driven", reason: CONVERSATION },
  deleteAside: { kind: "human_only", reason: DELETES },

  // Runs and the queue
  sendRunMessage: { kind: "gap" },
  cancelRun: { kind: "gap" },
  queueRunAction: { kind: "gap" },
  triggerAutoFix: { kind: "agent_action", action: "ticket.trigger_auto_fix" },
  skipCICheck: { kind: "human_only", reason: GATE },

  // Inbox
  resolveApproval: { kind: "human_only", reason: GATE },
  runHumanAction: { kind: "human_only", reason: GATE },

  // Workspaces
  createWorkspace: { kind: "gap" },
  createWorkspaceRepository: { kind: "gap" },
  archiveWorkspace: { kind: "agent_action", action: "workspace.archive" },
  restoreWorkspace: { kind: "agent_action", action: "workspace.restore" },
  setWorkspaceTemplate: { kind: "agent_action", action: "workspace.set_workflow" },
  setWorkspaceRuntime: { kind: "gap" },
  updateWorkspaceGates: { kind: "human_only", reason: OVERSIGHT },
  testWorkspaceGates: { kind: "not_a_write", reason: DRAFT },
  updateGitAutomation: { kind: "human_only", reason: OVERSIGHT },
  setMemoryConfig: { kind: "not_agent_driven", reason: "machine-level configuration of where memory is stored" },
  reloadServer: { kind: "not_agent_driven", reason: "restarts the control plane the calling agent runs on" },
  editorCheckout: { kind: "not_agent_driven", reason: "the operator's checkout; agents work in their own worktree with git" },
  editorWriteFile: { kind: "not_agent_driven", reason: "the operator's checkout; agents work in their own worktree with file tools" },

  // MCP gateway
  createMcpServer: { kind: "human_only", reason: OVERSIGHT },
  updateMcpServer: { kind: "human_only", reason: OVERSIGHT },
  deleteMcpServer: { kind: "human_only", reason: OVERSIGHT },
  checkMcpServerHealth: { kind: "not_a_write", reason: "a probe; nothing is saved" },

  // Baxter chat
  createBaxterChatSession: { kind: "not_agent_driven", reason: CONVERSATION },
  renameBaxterChatSession: { kind: "not_agent_driven", reason: CONVERSATION },
  deleteBaxterChatSession: { kind: "human_only", reason: DELETES },
  setBaxterChatRuntime: { kind: "not_agent_driven", reason: CONVERSATION },
  sendBaxterChatMessage: { kind: "not_agent_driven", reason: CONVERSATION },
  uploadBaxterChatAttachment: { kind: "not_agent_driven", reason: CONVERSATION },
  stopBaxterChatTurn: { kind: "not_agent_driven", reason: CONVERSATION },

  // Studio: agents and workflows
  previewStudioAgent: { kind: "not_a_write", reason: DRAFT },
  generateStudioAgent: { kind: "not_a_write", reason: DRAFT },
  createStudioAgent: { kind: "human_only", reason: OVERSIGHT },
  updateStudioAgent: { kind: "human_only", reason: OVERSIGHT },
  deleteStudioAgent: { kind: "human_only", reason: OVERSIGHT },
  restoreStudioAgentVersion: { kind: "human_only", reason: OVERSIGHT },
  generateStudioWorkflow: { kind: "not_a_write", reason: DRAFT },
  createStudioWorkflow: { kind: "human_only", reason: OVERSIGHT },
  updateStudioWorkflow: { kind: "human_only", reason: OVERSIGHT },
  deleteStudioWorkflow: { kind: "human_only", reason: OVERSIGHT },
  publishStudioWorkflow: { kind: "human_only", reason: OVERSIGHT },
  restoreStudioWorkflowVersion: { kind: "human_only", reason: OVERSIGHT },

  // Ticket Studio — a conversation with the scoping agent; its outcome, a
  // ticket, is reachable through loregarden_create_ticket.
  createTicketStudioSession: { kind: "not_agent_driven", reason: CONVERSATION },
  updateTicketStudioSession: { kind: "not_agent_driven", reason: CONVERSATION },
  deleteTicketStudioSession: { kind: "human_only", reason: DELETES },
  setTicketStudioRuntime: { kind: "not_agent_driven", reason: CONVERSATION },
  updateTicketStudioDraft: { kind: "not_agent_driven", reason: CONVERSATION },
  sendTicketStudioMessage: { kind: "not_agent_driven", reason: CONVERSATION },
  requestTicketStudioClarifications: { kind: "not_agent_driven", reason: CONVERSATION },
  saveTicketStudioClarifications: { kind: "not_agent_driven", reason: CONVERSATION },
  stopTicketStudioTurn: { kind: "not_agent_driven", reason: CONVERSATION },
  generateTicketStudioScope: { kind: "not_agent_driven", reason: CONVERSATION },
  commitTicketStudioSession: { kind: "not_agent_driven", reason: CONVERSATION },
  setTicketStudioReferenceRepos: { kind: "not_agent_driven", reason: CONVERSATION },
  generateTicketStudioSurvey: { kind: "not_agent_driven", reason: CONVERSATION },
  saveTicketStudioSurvey: { kind: "not_agent_driven", reason: CONVERSATION },

  // Reference repositories
  addReferenceRepo: { kind: "agent_action", action: "reference_repo.add" },
  syncReferenceRepo: { kind: "agent_action", action: "reference_repo.sync" },
  deleteReferenceRepo: { kind: "human_only", reason: DELETES },
} satisfies Partial<Record<keyof typeof api, WriteCoverage>>;
