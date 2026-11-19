# Long Home chat turns fail with "Agent run lease expired" while the agent is still answering

The repository is at `/app`; the Python server is in `/app/server` (run its tests with `cd /app/server && uv run pytest <path>`).

Home chat turns never renew their run lease, so the reconciliation sweep fails every chat turn that runs longer than `AGENT_RUN_LEASE` (10 minutes) while its agent is still answering. The run row reads `failed` ("Agent run lease expired") while the reply stays pending and the agent keeps working.

Ticket stages and ticket triage hold their lease through `supervised()`; chat turns go through `agent_turn_runner` (the permission bridge for claude, and the one-shot CLI for other adapters), and neither path renews. Make a chat turn hold its lease for the whole turn, on both paths.
