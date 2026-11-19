# Background work started from a Home chat can never register a chat wait

The repository is at `/app`; the Python server is in `/app/server` (run its tests with `cd /app/server && uv run pytest <path>`).

`loregarden_register_chat_wait` lets a chat agent register a detached background process so the chat resumes when it finishes. From Home and branch chats it is always refused. The tool's origin check refuses whenever the origin run has no `agent_pid`, but chat turns spawned through the permission bridge deliberately record no pid (a pid would make a restarted server adopt a run it can no longer answer). As a result every Home registration is refused, even for a properly detached child; `chat_waits` has never held a row.

Also, the command the tool tells agents to wrap their work in uses `setsid`, which macOS does not ship.

Make registration work for detached children of bridge-spawned chat turns, while still refusing a child that is not detached from the server's session, and a detached run whose identity record failed. Give agents a wrap recipe that works on macOS and records the child's exit code.

## Interface the tests expect

- A new module `loregarden/agents/chat_wait_recipe.py` exposes `DETACH_RECIPE: str`, a POSIX `sh` script template. It contains the literal placeholder `<command>`, reads the log path from the environment variable `LOG`, starts the command as the leader of a new session with its output going to `$LOG`, prints only the child's pid on stdout, and writes the command's exit code to `$LOG.rc` when it finishes (even if the command calls `exit`).
- The `loregarden_register_chat_wait` tool description includes `DETACH_RECIPE` verbatim and no longer mentions `setsid sh`.
