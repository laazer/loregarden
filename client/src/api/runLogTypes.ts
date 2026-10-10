/**
 * The payloads behind one agent run's log and the steer channel beside it:
 * GET/POST `/api/runs/{id}/log` and `/api/runs/{id}/messages`.
 *
 * Split out of `types.ts` when it reached its 1200-line cap. Re-exported from
 * there, so every existing importer keeps its path.
 */

export interface LogLine {
  time: string;
  tag: string;
  text: string;
}

/** One run's rendered log, as served by GET /api/runs/{id}/log. */
export interface RunLog {
  id: string;
  run_code: string;
  agent_id: string;
  skill_name: string;
  stage_key: string;
  status: string;
  command: string;
  started_at: string | null;
  finished_at: string | null;
  /** Empty for runs that predate the log streamer. */
  lines: LogLine[];
  live: string | null;
  stderr: string;
  /** `'tmux' | 'file'`, or "" for a run whose row does not record one. */
  transport: string;
  /**
   * The `tmux attach` command for a live tmux-transport run, composed on the
   * server — the session name is derived from the run id as well as the run
   * code, so the client cannot build it. "" when there is nothing to attach to.
   */
  attach_command: string;
}

/** GET /api/runs/{id}/messages — the steer history and why it may be refused. */
export interface RunMessagesPayload {
  messages: RunMessage[];
  /** Empty when this run can be steered; otherwise the reason it cannot. */
  refusal: string;
  /**
   * When a stop was asked for. Latches the composer's `Stopping…`: the
   * mutation's own pending flag resets as soon as the POST returns, long
   * before a detached process group has gone.
   */
  cancel_requested_at: string | null;
}

/** An operator's message to a run that is already in flight. */
export interface RunMessage {
  id: string;
  run_id: string;
  content: string;
  created_at: string;
  /** Null until the bridge has written it into the agent's stdin. */
  delivered_at: string | null;
}
