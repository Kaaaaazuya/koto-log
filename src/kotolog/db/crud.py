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


# --- 複数子・既定児（P9.1 / ADR-0006） ------------------------------------


def create_child(conn: sqlite3.Connection, name_alias: str, birthday: str | None = None) -> int:
    """子を新規作成して id を返す。最初の子なら既定児に自動設定する。"""
    cur = conn.execute(
        "INSERT INTO children (name_alias, birthday) VALUES (?, ?)",
        (name_alias, birthday),
    )
    child_id = cur.lastrowid
    if get_default_child_id(conn) is None:
        set_default_child_id(conn, child_id)  # 内部 commit で INSERT + settings を一括コミット
    else:
        conn.commit()
    return child_id


def list_children(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """全ての子を birthday 昇順（NULL は末尾）、同一/NULL は id 昇順で返す。"""
    return conn.execute("SELECT * FROM children ORDER BY (birthday IS NULL), birthday ASC, id ASC").fetchall()


def get_default_child_id(conn: sqlite3.Connection) -> int | None:
    """世帯の既定児 id（settings.default_child_id）。未設定・不正値・存在しない子なら None。"""
    value = get_setting(conn, "default_child_id")
    if value is None:
        return None
    try:
        child_id = int(value)
    except ValueError:
        return None
    row = conn.execute("SELECT 1 FROM children WHERE id = ?", (child_id,)).fetchone()
    return child_id if row is not None else None


def set_default_child_id(conn: sqlite3.Connection, child_id: int) -> None:
    set_setting(conn, "default_child_id", str(child_id))


def resolve_child_id(
    conn: sqlite3.Connection,
    *,
    line_user_id: str | None = None,
    child_name_hint: str | None = None,
) -> int:
    """リクエストごとに対象児 ID を解決する（ADR-0006 優先順位）。

    名前明示 → users.current_child_id → default_child_id → 単一児 の順で解決する。
    解決できない場合は RuntimeError。
    """
    if child_name_hint is not None:
        row = conn.execute("SELECT id FROM children WHERE name_alias = ?", (child_name_hint,)).fetchone()
        if row is not None:
            return row["id"]

    if line_user_id is not None:
        row = conn.execute(
            "SELECT current_child_id FROM users WHERE line_user_id = ?", (line_user_id,)
        ).fetchone()
        if row is not None and row["current_child_id"] is not None:
            exists = conn.execute(
                "SELECT 1 FROM children WHERE id = ?", (row["current_child_id"],)
            ).fetchone()
            if exists:
                return row["current_child_id"]

    did = get_default_child_id(conn)
    if did is not None:
        return did

    children = list_children(conn)
    if len(children) == 1:
        return children[0]["id"]

    raise RuntimeError("対象児を解決できませんでした。子を登録するか既定児を設定してください。")


def get_or_create_default_child(conn: sqlite3.Connection, seed_name: str) -> int:
    """既定児 id を解決する。無ければ既存の先頭児を既定化、子が皆無なら seed 児を作成する。

    起動時の結線で使う（KOTOLOG_DEFAULT_CHILD への実行時依存を撤廃）。冪等。
    get_default_child_id が存在確認済みのため重複チェック不要。
    """
    did = get_default_child_id(conn)
    if did is not None:
        return did
    children = list_children(conn)
    if children:
        cid = children[0]["id"]
        set_default_child_id(conn, cid)
        return cid
    return create_child(conn, seed_name)


# --- ユーザー管理（P9.3 / ADR-0006） ----------------------------------------


def upsert_user(conn: sqlite3.Connection, line_user_id: str, nickname: str | None = None) -> None:
    """LINE ユーザーを登録または更新する。

    INSERT OR IGNORE で競合を原子的に回避。nickname=None は「変更しない」を意味する。
    新規ユーザーは approved=0 で登録される（Issue #29）。
    """
    now = _now()
    conn.execute(
        "INSERT OR IGNORE INTO users (line_user_id, notify_enabled, approved, created_at, updated_at)"
        " VALUES (?, 1, 0, ?, ?)",
        (line_user_id, now, now),
    )
    if nickname is not None:
        conn.execute(
            "UPDATE users SET nickname = ?, updated_at = ? WHERE line_user_id = ?",
            (nickname, now, line_user_id),
        )
    conn.commit()


def set_user_nickname(conn: sqlite3.Connection, line_user_id: str, nickname: str | None) -> None:
    """ニックネームを明示的に設定する（None でクリア）。管理画面から使用。"""
    conn.execute(
        "UPDATE users SET nickname = ?, updated_at = ? WHERE line_user_id = ?",
        (nickname, _now(), line_user_id),
    )
    conn.commit()


def list_users(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """全ユーザーを作成日時昇順で返す。"""
    return conn.execute("SELECT * FROM users ORDER BY created_at ASC").fetchall()


def get_notify_users(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """notify_enabled=True のユーザーを返す。"""
    return conn.execute("SELECT * FROM users WHERE notify_enabled = 1 ORDER BY created_at ASC").fetchall()


def update_user_notify(conn: sqlite3.Connection, line_user_id: str, notify_enabled: bool) -> None:
    conn.execute(
        "UPDATE users SET notify_enabled = ?, updated_at = ? WHERE line_user_id = ?",
        (1 if notify_enabled else 0, _now(), line_user_id),
    )
    conn.commit()


def delete_user(conn: sqlite3.Connection, line_user_id: str) -> None:
    conn.execute("DELETE FROM users WHERE line_user_id = ?", (line_user_id,))
    conn.commit()


def get_child_name(conn: sqlite3.Connection, child_id: int) -> str | None:
    """子の name_alias を返す。見つからなければ None。"""
    row = conn.execute("SELECT name_alias FROM children WHERE id = ?", (child_id,)).fetchone()
    return row["name_alias"] if row else None


def set_user_current_child(conn: sqlite3.Connection, line_user_id: str, child_id: int | None) -> None:
    conn.execute(
        "UPDATE users SET current_child_id = ?, updated_at = ? WHERE line_user_id = ?",
        (child_id, _now(), line_user_id),
    )
    conn.commit()


# --- ユーザー承認管理（Issue #29）-----------------------------------------


def approve_user(conn: sqlite3.Connection, line_user_id: str) -> None:
    """ユーザーを承認する。"""
    conn.execute(
        "UPDATE users SET approved = 1, updated_at = ? WHERE line_user_id = ?",
        (_now(), line_user_id),
    )
    conn.commit()


def reject_user(conn: sqlite3.Connection, line_user_id: str) -> None:
    """ユーザーを却下（削除）する。"""
    conn.execute("DELETE FROM users WHERE line_user_id = ?", (line_user_id,))
    conn.commit()


def is_user_approved(conn: sqlite3.Connection, line_user_id: str) -> bool:
    """ユーザーが承認されているかチェックする。見つからない場合は False。"""
    row = conn.execute("SELECT approved FROM users WHERE line_user_id = ?", (line_user_id,)).fetchone()
    return row is not None and row["approved"] == 1


def get_user(conn: sqlite3.Connection, line_user_id: str) -> sqlite3.Row | None:
    """承認済みのユーザーを取得する。未承認または見つからない場合は None。"""
    return conn.execute(
        "SELECT * FROM users WHERE line_user_id = ? AND approved = 1", (line_user_id,)
    ).fetchone()


def list_pending_approvals(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """未承認（approved=0）のユーザーを返す。作成日時昇順。"""
    return conn.execute("SELECT * FROM users WHERE approved = 0 ORDER BY created_at ASC").fetchall()


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


# --- レート制限・コスト管理（Issue #37） -------------------------------------------


def check_rate_limit(
    conn: sqlite3.Connection,
    user_id: str,
    limit_type: str,
    max_count: int,
    window_hours: int = 1,
) -> bool:
    """レート制限チェック。制限内なら True、超過なら False を返す。

    limit_type: "message" または "llm_call"
    window_hours: 制限をリセットする時間ウィンドウ（時間）
    """
    if not user_id:
        return True

    now = datetime.now(JST)
    window_start = (now - timedelta(hours=window_hours)).isoformat()

    row = conn.execute(
        "SELECT * FROM user_rate_limits WHERE line_user_id = ?",
        (user_id,),
    ).fetchone()

    # ウィンドウ外のレコードは新規作成
    if row is None or (row["window_start"] < window_start):
        conn.execute(
            "INSERT OR REPLACE INTO user_rate_limits"
            "(line_user_id, message_count, llm_call_count, window_start, updated_at)"
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, 0, 0, now.isoformat(), _now()),
        )
        conn.commit()
        return True

    # 現在のカウントをチェック
    count_key = "message_count" if limit_type == "message" else "llm_call_count"
    current_count = row[count_key]

    return current_count < max_count


def increment_rate_limit(conn: sqlite3.Connection, user_id: str, limit_type: str) -> None:
    """レート制限カウンターをインクリメント。"""
    if not user_id:
        return

    now = datetime.now(JST)
    count_key = "message_count" if limit_type == "message" else "llm_call_count"
    other_key = (
        "llm_call_count" if limit_type == "message" else "message_count"
    )

    # 既存レコードをインクリメント、なければ新規作成
    row = conn.execute(
        f"SELECT {count_key} FROM user_rate_limits WHERE line_user_id = ?",
        (user_id,),
    ).fetchone()

    if row is not None:
        conn.execute(
            f"UPDATE user_rate_limits SET {count_key} = {count_key} + 1, updated_at = ?"
            "WHERE line_user_id = ?",
            (_now(), user_id),
        )
    else:
        values = {"message_count": 1 if limit_type == "message" else 0,
                  "llm_call_count": 1 if limit_type == "llm_call" else 0}
        conn.execute(
            "INSERT INTO user_rate_limits"
            "(line_user_id, message_count, llm_call_count, window_start, updated_at)"
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, values["message_count"], values["llm_call_count"],
             now.isoformat(), _now()),
        )

    conn.commit()
