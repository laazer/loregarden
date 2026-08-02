import { useEffect, useRef, useState } from "react";

import { API_BASE, api } from "../api/client";
import {
  ChatThinkingSocket,
  EMPTY_THINKING_FRAME,
  chatThinkingSocketUrl,
  type ChatThinkingFrame,
  type ChatThinkingSocketStatus,
} from "../lib/chatThinkingSocket";

/** How often to read the turn's transcript while the socket is down. */
const FALLBACK_POLL_INTERVAL_MS = 1200;

export interface ChatTurnThinking {
  /** Reasoning and tool steps so far. Empty when the turn has produced none. */
  content: string;
  /** The reply as it forms. Replaced by the settled message when the turn ends. */
  answer: string;
  /** What the agent is doing right now, or "" when it has not said. */
  activity: string;
  /** Whether anything is being received — false once the turn settles. */
  isStreaming: boolean;
}

const NOTHING: ChatTurnThinking = { content: "", answer: "", activity: "", isStreaming: false };

/**
 * The reasoning of whichever turn is in flight, as it is produced.
 *
 * Bound to a turn id rather than a conversation, because that is the one thing
 * all four chat surfaces already agree on: each publishes an `active_turn_id`
 * (or `active_run_id`) on its snapshot, and the server keys the channel by it.
 * So this hook is written once and every surface gets the same behaviour
 * instead of four near-copies drifting apart.
 *
 * The socket carries what comes next; the REST read carries what already
 * happened. Both are needed — a panel that opens mid-turn, or survives a
 * reload, would otherwise start blank and stay blank until the agent's next
 * thought — so the poll runs whenever the socket is not up, and stops when it
 * is.
 */
export function useChatTurnThinking(turnId: string | null | undefined): ChatTurnThinking {
  const [frame, setFrame] = useState<ChatThinkingFrame>(EMPTY_THINKING_FRAME);
  const [status, setStatus] = useState<ChatThinkingSocketStatus>("connecting");
  const [done, setDone] = useState(false);
  // Frames carry the whole transcript, so a poll that lands behind a socket
  // push must not rewind the panel.
  const lastSeq = useRef(0);

  useEffect(() => {
    lastSeq.current = 0;
    setFrame(EMPTY_THINKING_FRAME);
    setDone(false);
    if (!turnId) {
      setStatus("closed");
      return;
    }

    const accept = (next: ChatThinkingFrame) => {
      if (next.seq < lastSeq.current) return;
      lastSeq.current = next.seq;
      setFrame(next);
    };

    setStatus("connecting");
    const socket = new ChatThinkingSocket(chatThinkingSocketUrl(API_BASE, turnId), {
      onFrame: accept,
      onStatus: setStatus,
      onDone: () => setDone(true),
    });
    socket.open();

    return () => {
      socket.close();
    };
  }, [turnId]);

  const live = Boolean(turnId) && !done && status === "open";

  useEffect(() => {
    if (!turnId || done || live) return;
    let cancelled = false;

    const read = () => {
      api
        .chatTurnThinking(turnId)
        .then((next) => {
          if (cancelled || next.seq < lastSeq.current) return;
          lastSeq.current = next.seq;
          setFrame(next);
        })
        // A failed read is the socket's problem to recover from, not something
        // to surface: the panel is an aid, and an error toast for it would
        // interrupt the answer the operator is actually waiting for.
        .catch(() => undefined);
    };

    read();
    const timer = window.setInterval(read, FALLBACK_POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [turnId, done, live]);

  if (!turnId) return NOTHING;
  return {
    content: frame.content,
    answer: frame.answer,
    activity: frame.activity,
    isStreaming: !done,
  };
}
