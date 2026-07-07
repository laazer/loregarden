export interface TicketDiffComment {
  id: string;
  ticket_id: string;
  file_path: string;
  line_index: number;
  line_kind: string;
  content: string;
  resolved: boolean;
  created_at: string;
  created_by: string;
  updated_at: string;
}

async function diffReviewRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const base =
    import.meta.env.VITE_API_BASE ??
    (typeof window !== "undefined" ? window.location.origin : "http://127.0.0.1:8000");
  const res = await fetch(`${base}${path}`, {
    headers: { "Content-Type": "application/json", ...init?.headers },
    ...init,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || res.statusText);
  }
  return res.json() as Promise<T>;
}

export async function listTicketDiffComments(
  ticketId: string,
): Promise<{ comments: TicketDiffComment[]; total: number }> {
  return diffReviewRequest(`/api/tickets/${ticketId}/diff-comments`);
}

export async function addTicketDiffComment(
  ticketId: string,
  body: {
    file_path: string;
    line_index: number;
    line_kind: string;
    content: string;
    created_by?: string;
  },
): Promise<TicketDiffComment> {
  return diffReviewRequest(`/api/tickets/${ticketId}/diff-comments`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function submitTicketDiffReviewToAgent(
  ticketId: string,
  body?: { instructions?: string; created_by?: string },
): Promise<{ submitted_comments: number; message_preview: string }> {
  return diffReviewRequest(`/api/tickets/${ticketId}/diff-comments/submit-to-agent`, {
    method: "POST",
    body: JSON.stringify({
      instructions: body?.instructions ?? "",
      created_by: body?.created_by ?? "reviewer",
    }),
  });
}

export function diffCommentAnchor(filePath: string, lineIndex: number): string {
  return `${filePath}:${lineIndex}`;
}
