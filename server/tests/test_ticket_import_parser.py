from loregarden.services.ticket_import import (
    parse_import_file,
    should_show_import_preview,
)


def test_parse_markdown_ticket():
    content = """# TICKET: 42-sample
Title: Sample ticket
Work item type: bug
Priority: 2

---

## Description
Hello world

---

## Acceptance Criteria
- One
- Two
"""
    batch = parse_import_file("sample.md", content)
    assert not batch.errors
    assert len(batch.tickets) == 1
    ticket = batch.tickets[0]
    assert ticket.title == "Sample ticket"
    assert ticket.external_id == "42-sample"
    assert ticket.work_item_type.value == "bug"
    assert ticket.priority == 2
    assert ticket.description == "Hello world"
    assert ticket.acceptance_criteria == ["One", "Two"]


def test_parse_json_wrapper():
    content = '{"tickets":[{"title":"Wrapped","work_item_type":"milestone"}]}'
    batch = parse_import_file("wrapped.json", content)
    assert not batch.errors
    assert batch.tickets[0].title == "Wrapped"
    assert batch.tickets[0].work_item_type.value == "milestone"


def test_should_show_preview_rules():
    assert should_show_import_preview(total=0, formats=["md"]) is False
    assert should_show_import_preview(total=1, formats=["json"]) is True
    assert should_show_import_preview(total=2, formats=["md"]) is True
    assert should_show_import_preview(total=2, formats=["json"]) is False
    assert should_show_import_preview(total=2, formats=["json", "yaml"]) is False
