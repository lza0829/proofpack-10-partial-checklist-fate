from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException

from app.db import connect
from app.repositories import checklist as check_repo
from app.repositories import packs as pack_repo
from app.repositories import samples as sample_repo
from app.repositories import settings as settings_repo


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


REQUIRED_FIELDS = ("business_key", "amount", "qty", "event_ts", "revision", "title")

# 部分失败命运（互斥单选，不做并存开关）
POLICY_REJECT_ALL = "reject_all"        # 整包驳回口径
POLICY_SUSPEND_ITEMS = "suspend_items"  # 分项挂起
VALID_POLICIES = (POLICY_REJECT_ALL, POLICY_SUSPEND_ITEMS)

# 包状态
ST_BUILT = "built"
ST_CHECKED_OK = "checked_ok"
ST_PARTIAL_REJECTED = "partial_rejected"
ST_PARTIAL_SUSPENDED = "partial_suspended"
ST_RELEASED = "released"
ST_REJECTED = "rejected"
TERMINAL_STATES = (ST_RELEASED, ST_REJECTED)


def get_policy(conn) -> str:
    policy = settings_repo.get_setting(conn, "partial_fail_policy", POLICY_REJECT_ALL)
    return policy if policy in VALID_POLICIES else POLICY_REJECT_ALL


# ---- 单项判定：复检复用同一套算法，保证结果同源 ----

def _eval_count(items: list[dict], sample_rows: list[dict], item_count: int) -> tuple[bool, str]:
    ok = len(items) == len(sample_rows) and len(items) == item_count
    detail = f"包条目={len(items)}，样本行={len(sample_rows)}，包记录条数={item_count}"
    return ok, detail


def _eval_unique(items: list[dict]) -> tuple[bool, str]:
    keys = [it["business_key"] for it in items]
    ok = len(keys) == len(set(keys))
    dup = sorted({k for k in keys if keys.count(k) > 1})
    detail = "全部业务键唯一" if ok else f"重复键: {', '.join(dup)}"
    return ok, detail


def _eval_required(items: list[dict]) -> tuple[bool, str]:
    missing: list[str] = []
    for it in items:
        summary = it.get("summary") if isinstance(it.get("summary"), dict) else {}
        merged = {
            "business_key": it.get("business_key"),
            "title": summary.get("title"),
            "amount": summary.get("amount"),
            "qty": summary.get("qty"),
            "event_ts": summary.get("event_ts"),
            "revision": summary.get("revision"),
        }
        for field in REQUIRED_FIELDS:
            val = merged.get(field)
            if val is None or (isinstance(val, str) and val.strip() == ""):
                missing.append(f"{it.get('business_key')}.{field}")
    ok = len(missing) == 0
    detail = "必填字段齐全" if ok else f"缺失: {', '.join(missing[:20])}"
    return ok, detail


_CHECK_TITLES = {
    "count_match": "条数一致",
    "key_unique": "键唯一",
    "required_fields": "必填非空",
}


def _evaluate(conn, pack: dict) -> list[dict[str, Any]]:
    """对包的当前数据跑全部清单项，返回原始判定（不含命运）。"""
    items = pack_repo.list_items(conn, pack["id"])
    sample_rows = sample_repo.list_rows(conn, pack["batch_id"])
    evals = [
        ("count_match", *_eval_count(items, sample_rows, pack["item_count"])),
        ("key_unique", *_eval_unique(items)),
        ("required_fields", *_eval_required(items)),
    ]
    return [
        {
            "check_code": code,
            "check_title": _CHECK_TITLES[code],
            "passed": ok,
            "detail": detail,
        }
        for code, ok, detail in evals
    ]


def _evaluate_one(conn, pack: dict, check_code: str) -> tuple[bool, str]:
    for r in _evaluate(conn, pack):
        if r["check_code"] == check_code:
            return r["passed"], r["detail"]
    raise HTTPException(status_code=404, detail="清单项不存在")


