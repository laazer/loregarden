"""`loregarden survey import-legacy`: move the scratch survey record into the database.

Run once, from a checkout of main, against `scratch/ref_projects/` in the main
checkout. `--dry-run` parses and reports, then rolls back. The import is
idempotent, so a second run changes nothing.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from loregarden.cli.errors import UsageError
from loregarden.cli.mcp_tools import refuse_stale_write
from loregarden.db import session as db_session
from loregarden.services.reference_survey.legacy_import import import_legacy
from sqlmodel import Session


def _import(args: argparse.Namespace) -> str:
    directory = Path(args.dir).expanduser().resolve()
    if not directory.is_dir():
        raise UsageError(f"{directory} is not a directory")
    db_session.init_db()
    refuse_stale_write("survey import-legacy", allow_stale=False, supervised=False)
    with Session(db_session.engine) as session:
        report = import_legacy(
            session, directory, workspace_slug=args.workspace, commit=not args.dry_run
        )
    payload = report.as_dict()
    payload["dry_run"] = args.dry_run
    if not all(report.lossless.values()):
        raise UsageError(f"Not lossless, so nothing was written: {json.dumps(payload)}")
    return json.dumps(payload, indent=2)


def register(groups: argparse._SubParsersAction) -> None:
    group = groups.add_parser("survey", help="The reference survey's record.")
    commands = group.add_subparsers(dest="command", required=True)
    legacy = commands.add_parser(
        "import-legacy",
        help="Import scratch/ref_projects (SURVEY_QUEUE.md, ref_gh_links.md, UXBRIEF.md), losslessly.",
    )
    legacy.add_argument("--dir", required=True, help="The scratch/ref_projects directory.")
    legacy.add_argument(
        "--workspace", default="loregarden", help="Workspace for targets with no hint."
    )
    legacy.add_argument("--dry-run", action="store_true", help="Parse and report; write nothing.")
    legacy.set_defaults(run=_import)
