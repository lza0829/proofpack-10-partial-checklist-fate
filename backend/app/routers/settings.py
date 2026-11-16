from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from app.services import settings_service as svc

router = APIRouter(tags=["settings"])


class PolicyBody(BaseModel):
    partial_fail_policy: str


@router.get("/settings")
def list_settings() -> dict[str, Any]:
    return svc.list_settings()


@router.get("/settings/partial-fail-policy")
def get_policy() -> dict[str, str]:
    return svc.get_partial_fail_policy()


@router.put("/settings/partial-fail-policy")
def set_policy(body: PolicyBody) -> dict[str, str]:
    return svc.set_partial_fail_policy(body.partial_fail_policy)