def summarize_results(results: list[dict], policy: str) -> dict[str, Any]:
    """未通过项 / 挂起项与包状态同源——全部取自 checklist_results。"""
    failed = [r for r in results if not r["passed"]]
    suspended_open = [
        r for r in results
        if r["item_state"] == check_repo.STATE_SUSPENDED and not r["resolved"]
    ]
    resolved = [r for r in results if r["resolved"]]
    return {
        "policy": policy,
        "failed_count": len(failed),
        "open_suspension_count": len(suspended_open),
        "resolved_count": len(resolved),
        "failed_codes": [r["check_code"] for r in failed],
        "open_suspension_codes": [r["check_code"] for r in suspended_open],
    }


def list_results(pack_id: int | None = None) -> list[dict[str, Any]]:
    conn = connect()
    try:
        return check_repo.list_results(conn, pack_id=pack_id)
    finally:
        conn.close()


def list_actions(pack_id: int) -> list[dict[str, Any]]:
    conn = connect()
    try:
        pack = pack_repo.get_pack(conn, pack_id)
        if not pack:
            raise HTTPException(status_code=404, detail="证据包不存在")
        return check_repo.list_actions(conn, pack_id)
    finally:
        conn.close()


def run_checklist(pack_id: int) -> dict[str, Any]:
    conn = connect()
    try:
        pack = pack_repo.get_pack(conn, pack_id)
        if not pack:
            raise HTTPException(status_code=404, detail="证据包不存在")
        if pack["status"] in TERMINAL_STATES:
            raise HTTPException(status_code=400, detail="包已终审（放行/驳回），不可重跑清单")

        policy = get_policy(conn)
        now = _now()
        check_repo.clear_for_pack(conn, pack_id)

        evaluated = _evaluate(conn, pack)
        for r in evaluated:
            if r["passed"]:
                item_state = check_repo.STATE_PASSED
            elif policy == POLICY_REJECT_ALL:
                # 整包驳回口径：未通过项全部保留为 failed，禁止放行
                item_state = check_repo.STATE_FAILED
            else:
                # 分项挂起：未通过项标挂起，已通过项保留
                item_state = check_repo.STATE_SUSPENDED
            check_repo.insert_result(
                conn,
                pack_id=pack_id,
                check_code=r["check_code"],
                check_title=r["check_title"],
                passed=r["passed"],
                detail=r["detail"],
                item_state=item_state,
                created_at=now,
            )

        all_ok = all(r["passed"] for r in evaluated)
        if all_ok:
            new_status = ST_CHECKED_OK
        elif policy == POLICY_REJECT_ALL:
            new_status = ST_PARTIAL_REJECTED
        else:
            new_status = ST_PARTIAL_SUSPENDED
        pack_repo.update_status(conn, pack_id, new_status)
        conn.commit()

        results = check_repo.list_results(conn, pack_id=pack_id)
        return {
            "pack_id": pack_id,
            "pack_status": new_status,
            "partial_fail_policy": policy,
            "all_passed": all_ok,
            "item_count": len(pack_repo.list_items(conn, pack_id)),
            "summary": summarize_results(results, policy),
            "results": results,
            "actions": check_repo.list_actions(conn, pack_id),
        }
    finally:
        conn.close()


def _require_suspend_policy(conn, pack: dict) -> str:
    policy = get_policy(conn)
    if policy != POLICY_SUSPEND_ITEMS:
        raise HTTPException(
            status_code=400,
            detail="当前为整包驳回口径，未通过项只可驳回留因或整包复检（重跑清单），不支持逐项处置",
        )
    return policy


def _get_open_suspension(conn, pack_id: int, check_code: str) -> dict[str, Any]:
    row = check_repo.get_result(conn, pack_id, check_code)
    if not row:
        raise HTTPException(status_code=404, detail="清单项不存在")
    if row["item_state"] != check_repo.STATE_SUSPENDED:
        raise HTTPException(status_code=400, detail="该项不是挂起项，无需逐项处置")
    if row["resolved"]:
        raise HTTPException(status_code=400, detail="挂起项已解决，不可重复处置")
    return row


