from __future__ import annotations

import os
import tempfile

import pytest

# 每个测试进程使用独立临时库，与真实数据隔离
_TMP = tempfile.mkdtemp(prefix="proofpack-test-")
os.environ["DATA_DIR"] = _TMP

from app.seed import init_db  # noqa: E402
from app.db import connect  # noqa: E402
from app.services import sample_service, pack_service, checklist_service, release_service  # noqa: E402
from app.services import settings_service  # noqa: E402
from app.repositories import checklist as check_repo  # noqa: E402
from app.repositories import packs as pack_repo  # noqa: E402
from fastapi import HTTPException  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _init():
    init_db()


@pytest.fixture()
def conn():
    c = connect()
    try:
        yield c
    finally:
        c.close()


def _make_pack_with_failures(conn, *, break_unique: bool = True, break_required: bool = False):
    """采样 → 打包 → 直接改库制造清单未通过项，返回 pack_id。"""
    batch = sample_service.run_sample(subject_id=1, rate=0.5)
    pack = pack_service.build_pack(batch["id"])
    pack_id = pack["id"]
    if break_unique:
        rows = conn.execute(
            "SELECT id, business_key FROM pack_items WHERE pack_id=? ORDER BY id LIMIT 2",
            (pack_id,),
        ).fetchall()
        if len(rows) >= 2:
            conn.execute(
                "UPDATE pack_items SET business_key=? WHERE id=?",
                (rows[0]["business_key"], rows[1]["id"]),
            )
    if break_required:
        row = conn.execute(
            "SELECT id FROM pack_items WHERE pack_id=? ORDER BY id LIMIT 1", (pack_id,)
        ).fetchone()
        conn.execute(
            "UPDATE pack_items SET summary_json=? WHERE id=?",
            ('{"title": null, "amount": null, "qty": 1, "event_ts": "t", "revision": 1}', row["id"]),
        )
    conn.commit()
    return pack_id


def _fix_unique(conn, pack_id: int) -> None:
    """把实际重复键中的一行改成全新唯一键，恢复键唯一。"""
    dup = conn.execute(
        "SELECT business_key FROM pack_items WHERE pack_id=? "
        "GROUP BY business_key HAVING COUNT(*)>1 LIMIT 1",
        (pack_id,),
    ).fetchone()
    row = conn.execute(
        "SELECT id FROM pack_items WHERE pack_id=? AND business_key=? ORDER BY id DESC LIMIT 1",
        (pack_id, dup["business_key"]),
    ).fetchone()
    conn.execute(
        "UPDATE pack_items SET business_key=? WHERE id=?",
        (f"FIXED-{pack_id}-{row['id']}", row["id"]),
    )
    conn.commit()


def _set_policy(policy: str) -> None:
    settings_service.set_partial_fail_policy(policy)


# ---------- 路径 1：全过 → 放行 ----------

def test_all_pass_then_release(conn):
    batch = sample_service.run_sample(subject_id=1, rate=0.5)
    pack = pack_service.build_pack(batch["id"])
    out = checklist_service.run_checklist(pack["id"])
    assert out["all_passed"] is True
    assert out["pack_status"] == "checked_ok"

    decision = release_service.decide(pack["id"], "pass")
    assert decision["decision"] == "pass"
    assert decision["pack_status"] == "released"
    assert decision["blocking"] is None

    fresh = pack_repo.get_pack(conn, pack["id"])
    assert fresh["status"] == "released"


# ---------- 路径 2：整包驳回口径 ----------

def test_reject_all_policy_blocks_pass_and_keeps_failed_items(conn):
    _set_policy("reject_all")
    pack_id = _make_pack_with_failures(conn)

    out = checklist_service.run_checklist(pack_id)
    assert out["all_passed"] is False
    assert out["pack_status"] == "partial_rejected"
    # 未通过项全部保留为 failed，通过项仍为 passed
    states = {r["check_code"]: r["item_state"] for r in out["results"]}
    assert states["key_unique"] == "failed"
    assert states["count_match"] == "passed"
    assert out["summary"]["failed_count"] >= 1
    assert out["summary"]["open_suspension_count"] == 0

    # 禁止放行通过
    with pytest.raises(HTTPException) as ei:
        release_service.decide(pack_id, "pass")
    assert ei.value.status_code == 400
    assert "拒绝放行" in ei.value.detail

    # 挂起策略专属动作在该口径下被拒绝
    with pytest.raises(HTTPException) as ei:
        checklist_service.recheck_item(pack_id, "key_unique")
    assert ei.value.status_code == 400
    with pytest.raises(HTTPException) as ei:
        checklist_service.exempt_item(pack_id, "key_unique", "特殊情况")
    assert ei.value.status_code == 400

    # 只能驳回留因，拒绝原因与清单结果同源
    decision = release_service.decide(pack_id, "reject", reason="键重复，整包驳回")
    assert decision["pack_status"] == "rejected"
    assert "同源阻断" in decision["reason"]
    assert "键唯一" in decision["reason"]
    # 未通过项在驳回后仍保留
    results = check_repo.list_results(conn, pack_id=pack_id)
    assert any(not r["passed"] and r["item_state"] == "failed" for r in results)


