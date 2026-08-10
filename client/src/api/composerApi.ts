import { request } from "./http";

/** One ranked hit from the composer's `@` path picker. */
export interface EditorPathMatch {
  name: string;
  repo_path: string;
  kind: "directory" | "file";
}

/** A post-it written from the composer's `/note` command. */
export interface ComposerNote {
  id: string;
  body: string;
  /** When it was last sent into a conversation, or null while unsent. */
  sent_at: string | null;
  created_at: string;
  updated_at: string;
}

/** What the chat composers' `/` commands and `@` references talk to: workspace
 * path lookup for the reference picker, and the post-its `/note` keeps. Both
 * are workspace-scoped, and neither belongs to any one conversation. */
export const composerApi = {
  /** Ranked files and folders for the `@` picker; an empty query lists the top level. */
  editorSearch: (slug: string, query: string, contextRoot?: string) => {
    const q = new URLSearchParams({ q: query });
    if (contextRoot) q.set("context_root", contextRoot);
    return request<EditorPathMatch[]>(
      `/api/workspaces/${encodeURIComponent(slug)}/editor/search?${q}`,
    );
  },
  notes: (slug: string) =>
    request<ComposerNote[]>(`/api/workspaces/${encodeURIComponent(slug)}/composer-notes`),
  createNote: (slug: string, body: string) =>
    request<ComposerNote>(`/api/workspaces/${encodeURIComponent(slug)}/composer-notes`, {
      method: "POST",
      body: JSON.stringify({ body }),
    }),
  /** Edit the text, stamp it as sent, or both. Sending never deletes it. */
  updateNote: (slug: string, noteId: string, body: { body?: string; mark_sent?: boolean }) =>
    request<ComposerNote>(
      `/api/workspaces/${encodeURIComponent(slug)}/composer-notes/${noteId}`,
      { method: "PATCH", body: JSON.stringify(body) },
    ),
  deleteNote: (slug: string, noteId: string) =>
    request<{ deleted: string }>(
      `/api/workspaces/${encodeURIComponent(slug)}/composer-notes/${noteId}`,
      { method: "DELETE" },
    ),
};
