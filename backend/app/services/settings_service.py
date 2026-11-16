from __future__ import annotations

from fastapi import HTTPException

from app.db import connect
from app.repositories import settings as settings_repo
from app.services.checklist_service import POLICY_REJECT_ALL, VALID_POLICIES


def list_settings() -> dict[str, str]:
    conn = connect()
    try:
        return settings_repo.list_settings(conn)
    finally:
        conn.close()


def get_partial_fail_policy() -> dict[str, str]:
    conn = connect()
    try:
        value = settings_repo.get_setting(conn, "partial_fail_policy", POLICY_REJECT_ALL)
        if value not in VALID_POLICIES:
            value = POLICY_REJECT_ALL
        return {"partial_fail_policy": value}
    finally:
        conn.close()


def set_partial_fail_policy(policy: str) -> dict[str, str]:
    policy = (policy or "").strip().lower()
    if policy not in VALID_POLICIES:
        raise HTTPException(
            status_code=400,
            detail="partial_fail_policy 须为 reject_all（整包驳回口径）或 suspend_items（分项挂起），二者互斥单选",
        )
    conn = connect()
    try:
        settings_repo.set_setting(conn, "partial_fail_policy", policy)
        # 单一互斥策略，不留任何并存开关
        conn.execute("DELETE FROM settings WHERE key='require_checklist_pass'")
        conn.commit()
        return {"partial_fail_policy": policy}
    finally:
        conn.close()
