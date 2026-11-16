from __future__ import annotations

from datetime import datetime, timezone

from app.db import connect

# 举证对象种子
SUBJECT_SEEDS = [
    {
        "code": "store_dayend",
        "title": "门店日结抽检",
        "domain": "store",
        "fixture": "store_dayend.json",
        "sample_rate_default": 0.3,
        "enabled": 1,
    },
    {
        "code": "channel_settle",
        "title": "渠道结算抽检",
        "domain": "channel",
        "fixture": "channel_settle.json",
        "sample_rate_default": 0.3,
        "enabled": 1,
    },
    {
        "code": "price_change",
        "title": "价目变更核证",
        "domain": "price",
        "fixture": "price_change.json",
        "sample_rate_default": 0.3,
        "enabled": 1,
    },
]


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _ensure_column(conn, table: str, column: str, ddl: str) -> None:
    cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def init_db() -> None:
    conn = connect()
    try:
        conn.executescript(
            """
            -- 举证对象
            CREATE TABLE IF NOT EXISTS subjects(
                id INTEGER PRIMARY KEY,
                code TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                domain TEXT NOT NULL,
                fixture TEXT NOT NULL,
                sample_rate_default REAL NOT NULL DEFAULT 0.3,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );
            -- 采样批次
            CREATE TABLE IF NOT EXISTS sample_batches(
                id INTEGER PRIMARY KEY,
                batch_no TEXT NOT NULL UNIQUE,
                subject_id INTEGER NOT NULL,
                seed TEXT NOT NULL,
                rate REAL NOT NULL,
                source_count INTEGER NOT NULL,
                sample_count INTEGER NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(subject_id) REFERENCES subjects(id)
            );
            -- 样本行
            CREATE TABLE IF NOT EXISTS sample_rows(
                id INTEGER PRIMARY KEY,
                batch_id INTEGER NOT NULL,
                business_key TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                event_ts TEXT,
                revision INTEGER,
                FOREIGN KEY(batch_id) REFERENCES sample_batches(id)
            );
            -- 证据包
            CREATE TABLE IF NOT EXISTS evidence_packs(
                id INTEGER PRIMARY KEY,
                pack_no TEXT NOT NULL UNIQUE,
                batch_id INTEGER NOT NULL,
                subject_id INTEGER NOT NULL,
                item_count INTEGER NOT NULL,
                fingerprint TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(batch_id) REFERENCES sample_batches(id),
                FOREIGN KEY(subject_id) REFERENCES subjects(id)
            );
            -- 证据包条目
            CREATE TABLE IF NOT EXISTS pack_items(
                id INTEGER PRIMARY KEY,
                pack_id INTEGER NOT NULL,
                business_key TEXT NOT NULL,
                summary_json TEXT NOT NULL,
                item_fingerprint TEXT NOT NULL,
                FOREIGN KEY(pack_id) REFERENCES evidence_packs(id)
            );
            -- 校验清单结果
            -- item_state: passed 通过 / failed 未通过(整包驳回口径下保留) / suspended 挂起(分项挂起策略)
            --   resolved=1 表示挂起项已复检通过或豁免；处置留痕见 checklist_actions
            CREATE TABLE IF NOT EXISTS checklist_results(
                id INTEGER PRIMARY KEY,
                pack_id INTEGER NOT NULL,
                check_code TEXT NOT NULL,
                check_title TEXT NOT NULL,
                passed INTEGER NOT NULL,
                detail TEXT,
                created_at TEXT NOT NULL,
                item_state TEXT NOT NULL DEFAULT 'passed',
                resolved INTEGER NOT NULL DEFAULT 0,
                resolve_note TEXT,
                resolved_at TEXT,
                FOREIGN KEY(pack_id) REFERENCES evidence_packs(id)
            );
            -- 清单项处置留痕：复检 / 豁免
            CREATE TABLE IF NOT EXISTS checklist_actions(
                id INTEGER PRIMARY KEY,
                pack_id INTEGER NOT NULL,
                check_code TEXT NOT NULL,
                action TEXT NOT NULL,
                reason TEXT,
                result_state TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(pack_id) REFERENCES evidence_packs(id)
            );
            -- 放行决策（decision 同源拒绝原因，action 区分首轮/复检后放行）
            CREATE TABLE IF NOT EXISTS release_decisions(
                id INTEGER PRIMARY KEY,
                pack_id INTEGER NOT NULL,
                decision TEXT NOT NULL,
                reason TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(pack_id) REFERENCES evidence_packs(id)
            );
            -- 设置
            CREATE TABLE IF NOT EXISTS settings(
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            -- 影子试跑（空挂，后题用）
            CREATE TABLE IF NOT EXISTS shadow_runs(
                id INTEGER PRIMARY KEY,
                pack_id INTEGER,
                rule_version_id INTEGER,
                status TEXT,
                note TEXT,
                created_at TEXT
            );
            -- 规则版本（空挂，后题用）
            CREATE TABLE IF NOT EXISTS rule_versions(
                id INTEGER PRIMARY KEY,
                code TEXT,
                title TEXT,
                payload_json TEXT,
                active INTEGER DEFAULT 0,
                created_at TEXT
            );
            """
        )
        # 旧库迁移：补齐清单项命运字段（CREATE TABLE IF NOT EXISTS 不会改已存在的表）
        _ensure_column(conn, "checklist_results", "item_state", "item_state TEXT NOT NULL DEFAULT 'passed'")
        _ensure_column(conn, "checklist_results", "resolved", "resolved INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "checklist_results", "resolve_note", "resolve_note TEXT")
        _ensure_column(conn, "checklist_results", "resolved_at", "resolved_at TEXT")
        if conn.execute("SELECT COUNT(*) c FROM settings").fetchone()["c"] == 0:
            now = _now()
            for s in SUBJECT_SEEDS:
                conn.execute(
                    """
                    INSERT INTO subjects(
                        code, title, domain, fixture, sample_rate_default, enabled, created_at
                    ) VALUES (?,?,?,?,?,?,?)
                    """,
                    (
                        s["code"],
                        s["title"],
                        s["domain"],
                        s["fixture"],
                        s["sample_rate_default"],
                        s["enabled"],
                        now,
                    ),
                )
            conn.execute(
                "INSERT INTO settings(key, value) VALUES (?, ?)",
                ("default_sample_rate", "0.3"),
            )
            # 部分失败命运：互斥单选。reject_all=整包驳回口径；suspend_items=分项挂起
            conn.execute(
                "INSERT INTO settings(key, value) VALUES (?, ?)",
                ("partial_fail_policy", "reject_all"),
            )
            conn.execute(
                "INSERT INTO settings(key, value) VALUES (?, ?)",
                ("sample_mode", "seeded"),
            )
        else:
            # 旧库迁移：布尔开关升级为互斥策略；原开关为 0（允许带未通过放行）也统一收口为互斥策略
            row = conn.execute(
                "SELECT value FROM settings WHERE key='partial_fail_policy'"
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO settings(key, value) VALUES (?, ?)",
                    ("partial_fail_policy", "reject_all"),
                )
            conn.execute("DELETE FROM settings WHERE key='require_checklist_pass'")
        conn.commit()
    finally:
        conn.close()
