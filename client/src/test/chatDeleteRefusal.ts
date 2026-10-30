/**
 * The chat-delete refusal, as the live database renders it.
 *
 * Read read-only on 2026-10-16: 8 chat worktrees, every `worktree_path` exactly
 * 86 characters, and the common-branch refusal rendering at 220-239 characters
 * over those rows. This is the longest of the 8, verbatim rather than
 * paraphrased — the point of the fixture is that the 4-line clip lands *inside*
 * the path (characters 108-194) and never before it, which a short fixture
 * cannot exercise.
 */

export const CHAT_DELETE_REFUSAL_TITLE = "Delete session";

export const CHAT_DELETE_REFUSAL_WORKTREE_PATH =
  "/Users/jacobbrandt/workspace/.worktrees/chat-chat-do-i-have-a-ticket-for-symc-02d2589a";

export const CHAT_DELETE_REFUSAL_MESSAGE =
  "This chat still has unpublished work on " +
  "`chat/do-i-have-a-ticket-for-symcing-tickets-with-gh-i-a0a1f1c1`" +
  ` at ${CHAT_DELETE_REFUSAL_WORKTREE_PATH}. Commit or discard it, then delete the chat.`;
