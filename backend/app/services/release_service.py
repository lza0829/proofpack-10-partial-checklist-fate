from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException

from app.db import connect
from app.repositories import checklist as check_repo
from app.repositories import packs as pack_repo
from app.repositories import releases as release_repo
from app.services import checklist_service as check_svc


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def list_releases(limit: int = 50) -> list[dict[str, Any]]:
    conn = connect()
    try:
        rows = release_repo.list_decisions(conn, limit=limit)
        packs = {p["id"]: p for p in pack_repo.list_packs(conn, limit=500)}
        for r in rows:
            p = packs.get(r["pack_id"])
            r["pack_no"] = p["pack_no"] if p else None
            r["pack_status"] = p["status"] if p else None
        return rows
    finally:
        conn.close()


def _blocking_reason(results: list[dict], policy: str) -> str | None:
    """与包状态、未通过项列表同源：按互斥策略各取一份阻断名单。"""
    if policy == check_svc.POLICY_REJECT_ALL:
        # 整包驳回口径：未通过项（item_state=failed）全部保留，禁止放行
        blocked = [r for r in results if not r["passed"]]
        if not blocked:
            return None
        names = "、".join(r["check_title"] for r in blocked)
        return f"整包驳回口径：未通过项（{names}）；禁止放行，只能驳回留因或复检"
    # 分项挂起口径：未解决挂起即阻断（其本身必为未通过，无需重复计名）
    open_suspended = [
        r for r in results
        if r["item_state"] == check_repo.STATE_SUSPENDED and not r["resolved"]
    ]
    if not open_suspended:
        return None
    names = "、".join(r["check_title"] for r in open_suspended)
    return f"分项挂起口径：未解决挂起项（{names}）；须逐项复检或豁免并留因后才可放行"


def decide(pack_id: int, decision: str, reason: str | None = None) -> dict[str, Any]:
    decision = (decision or "").strip().lower()
    if decision not in ("pass", "reject"):
        raise HTTPException(status_code=400, detail="决策须为 pass 或 reject")

    conn = connect()
    try:
        pack = pack_repo.get_pack(conn, pack_id)
        if not pack:
            raise HTTPException(status_code=404, detail="证据包不存在")
        if pack["status"] in check_svc.TERMINAL_STATES:
            raise HTTPException(status_code=400, detail="包已终审，不可重复决策")

        results = check_repo.list_results(conn, pack_id=pack_id)
        if not results:
            raise HTTPException(status_code=400, detail="请先跑校验清单")

        policy = check_svc.get_policy(conn)
        blocked = _blocking_reason(results, policy)
        all_ok = check_repo.all_passed(conn, pack_id)

        if decision == "pass":
            # 铁律：仍有未通过项或未解决挂起时，绝不写出放行通过
            if blocked:
                raise HTTPException(status_code=400, detail=f"拒绝放行：{blocked}")
            if not reason:
                reason = "清单全部通过（挂起项均已闭环），予以放行"
            status = check_svc.ST_RELEASED
        else:
            if not reason or not str(reason).strip():
                raise HTTPException(status_code=400, detail="驳回须填写原因")
            if blocked:
                # 拒绝原因与清单结果同源：自动附上同源阻断项，人工原因保留在前
                reason = f"{reason.strip()}｜同源阻断：{blocked}"
            status = check_svc.ST_REJECTED

        now = _now()
        rid = release_repo.create_decision(
            conn,
            pack_id=pack_id,
            decision=decision,
            reason=reason,
            created_at=now,
        )
        pack_repo.update_status(conn, pack_id, status)
        conn.commit()

        return {
            "id": rid,
            "pack_id": pack_id,
            "pack_no": pack["pack_no"],
            "pack_status": status,
            "decision": decision,
            "reason": reason,
            "created_at": now,
            "partial_fail_policy": policy,
            "checklist_all_passed": all_ok,
            "blocking": blocked,
            "summary": check_svc.summarize_results(results, policy),
        }
    finally:
        conn.close()
