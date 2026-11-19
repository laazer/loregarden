# Blocking operator questions are dropped or missing from the feed

The repository is at `/app`; the Python server is in `/app/server` (run its tests with `cd /app/server && uv run pytest <path>`).

Operator questions asked by agents (`loregarden_ask_operator` and the operator-question feed) have two defects with **blocking** questions:

1. A stage holds one open decision. Asking a second blocking question on the same stage returns the first question's id with `already_asked: false` and files nothing, so the agent believes it asked. Asking the *same* question again should still report it as already asked; a *different* second question must say plainly that it was not filed, and tell the agent what to do next.
2. Blocking questions never appear in `loregarden_list_operator_questions`, and its `holding_gate` count stays 0 while one is holding the gate. The feed must list blocking operator questions with their default, confidence, target stage and wording, and an answered one's outcome (confirmed or overridden). Decisions raised by a stage report are not operator questions and stay out of the feed.

## Interface the tests expect

- The result of asking a question (the dataclass in `services/operator_questions.py` that already carries `existing: bool`) gains `displaced: bool`: true when a *different* blocking question was not filed because the stage already holds one open decision. `approval` is then the open decision, `existing` is false, and `next_step` contains the words `NOT filed`.
- Re-asking the same question keeps returning `existing=True`, `displaced=False`.
