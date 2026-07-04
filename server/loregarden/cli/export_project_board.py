"""Export project_board markdown from SQLite ticket state."""

from __future__ import annotations

import sys

from sqlmodel import Session, select

from loregarden.db.session import engine, init_db
from loregarden.models.domain import Ticket
from loregarden.services.export_service import ExportService
from loregarden.services.seed import seed_database


def main() -> int:
    init_db()
    with Session(engine) as session:
        if not session.exec(select(Ticket)).first():
            seed_database(session)
        result = ExportService(session).export_project_board()
    print(f"exported {result['exported']} tickets")
    for path in result["paths"]:
        print(f"  {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
