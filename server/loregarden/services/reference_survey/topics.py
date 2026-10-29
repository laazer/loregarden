"""What people commonly survey for, as one catalog.

Each topic carries the question a pass reads for, a reason, and what a
discovery run should search for. The add form uses the first two to fill
itself in; `discovery` uses the search hint. A topic is a shortcut, not a
category: a source added with one is an ordinary source.
"""

from __future__ import annotations

from loregarden.models.domain.survey_enums import SurveyTopic
from pydantic import BaseModel


class TopicView(BaseModel):
    id: SurveyTopic
    label: str
    #: What a pass reads for, filled into "What should the survey answer?".
    question: str
    #: Filled into "Why this one?".
    why: str
    #: What a discovery run searches for.
    search: str


TOPICS: dict[SurveyTopic, TopicView] = {
    topic.id: topic
    for topic in (
        TopicView(
            id=SurveyTopic.MEMORY_LAYER,
            label="Improved memory layer",
            question=(
                "How do they capture, store, retrieve, rank and retire what agents learn, and "
                "what would make our memory layer better?"
            ),
            why="It works on agent memory, which ours could do better.",
            search=(
                "open-source long-term memory for LLM agents: memory layers, knowledge graphs of "
                "what agents learn, retrieval and forgetting"
            ),
        ),
        TopicView(
            id=SurveyTopic.UI_UX,
            label="UI/UX improvements and inspiration",
            question=(
                "What layouts, interaction patterns and visual details would make our operator "
                "surfaces clearer and faster to use?"
            ),
            why="Its interface handles dense, live data well.",
            search=(
                "well-designed interfaces for developer tools, agent control panels and dense "
                "data dashboards; design systems and pattern libraries for them"
            ),
        ),
        TopicView(
            id=SurveyTopic.SIMILAR_TOOLS,
            label="Similar tools",
            question=(
                "What do tools that solve the same problem as this project do differently, and "
                "what should we adopt or avoid?"
            ),
            why="It solves the same problem we do.",
            search=(
                "projects with the same purpose as this workspace: read its README and AGENTS.md "
                "first to say what that purpose is, then search for others that share it"
            ),
        ),
        TopicView(
            id=SurveyTopic.AGENT_ORCHESTRATION,
            label="Agent orchestration",
            question=(
                "How do they plan, dispatch, supervise and recover multi-agent work, and what "
                "would make our orchestration more reliable?"
            ),
            why="It orchestrates agents, which is our core.",
            search=(
                "multi-agent orchestration frameworks and control planes: planning, dispatch, "
                "supervision, recovery of failed runs"
            ),
        ),
        TopicView(
            id=SurveyTopic.TESTING_VERIFICATION,
            label="Testing and verification",
            question=(
                "How do they check that agent work is correct before it lands: tests, gates, "
                "evals, review?"
            ),
            why="It verifies agent output, which our gates also do.",
            search=(
                "verifying AI agent output before it ships: evaluation harnesses, automated "
                "gates, test generation, code review agents"
            ),
        ),
    )
}


def topic_list() -> list[TopicView]:
    return list(TOPICS.values())
