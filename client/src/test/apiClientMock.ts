export const API_BASE = "http://127.0.0.1:8000";

export function isWorkflowWorkItem(): boolean {
  return true;
}

export const api = {
  workspaces: jest.fn().mockResolvedValue([]),
  ticketTree: jest.fn().mockResolvedValue([]),
  tickets: jest.fn().mockResolvedValue([]),
  ticket: jest.fn(),
  updateTicket: jest.fn(),
  workflowTemplates: jest.fn().mockResolvedValue([]),
  workspaceWorkflow: jest.fn().mockResolvedValue(null),
  approvals: jest.fn().mockResolvedValue([]),
  runs: jest.fn().mockResolvedValue([]),
  triage: jest.fn().mockResolvedValue({ pending_approvals: [] }),
  triage: jest.fn().mockResolvedValue(null),
  runtimeOptions: jest.fn().mockResolvedValue([]),
  usage: jest.fn().mockResolvedValue({}),
  memoryConfig: jest.fn().mockResolvedValue({}),
  setMemoryConfig: jest.fn(),
  orchestrate: jest.fn(),
  openPr: jest.fn(),
  startRun: jest.fn(),
  advance: jest.fn(),
  resolveApproval: jest.fn(),
  setWorkspaceTemplate: jest.fn(),
  setWorkspaceRuntime: jest.fn(),
  createWorkspace: jest.fn(),
  previewTicketImportPaths: jest.fn(),
  importTickets: jest.fn(),
  createTicket: jest.fn(),
};
