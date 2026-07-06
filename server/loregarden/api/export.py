from fastapi import APIRouter, Depends
from loregarden.db.session import get_session
from loregarden.services.export_service import ExportService
from sqlmodel import Session

router = APIRouter(tags=["export"])


@router.post("/export/project-board")
def export_project_board(session: Session = Depends(get_session)) -> dict:
    return ExportService(session).export_project_board()