def test_reject_all_recheck_via_rerun_after_data_fix(conn):
    """整包口径的复检 = 修复后重跑清单，全过即可放行。"""
    _set_policy("reject_all")
    pack_id = _make_pack_with_failures(conn)
    checklist_service.run_checklist(pack_id)
    assert pack_repo.get_pack(conn, pack_id)["status"] == "partial_rejected"

    # 修复数据（恢复唯一键）后整包复检
    _fix_unique(conn, pack_id)

    out = checklist_service.run_checklist(pack_id)
    assert out["all_passed"] is True
    assert out["pack_status"] == "checked_ok"
    decision = release_service.decide(pack_id, "pass")
    assert decision["pack_status"] == "released"


# ---------- 路径 3：分项挂起，未清挂起被拒 ----------

def test_suspend_policy_unresolved_blocks_release(conn):
    _set_policy("suspend_items")
    pack_id = _make_pack_with_failures(conn)

    out = checklist_service.run_checklist(pack_id)
    assert out["pack_status"] == "partial_suspended"
    states = {r["check_code"]: r["item_state"] for r in out["results"]}
    assert states["key_unique"] == "suspended"
    # 已通过项保留
    assert states["count_match"] == "passed"
    assert out["summary"]["open_suspension_count"] >= 1

    # 挂起未清 → 放行被拒
    with pytest.raises(HTTPException) as ei:
        release_service.decide(pack_id, "pass")
    assert ei.value.status_code == 400
    assert "未解决挂起项" in ei.value.detail

    # 豁免必须留原因
    with pytest.raises(HTTPException) as ei:
        checklist_service.exempt_item(pack_id, "key_unique", "  ")
    assert ei.value.status_code == 400


# ---------- 路径 4：挂起清掉后放行（复检通过 / 豁免两条子路径） ----------

def test_suspend_policy_recheck_pass_then_release(conn):
    _set_policy("suspend_items")
    pack_id = _make_pack_with_failures(conn)
    checklist_service.run_checklist(pack_id)

    # 先复检一次：数据未修复，仍挂起，包状态不变
    again = checklist_service.recheck_item(pack_id, "key_unique")
    assert again["recheck_passed"] is False
    assert pack_repo.get_pack(conn, pack_id)["status"] == "partial_suspended"

    # 修复数据后复检通过
    _fix_unique(conn, pack_id)
    out = checklist_service.recheck_item(pack_id, "key_unique")
    assert out["recheck_passed"] is True
    assert out["pack_status"] == "checked_ok"
    target = next(r for r in out["results"] if r["check_code"] == "key_unique")
    assert target["resolved"] == 1
    assert target["resolve_note"] == "复检通过"

    decision = release_service.decide(pack_id, "pass")
    assert decision["pack_status"] == "released"
    assert decision["blocking"] is None


def test_suspend_policy_exempt_with_reason_then_release(conn):
    _set_policy("suspend_items")
    pack_id = _make_pack_with_failures(conn)
    checklist_service.run_checklist(pack_id)

    out = checklist_service.exempt_item(pack_id, "key_unique", "重复键系同源系统补数，已人工核证")
    assert out["pack_status"] == "checked_ok"
    target = next(r for r in out["results"] if r["check_code"] == "key_unique")
    assert target["resolved"] == 1
    assert "豁免" in target["resolve_note"]
    # 豁免留痕
    actions = check_repo.list_actions(conn, pack_id)
    assert any(a["action"] == "exempt" and a["reason"] for a in actions)

    decision = release_service.decide(pack_id, "pass", reason="挂起已豁免")
    assert decision["pack_status"] == "released"


def test_suspend_multiple_failures_must_clear_all(conn):
    """两个未通过项：只解决一个仍不得放行。"""
    _set_policy("suspend_items")
    pack_id = _make_pack_with_failures(conn, break_unique=True, break_required=True)
    out = checklist_service.run_checklist(pack_id)
    assert out["summary"]["open_suspension_count"] >= 2

    checklist_service.exempt_item(pack_id, "key_unique", "原因A")
    with pytest.raises(HTTPException) as ei:
        release_service.decide(pack_id, "pass")
    assert ei.value.status_code == 400

    checklist_service.exempt_item(pack_id, "required_fields", "原因B")
    decision = release_service.decide(pack_id, "pass")
    assert decision["pack_status"] == "released"


# ---------- 铁律与互斥性 ----------

def test_policy_is_mutually_exclusive_single_value(conn):
    settings_service.set_partial_fail_policy("suspend_items")
    settings = settings_service.list_settings()
    assert settings["partial_fail_policy"] == "suspend_items"
    assert "require_checklist_pass" not in settings  # 旧并存开关已移除

    settings_service.set_partial_fail_policy("reject_all")
    assert settings_service.get_partial_fail_policy()["partial_fail_policy"] == "reject_all"

    with pytest.raises(HTTPException) as ei:
        settings_service.set_partial_fail_policy("both")
    assert ei.value.status_code == 400


def test_terminal_pack_cannot_change_fate(conn):
    _set_policy("reject_all")
    pack_id = _make_pack_with_failures(conn)
    checklist_service.run_checklist(pack_id)
    release_service.decide(pack_id, "reject", reason="终局驳回")

    with pytest.raises(HTTPException) as ei:
        release_service.decide(pack_id, "pass")
    assert ei.value.status_code == 400
    with pytest.raises(HTTPException) as ei:
        checklist_service.run_checklist(pack_id)
    assert ei.value.status_code == 400


def test_cannot_run_release_before_checklist(conn):
    batch = sample_service.run_sample(subject_id=1, rate=0.5)
    pack = pack_service.build_pack(batch["id"])
    with pytest.raises(HTTPException) as ei:
        release_service.decide(pack["id"], "pass")
    assert ei.value.status_code == 400