def _refresh_pack_status(conn, pack_id: int) -> str:
    if check_repo.has_unresolved(conn, pack_id):
        status = ST_PARTIAL_SUSPENDED
    else:
        status = ST_CHECKED_OK
    pack_repo.update_status(conn, pack_id, status)
    return status


def recheck_item(pack_id: int, check_code: str) -> dict[str, Any]:
    """对单个挂起项按当前数据复检：通过则解除挂起，仍失败则继续挂起。"""
    conn = connect()
    try:
        pack = pack_repo.get_pack(conn, pack_id)
        if not pack:
            raise HTTPException(status_code=404, detail="证据包不存在")
        if pack["status"] in TERMINAL_STATES:
            raise HTTPException(status_code=400, detail="包已终审，不可复检")
        policy = _require_suspend_policy(conn, pack)
        _get_open_suspension(conn, pack_id, check_code)

        now = _now()
        ok, detail = _evaluate_one(conn, pack, check_code)
        if ok:
            check_repo.mark_resolved(
                conn,
                pack_id=pack_id,
                check_code=check_code,
                passed=True,
                detail=detail,
                note="复检通过",
                resolved_at=now,
            )
            result_state = check_repo.STATE_PASSED
        else:
            # 复检仍未通过：挂起保留，不做 resolved
            result_state = check_repo.STATE_SUSPENDED
        check_repo.insert_action(
            conn,
            pack_id=pack_id,
            check_code=check_code,
            action="recheck",
            reason=None,
            result_state=result_state,
            created_at=now,
        )
        status = _refresh_pack_status(conn, pack_id)
        conn.commit()

        results = check_repo.list_results(conn, pack_id=pack_id)
        return {
            "pack_id": pack_id,
            "pack_status": status,
            "check_code": check_code,
            "recheck_passed": ok,
            "detail": detail,
            "summary": summarize_results(results, policy),
            "results": results,
            "actions": check_repo.list_actions(conn, pack_id),
        }
    finally:
        conn.close()


def exempt_item(pack_id: int, check_code: str, reason: str | None) -> dict[str, Any]:
    """豁免挂起项：原因强制留痕；豁免后该项视为已解决，但挂起身份与留痕保留。"""
    reason = (reason or "").strip()
    if not reason:
        raise HTTPException(status_code=400, detail="豁免须填写原因")
    conn = connect()
    try:
        pack = pack_repo.get_pack(conn, pack_id)
        if not pack:
            raise HTTPException(status_code=404, detail="证据包不存在")
        if pack["status"] in TERMINAL_STATES:
            raise HTTPException(status_code=400, detail="包已终审，不可豁免")
        policy = _require_suspend_policy(conn, pack)
        row = _get_open_suspension(conn, pack_id, check_code)

        now = _now()
        note = f"豁免：{reason}"
        check_repo.mark_resolved(
            conn,
            pack_id=pack_id,
            check_code=check_code,
            passed=True,
            detail=row["detail"],
            note=note,
            resolved_at=now,
        )
        check_repo.insert_action(
            conn,
            pack_id=pack_id,
            check_code=check_code,
            action="exempt",
            reason=reason,
            result_state="exempted",
            created_at=now,
        )
        status = _refresh_pack_status(conn, pack_id)
        conn.commit()

        results = check_repo.list_results(conn, pack_id=pack_id)
        return {
            "pack_id": pack_id,
            "pack_status": status,
            "check_code": check_code,
            "exempted": True,
            "reason": reason,
            "summary": summarize_results(results, policy),
            "results": results,
            "actions": check_repo.list_actions(conn, pack_id),
        }
    finally:
        conn.close()
