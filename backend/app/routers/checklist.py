from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.services import checklist_service as svc

router = APIRouter(tags=["checklist"])


class ChecklistRunBody(BaseModel):
    pack_id: int


class ItemBody(BaseModel):
    pack_id: int
    check_code: str


class ExemptBody(BaseModel):
    pack_id: int
    check_code: str
    reason: str | None = None


@router.get("/checklist")
def list_checklist(pack_id: int | None = Query(default=None)):
    return svc.list_results(pack_id=pack_id)


@router.get("/checklist/actions")
def list_actions(pack_id: int = Query(...)):
    return svc.list_actions(pack_id)


@router.post("/checklist/run")
def run_checklist(body: ChecklistRunBody) -> dict[str, Any]:
    return svc.run_checklist(body.pack_id)


@router.post("/checklist/recheck")
def recheck_item(body: ItemBody) -> dict[str, Any]:
    return svc.recheck_item(body.pack_id, body.check_code)


@router.post("/checklist/exempt")
def exempt_item(body: ExemptBody) -> dict[str, Any]:
    return svc.exempt_item(body.pack_id, body.check_code, body.reason)
