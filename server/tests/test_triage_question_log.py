import json

from fastapi.testclient import TestClient
from loregarden.models.domain import Approval, ApprovalKind, ApprovalStatus, Ticket, TriageMessage
from loregarden.services.triage_question_log import format_answer_message, format_question_message
from sqlmodel import Session, select

TWO_QUESTIONS = {
    "questions": [
        {
            "question": "Which environments get this first?",
            "header": "Rollout",
            "options": [{"label": "Staging first"}, {"label": "Production first"}],
        },
        {
            "question": "Who approves it?",
            "header": "Approver",
            "options": [{"label": "Platform lead"}, {"label": "Anyone"}],
        },
    ]
}

ONE_QUESTION = {
    "questions": [
        {
            "question": "Pick a runner?",
            "header": "Runner",
            "options": [{"label": "pytest"}, {"label": "npm test"}],
        }
    ]
}


def _seed_question_approval(stage_key: str, tool_input: dict) -> tuple[str, str]:
    from loregarden.db.session import engine

    with Session(engine) as session:
        ticket = session.exec(select(Ticket).limit(1)).first()
        approval = Approval(
            ticket_id=ticket.id,
            workspace_id=ticket.workspace_id,
            kind=ApprovalKind.CLI_QUESTION,
            title="Agent questions",
            stage_key=stage_key,
            tool_name="AskUserQuestion",
            tool_input_json=json.dumps(tool_input),
            status=ApprovalStatus.PENDING,
        )
        session.add(approval)
        session.commit()
        session.refresh(approval)
        return approval.id, ticket.id


def _triage_messages(ticket_id: str) -> list[TriageMessage]:
    from loregarden.db.session import engine

    with Session(engine) as session:
        return list(
            session.exec(
                select(TriageMessage)
                .where(TriageMessage.ticket_id == ticket_id)
                .order_by(TriageMessage.created_at.asc())
            ).all()
        )


class TestAnsweredTriageQuestionsReachTheChat:
    def test_question_and_answer_are_appended_to_the_conversation(self, client: TestClient):
        approval_id, ticket_id = _seed_question_approval("triage", TWO_QUESTIONS)
        before = len(_triage_messages(ticket_id))

        resolved = client.post(
            f"/api/inbox/approvals/{approval_id}",
            json={
                "action": "approve",
                "answers": {
                    "Which environments get this first?": "Staging first",
                    "Who approves it?": "Platform lead",
                },
            },
        )
        assert resolved.status_code == 200, resolved.text

        messages = _triage_messages(ticket_id)
        assert len(messages) == before + 2
        question_msg, answer_msg = messages[-2], messages[-1]
        assert question_msg.role == "assistant"
        assert "Which environments get this first?" in question_msg.content
        assert "Who approves it?" in question_msg.content
        assert answer_msg.role == "user"
        assert "Staging first" in answer_msg.content
        assert "Platform lead" in answer_msg.content

    def test_a_stage_run_question_stays_out_of_the_triage_chat(self, client: TestClient):
        """Questions a stage run asks belong to that run, not to a conversation."""
        approval_id, ticket_id = _seed_question_approval("implementation", ONE_QUESTION)
        before = len(_triage_messages(ticket_id))

        resolved = client.post(
            f"/api/inbox/approvals/{approval_id}",
            json={"action": "approve", "answers": {"Pick a runner?": "pytest"}},
        )
        assert resolved.status_code == 200, resolved.text
        assert len(_triage_messages(ticket_id)) == before

    def test_a_rejected_question_records_nothing(self, client: TestClient):
        approval_id, ticket_id = _seed_question_approval("triage", ONE_QUESTION)
        before = len(_triage_messages(ticket_id))

        resolved = client.post(f"/api/inbox/approvals/{approval_id}", json={"action": "reject"})
        assert resolved.status_code == 200, resolved.text
        assert len(_triage_messages(ticket_id)) == before

    def test_the_answer_still_reaches_the_agent(self, client: TestClient):
        """Mirroring into the chat must not disturb the tool result the CLI is waiting on."""
        approval_id, _ = _seed_question_approval("triage", ONE_QUESTION)

        client.post(
            f"/api/inbox/approvals/{approval_id}",
            json={"action": "approve", "answers": {"Pick a runner?": "pytest"}},
        )

        from loregarden.db.session import engine

        with Session(engine) as session:
            stored = session.get(Approval, approval_id)
            payload = json.loads(stored.response_json)
        assert payload["updated_input"]["answers"]["Pick a runner?"] == "pytest"


class TestMessageFormatting:
    def test_a_single_question_is_not_bulleted(self):
        assert format_question_message(ONE_QUESTION) == "Pick a runner?"

    def test_multiple_questions_are_listed(self):
        text = format_question_message(TWO_QUESTIONS)
        assert text == "- Which environments get this first?\n- Who approves it?"

    def test_a_single_answer_stands_alone(self):
        text = format_answer_message(ONE_QUESTION, {"Pick a runner?": "pytest"})
        assert text == "pytest"

    def test_multiple_answers_quote_their_questions(self):
        text = format_answer_message(
            TWO_QUESTIONS,
            {
                "Which environments get this first?": "Staging first",
                "Who approves it?": "Platform lead",
            },
        )
        assert "- Which environments get this first?\n  Staging first" in text
        assert "- Who approves it?\n  Platform lead" in text

    def test_multi_select_answers_are_joined(self):
        text = format_answer_message(ONE_QUESTION, {"Pick a runner?": ["pytest", "npm test"]})
        assert text == "pytest, npm test"

    def test_free_text_response_wins_over_options(self):
        text = format_answer_message(ONE_QUESTION, {}, response="  use whatever CI uses  ")
        assert text == "use whatever CI uses"

    def test_an_unanswerable_payload_yields_nothing(self):
        assert format_question_message({"questions": []}) == ""
        assert format_answer_message({"questions": []}, {}) == ""
