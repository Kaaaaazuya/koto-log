"""CRUD 関数（T1.1）。

時刻はすべて JST 絶対時刻の ISO8601 文字列で受け渡す（正規化は utils.timeparse が担当）。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from kotolog.db.migrations import migrate

JST = timezone(timedelta(hours=9))

# update_record で書き換えを許可するカラム（任意キーの混入を防ぐ）
_UPDATABLE = {"type", "sub_type", "amount", "unit", "started_at", "ended_at", "note"}


def _now() -> str:
    return datetime.now(JST).isoformat()


def init_db(conn: sqlite3.Connection) -> None:
    """スキーマを適用する（冪等）。マイグレーション基盤経由で前進適用する（P9.0）。"""
    migrate(conn)


def ensure_child(conn: sqlite3.Connection, name_alias: str) -> int:
    """別名の子を取得（無ければ作成）して id を返す。"""
    row = conn.execute("SELECT id FROM children WHERE name_alias = ?", (name_alias,)).fetchone()
    if row is not None:
        return row["id"]
    cur = conn.execute("INSERT INTO children (name_alias) VALUES (?)", (name_alias,))
    conn.commit()
    return cur.lastrowid


def insert_record(
    conn: sqlite3.Connection,
    *,
    child_id: int,
    type: str,
    started_at: str,
    sub_type: str | None = None,
    amount: float | None = None,
    unit: str | None = None,
    ended_at: str | None = None,
    note: str | None = None,
) -> int:
    now = _now()
    cur = conn.execute(
        """
        INSERT INTO records
            (child_id, type, sub_type, amount, unit,
             started_at, ended_at, note, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (child_id, type, sub_type, amount, unit, started_at, ended_at, note, now, now),
    )
    conn.commit()
    return cur.lastrowid


def get_record(conn: sqlite3.Connection, record_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM records WHERE id = ?", (record_id,)).fetchone()


def get_last_record(conn: sqlite3.Connection, child_id: int, type: str | None = None) -> sqlite3.Row | None:
    """最も新しい started_at の記録（「さっき」「前回の◯◯」の対象）。"""
    sql = ["SELECT * FROM records", "WHERE child_id = ?"]
    params: list = [child_id]
    if type is not None:
        sql.append("AND type = ?")
        params.append(type)
    sql.append("ORDER BY started_at DESC, id DESC LIMIT 1")
    return conn.execute("\n".join(sql), params).fetchone()


def query_records(
    conn: sqlite3.Connection,
    *,
    child_id: int,
    start: str,
    end: str,
    type: str | None = None,
    sub_type: str | None = None,
) -> list[sqlite3.Row]:
    """期間 [start, end] と任意の種別・サブ種別で記録を取得する。"""
    sql = [
        "SELECT * FROM records",
        "WHERE child_id = ? AND started_at >= ? AND started_at <= ?",
    ]
    params: list = [child_id, start, end]
    if type is not None:
        sql.append("AND type = ?")
        params.append(type)
    if sub_type is not None:
        sql.append("AND sub_type = ?")
        params.append(sub_type)
    sql.append("ORDER BY started_at ASC, id ASC")
    return conn.execute("\n".join(sql), params).fetchall()


def update_record(conn: sqlite3.Connection, record_id: int, new_values: dict) -> bool:
    """指定カラムを更新する。許可外キーは無視。更新があれば True。"""
    fields = {k: v for k, v in new_values.items() if k in _UPDATABLE}
    if not fields:
        return False
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    params = [*fields.values(), _now(), record_id]
    cur = conn.execute(f"UPDATE records SET {set_clause}, updated_at = ? WHERE id = ?", params)
    conn.commit()
    return cur.rowcount > 0


def delete_record(conn: sqlite3.Connection, record_id: int) -> bool:
    cur = conn.execute("DELETE FROM records WHERE id = ?", (record_id,))
    conn.commit()
    return cur.rowcount > 0


# --- 設定（key-value） -------------------------------------------------------


def get_setting(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
        (key, value),
    )
    conn.commit()


# --- 冪等化（T2.2） ---------------------------------------------------------


def is_processed(conn: sqlite3.Connection, event_id: str) -> bool:
    """LINE webhook の event_id が処理済みかどうかを返す。"""
    row = conn.execute("SELECT 1 FROM processed_events WHERE event_id = ?", (event_id,)).fetchone()
    return row is not None


def mark_processed(conn: sqlite3.Connection, event_id: str) -> None:
    """event_id を処理済みとして記録する（重複は無視）。"""
    conn.execute(
        "INSERT OR IGNORE INTO processed_events (event_id, created_at) VALUES (?, ?)",
        (event_id, _now()),
    )
    conn.commit()
