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


def test_parse_markdown_ticket_falls_back_to_h1_heading_when_title_line_missing():
    """Ticket 85: many hand-written/exported markdown tickets use a plain
    `# Heading` for the title instead of the `Title:` key-value line. Smart
    import should derive the title from that heading rather than rejecting
    the whole file.
    """
    content = """# Sample ticket from another tool

## Description
Hello world

## Acceptance Criteria
- One
- Two
"""
    batch = parse_import_file("sample.md", content)
    assert not batch.errors
    assert len(batch.tickets) == 1
    ticket = batch.tickets[0]
    assert ticket.title == "Sample ticket from another tool"
    assert ticket.description == "Hello world"
    assert ticket.acceptance_criteria == ["One", "Two"]


def test_parse_markdown_ticket_prefers_explicit_title_line_over_heading():
    content = """# Heading text
Title: Explicit title wins

## Description
Hello world
"""
    batch = parse_import_file("sample.md", content)
    assert not batch.errors
    assert batch.tickets[0].title == "Explicit title wins"


def test_parse_markdown_ticket_without_title_line_or_heading_still_errors():
    content = """Work item type: bug

## Description
No title anywhere in this file.
"""
    batch = parse_import_file("sample.md", content)
    assert not batch.tickets
    assert len(batch.errors) == 1
    assert "Title" in batch.errors[0]


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
