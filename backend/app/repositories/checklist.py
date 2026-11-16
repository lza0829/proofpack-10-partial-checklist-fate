from __future__ import annotations

import sqlite3
from typing import Any

# 清单项命运（与包状态同源）
STATE_PASSED = "passed"      # 通过
STATE_FAILED = "failed"      # 未通过（整包驳回口径下保留，不可放行）
STATE_SUSPENDED = "suspended"  # 挂起（分项挂起策略，待复检/豁免）

VALID_STATES = (STATE_PASSED, STATE_FAILED, STATE_SUSPENDED)


def clear_for_pack(conn: sqlite3.Connection, pack_id: int) -> None:
    conn.execute("DELETE FROM checklist_results WHERE pack_id=?", (pack_id,))
    conn.execute("DELETE FROM checklist_actions WHERE pack_id=?", (pack_id,))


def insert_result(
    conn: sqlite3.Connection,
    *,
    pack_id: int,
    check_code: str,
    check_title: str,
    passed: bool,
    detail: str,
    item_state: str,
    created_at: str,
) -> int:
    if item_state not in VALID_STATES:
        raise ValueError(f"非法清单项状态: {item_state}")
    cur = conn.execute(
        """
        INSERT INTO checklist_results(
            pack_id, check_code, check_title, passed, detail, created_at,
            item_state, resolved, resolve_note, resolved_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
        (
            pack_id,
            check_code,
            check_title,
            1 if passed else 0,
            detail,
            created_at,
            item_state,
            0,
            None,
            None,
        ),
    )
    return int(cur.lastrowid)


def insert_action(
    conn: sqlite3.Connection,
    *,
    pack_id: int,
    check_code: str,
    action: str,
    reason: str | None,
    result_state: str,
    created_at: str,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO checklist_actions(
            pack_id, check_code, action, reason, result_state, created_at
        ) VALUES (?,?,?,?,?,?)
        """,
        (pack_id, check_code, action, reason, result_state, created_at),
    )
    return int(cur.lastrowid)


def list_actions(conn: sqlite3.Connection, pack_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, pack_id, check_code, action, reason, result_state, created_at
        FROM checklist_actions
        WHERE pack_id=?
        ORDER BY id
        """,
        (pack_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def list_results(
    conn: sqlite3.Connection,
    pack_id: int | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    if pack_id is not None:
        rows = conn.execute(
            """
            SELECT id, pack_id, check_code, check_title, passed, detail, created_at,
                   item_state, resolved, resolve_note, resolved_at
            FROM checklist_results
            WHERE pack_id=?
            ORDER BY id
            """,
            (pack_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT id, pack_id, check_code, check_title, passed, detail, created_at,
                   item_state, resolved, resolve_note, resolved_at
            FROM checklist_results
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_result(conn: sqlite3.Connection, pack_id: int, check_code: str) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT id, pack_id, check_code, check_title, passed, detail, created_at,
               item_state, resolved, resolve_note, resolved_at
        FROM checklist_results
        WHERE pack_id=? AND check_code=?
        """,
        (pack_id, check_code),
    ).fetchone()
    return dict(row) if row else None


def mark_resolved(
    conn: sqlite3.Connection,
    *,
    pack_id: int,
    check_code: str,
    passed: bool,
    detail: str,
    note: str,
    resolved_at: str,
) -> None:
    """挂起项经复检通过或豁免后置为已解决（命运仍保留为 suspended，resolved=1 留痕）。"""
    conn.execute(
        """
        UPDATE checklist_results
        SET passed=?, detail=?, resolved=1, resolve_note=?, resolved_at=?
        WHERE pack_id=? AND check_code=?
        """,
        (1 if passed else 0, detail, note, resolved_at, pack_id, check_code),
    )


def all_passed(conn: sqlite3.Connection, pack_id: int) -> bool:
    """所有清单项当前均为通过且不存在未解决挂起。与放行判定同源。"""
    rows = conn.execute(
        "SELECT passed, item_state, resolved FROM checklist_results WHERE pack_id=?",
        (pack_id,),
    ).fetchall()
    if not rows:
        return False
    for r in rows:
        if int(r["passed"]) != 1:
            return False
        if r["item_state"] == STATE_SUSPENDED and int(r["resolved"]) != 1:
            return False
    return True


def has_unresolved(conn: sqlite3.Connection, pack_id: int) -> bool:
    """存在未通过项或未解决挂起——为真即禁止放行。"""
    row = conn.execute(
        """
        SELECT
            SUM(CASE WHEN passed=0 THEN 1 ELSE 0 END) AS failed_cnt,
            SUM(CASE WHEN item_state='suspended' AND resolved=0 THEN 1 ELSE 0 END) AS suspended_cnt
        FROM checklist_results
        WHERE pack_id=?
        """,
        (pack_id,),
    ).fetchone()
    return int(row["failed_cnt"] or 0) > 0 or int(row["suspended_cnt"] or 0) > 0
