"""CRUD 関数（T1.1）。

時刻はすべて JST 絶対時刻の ISO8601 文字列で受け渡す（正規化は utils.timeparse が担当）。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

from kotolog.db.connection import KotoConnection
from kotolog.db.migrations import migrate

JST = timezone(timedelta(hours=9))

# update_record で書き換えを許可するカラム（任意キーの混入を防ぐ）
_UPDATABLE = {"type", "sub_type", "amount", "unit", "started_at", "ended_at", "note"}


def _now() -> str:
    return datetime.now(JST).isoformat()


def init_db(conn: KotoConnection) -> None:
    """スキーマを適用する（冪等）。マイグレーション基盤経由で前進適用する（P9.0）。"""
    migrate(conn)


def ensure_child(conn: KotoConnection, name_alias: str) -> int:
    """別名の子を取得（無ければ作成）して id を返す。

    SELECT→INSERT の複合操作のため、並行呼び出しでの重複作成を防ぐべく
    conn.lock で直列化する（Issue #33）。
    """
    with conn.lock:
        row = conn.execute("SELECT id FROM children WHERE name_alias = ?", (name_alias,)).fetchone()
        if row is not None:
            return row["id"]
        cur = conn.execute("INSERT INTO children (name_alias) VALUES (?)", (name_alias,))
        conn.commit()
        return cur.lastrowid


# --- 複数子・既定児（P9.1 / ADR-0006） ------------------------------------


def create_child(conn: KotoConnection, name_alias: str, birthday: str | None = None) -> int:
    """子を新規作成して id を返す。最初の子なら既定児に自動設定する。

    既定児の有無チェック→設定が複合操作のため conn.lock で直列化する（Issue #33）。
    """
    with conn.lock:
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


def list_children(conn: KotoConnection) -> list[sqlite3.Row]:
    """全ての子を birthday 昇順（NULL は末尾）、同一/NULL は id 昇順で返す。"""
    return conn.execute("SELECT * FROM children ORDER BY (birthday IS NULL), birthday ASC, id ASC").fetchall()


def get_default_child_id(conn: KotoConnection) -> int | None:
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


def set_default_child_id(conn: KotoConnection, child_id: int) -> None:
    set_setting(conn, "default_child_id", str(child_id))


def resolve_child_id(
    conn: KotoConnection,
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


def get_or_create_default_child(conn: KotoConnection, seed_name: str) -> int:
    """既定児 id を解決する。無ければ既存の先頭児を既定化、子が皆無なら seed 児を作成する。

    起動時の結線で使う（KOTOLOG_DEFAULT_CHILD への実行時依存を撤廃）。冪等。
    get_default_child_id が存在確認済みのため重複チェック不要。
    check→act の複合操作のため conn.lock で直列化する（Issue #33）。
    """
    with conn.lock:
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


def upsert_user(conn: KotoConnection, line_user_id: str, nickname: str | None = None) -> None:
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


def set_user_nickname(conn: KotoConnection, line_user_id: str, nickname: str | None) -> None:
    """ニックネームを明示的に設定する（None でクリア）。管理画面から使用。"""
    conn.execute(
        "UPDATE users SET nickname = ?, updated_at = ? WHERE line_user_id = ?",
        (nickname, _now(), line_user_id),
    )
    conn.commit()


def list_users(conn: KotoConnection) -> list[sqlite3.Row]:
    """全ユーザーを作成日時昇順で返す。"""
    return conn.execute("SELECT * FROM users ORDER BY created_at ASC").fetchall()


def get_notify_users(conn: KotoConnection) -> list[sqlite3.Row]:
    """notify_enabled=True のユーザーを返す。"""
    return conn.execute("SELECT * FROM users WHERE notify_enabled = 1 ORDER BY created_at ASC").fetchall()


def update_user_notify(conn: KotoConnection, line_user_id: str, notify_enabled: bool) -> None:
    conn.execute(
        "UPDATE users SET notify_enabled = ?, updated_at = ? WHERE line_user_id = ?",
        (1 if notify_enabled else 0, _now(), line_user_id),
    )
    conn.commit()


def delete_user(conn: KotoConnection, line_user_id: str) -> None:
    conn.execute("DELETE FROM users WHERE line_user_id = ?", (line_user_id,))
    conn.commit()


def get_child_name(conn: KotoConnection, child_id: int) -> str | None:
    """子の name_alias を返す。見つからなければ None。"""
    row = conn.execute("SELECT name_alias FROM children WHERE id = ?", (child_id,)).fetchone()
    return row["name_alias"] if row else None


def set_user_current_child(conn: KotoConnection, line_user_id: str, child_id: int | None) -> None:
    conn.execute(
        "UPDATE users SET current_child_id = ?, updated_at = ? WHERE line_user_id = ?",
        (child_id, _now(), line_user_id),
    )
    conn.commit()


# --- ユーザー承認管理（Issue #29）-----------------------------------------


def approve_user(conn: KotoConnection, line_user_id: str) -> None:
    """ユーザーを承認する。"""
    conn.execute(
        "UPDATE users SET approved = 1, updated_at = ? WHERE line_user_id = ?",
        (_now(), line_user_id),
    )
    conn.commit()


def reject_user(conn: KotoConnection, line_user_id: str) -> None:
    """ユーザーを却下（削除）する。"""
    conn.execute("DELETE FROM users WHERE line_user_id = ?", (line_user_id,))
    conn.commit()


def is_user_approved(conn: KotoConnection, line_user_id: str) -> bool:
    """ユーザーが承認されているかチェックする。見つからない場合は False。"""
    row = conn.execute("SELECT approved FROM users WHERE line_user_id = ?", (line_user_id,)).fetchone()
    return row is not None and row["approved"] == 1


def get_user(conn: KotoConnection, line_user_id: str) -> sqlite3.Row | None:
    """承認済みのユーザーを取得する。未承認または見つからない場合は None。"""
    return conn.execute(
        "SELECT * FROM users WHERE line_user_id = ? AND approved = 1", (line_user_id,)
    ).fetchone()


def list_pending_approvals(conn: KotoConnection) -> list[sqlite3.Row]:
    """未承認（approved=0）のユーザーを返す。作成日時昇順。"""
    return conn.execute("SELECT * FROM users WHERE approved = 0 ORDER BY created_at ASC").fetchall()


def insert_record(
    conn: KotoConnection,
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


def get_record(conn: KotoConnection, record_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM records WHERE id = ?", (record_id,)).fetchone()


def get_last_record(conn: KotoConnection, child_id: int, type: str | None = None) -> sqlite3.Row | None:
    """最も新しい started_at の記録（「さっき」「前回の◯◯」の対象）。"""
    sql = ["SELECT * FROM records", "WHERE child_id = ?"]
    params: list = [child_id]
    if type is not None:
        sql.append("AND type = ?")
        params.append(type)
    sql.append("ORDER BY started_at DESC, id DESC LIMIT 1")
    return conn.execute("\n".join(sql), params).fetchone()


def query_records(
    conn: KotoConnection,
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


def update_record(conn: KotoConnection, record_id: int, new_values: dict) -> bool:
    """指定カラムを更新する。許可外キーは無視。更新があれば True。"""
    fields = {k: v for k, v in new_values.items() if k in _UPDATABLE}
    if not fields:
        return False
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    params = [*fields.values(), _now(), record_id]
    # set_clause の列名は _UPDATABLE 許可リスト由来、値はすべて ? バインド（外部入力は含まない）
    cur = conn.execute(f"UPDATE records SET {set_clause}, updated_at = ? WHERE id = ?", params)  # nosec B608
    conn.commit()
    return cur.rowcount > 0


def delete_record(conn: KotoConnection, record_id: int) -> bool:
    cur = conn.execute("DELETE FROM records WHERE id = ?", (record_id,))
    conn.commit()
    return cur.rowcount > 0


# --- 設定（key-value） -------------------------------------------------------


def get_setting(conn: KotoConnection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_setting(conn: KotoConnection, key: str, value: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
        (key, value),
    )
    conn.commit()


# --- 会話文脈（Issue #38） ---------------------------------------------------


def get_session_context(conn: KotoConnection, line_user_id: str) -> list[dict] | None:
    """直近の会話文脈（Agent.handle の history 用）を取得する。無ければ None。"""
    row = conn.execute(
        "SELECT recent_context FROM sessions WHERE line_user_id = ?", (line_user_id,)
    ).fetchone()
    if row is None or not row["recent_context"]:
        return None
    try:
        data = json.loads(row["recent_context"])
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, list) else None


def set_session_context(conn: KotoConnection, line_user_id: str, context: list[dict]) -> None:
    """直近の会話文脈を保存する（既存レコードは上書き）。"""
    now = _now()
    conn.execute(
        """
        INSERT INTO sessions (line_user_id, recent_context, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(line_user_id) DO UPDATE SET
            recent_context = excluded.recent_context,
            updated_at = excluded.updated_at
        """,
        (line_user_id, json.dumps(context, ensure_ascii=False), now),
    )
    conn.commit()


# --- 冪等化（T2.2） ---------------------------------------------------------


def is_processed(conn: KotoConnection, event_id: str) -> bool:
    """LINE webhook の event_id が処理済みかどうかを返す。"""
    row = conn.execute("SELECT 1 FROM processed_events WHERE event_id = ?", (event_id,)).fetchone()
    return row is not None


def mark_processed(conn: KotoConnection, event_id: str) -> None:
    """event_id を処理済みとして記録する（重複は無視）。"""
    conn.execute(
        "INSERT OR IGNORE INTO processed_events (event_id, created_at) VALUES (?, ?)",
        (event_id, _now()),
    )
    conn.commit()


def cleanup_old_processed_events(conn: KotoConnection, older_than_days: int = 7) -> int:
    """指定日数より古い processed_events を削除する（Issue #47）。削除件数を返す。

    LINE webhook の再送は短時間しか起こらないため、この期間を過ぎたレコードは
    冪等化の役目を終えており安全に削除できる。
    """
    if older_than_days < 1:
        raise ValueError("older_than_days must be at least 1")
    threshold = (datetime.now(JST) - timedelta(days=older_than_days)).isoformat()
    cur = conn.execute("DELETE FROM processed_events WHERE created_at < ?", (threshold,))
    conn.commit()
    return cur.rowcount


# --- レート制限・コスト管理（Issue #37） -------------------------------------------


def check_rate_limit(
    conn: KotoConnection,
    user_id: str,
    limit_type: str,
    max_count: int,
    window_hours: int = 1,
) -> bool:
    """レート制限チェック。制限内なら True、超過なら False を返す（読み取り専用）。

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

    # レコードがない、またはウィンドウ外の場合は制限内（カウント0）とみなす
    if row is None or (row["window_start"] < window_start):
        return True

    count_key = "message_count" if limit_type == "message" else "llm_call_count"
    return row[count_key] < max_count


def increment_rate_limit(conn: KotoConnection, user_id: str, limit_type: str, window_hours: int = 1) -> None:
    """レート制限カウンターをインクリメント。ウィンドウが切れている場合はリセットする。

    ウィンドウ境界での SELECT→INSERT/UPDATE 分岐が複合操作のため、
    並行呼び出しでの増分ロストを防ぐべく conn.lock で直列化する（Issue #33）。
    """
    if not user_id:
        return

    now = datetime.now(JST)
    now_str = now.isoformat()
    window_start_limit = (now - timedelta(hours=window_hours)).isoformat()
    msg_inc = 1 if limit_type == "message" else 0
    llm_inc = 1 if limit_type == "llm_call" else 0

    with conn.lock:
        row = conn.execute(
            "SELECT window_start FROM user_rate_limits WHERE line_user_id = ?",
            (user_id,),
        ).fetchone()

        if row is None or (row["window_start"] < window_start_limit):
            # 新規ウィンドウ作成、または期限切れウィンドウの上書き
            conn.execute(
                """
                INSERT OR REPLACE INTO user_rate_limits
                (line_user_id, message_count, llm_call_count, window_start, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (user_id, msg_inc, llm_inc, now_str, now_str),
            )
        else:
            # 既存ウィンドウ内でのインクリメント
            conn.execute(
                """
                UPDATE user_rate_limits SET
                    message_count = message_count + ?,
                    llm_call_count = llm_call_count + ?,
                    updated_at = ?
                WHERE line_user_id = ?
                """,
                (msg_inc, llm_inc, now_str, user_id),
            )
        conn.commit()


# --- コスト計測（Issue #68 / ADR-0002 DB永続化） -----------------------------


def monthly_usage_summary(conn: KotoConnection, year_month: str) -> dict:
    """指定月（"YYYY-MM"）の usage_log を集計する。世帯全体（ユーザー別内訳なし）。

    - `ts` は JST ISO8601 文字列（例: "2026-07-10T09:00:00+09:00"）。先頭7文字が
      year_month に一致する行を対象にする。
    - `cost_usd` は NULL（単価未取得）を 0 として合算する（[[project-pii-check]] とは
      無関係。ADR-0002 のリスク節：単価表未対応モデルは cost_usd=NULL になりうる）。
    - `by_operation` / `by_model` は「extract/loop/push」「モデル文字列」ごとの内訳。

    `year_month` は厳密に "YYYY-MM" 形式のみ許可する（LIKE の先頭一致に使うため、
    "2026" や "%" を含む値だと意図しない行まで拾ってしまう）。
    """
    if (
        len(year_month) != 7
        or year_month[4] != "-"
        or not year_month[:4].isdigit()
        or not year_month[5:].isdigit()
    ):
        raise ValueError("year_month must be in YYYY-MM format")

    prefix = f"{year_month}%"

    totals = conn.execute(
        """
        SELECT
            COUNT(*) AS call_count,
            COALESCE(SUM(input_tokens), 0) AS total_input_tokens,
            COALESCE(SUM(output_tokens), 0) AS total_output_tokens,
            COALESCE(SUM(total_tokens), 0) AS total_tokens,
            COALESCE(SUM(cost_usd), 0) AS total_cost_usd
        FROM usage_log
        WHERE ts LIKE ?
        """,
        (prefix,),
    ).fetchone()

    def _breakdown(group_col: str) -> dict:
        rows = conn.execute(
            f"""
            SELECT
                {group_col} AS key,
                COUNT(*) AS calls,
                COALESCE(SUM(input_tokens), 0) AS input_tokens,
                COALESCE(SUM(output_tokens), 0) AS output_tokens,
                COALESCE(SUM(cost_usd), 0) AS cost_usd
            FROM usage_log
            WHERE ts LIKE ?
            GROUP BY {group_col}
            """,  # nosec B608 - group_col は本関数内の固定リテラルのみ渡す
            (prefix,),
        ).fetchall()
        return {
            row["key"]: {
                "calls": row["calls"],
                "input_tokens": row["input_tokens"],
                "output_tokens": row["output_tokens"],
                "cost_usd": row["cost_usd"],
            }
            for row in rows
            if row["key"] is not None
        }

    return {
        "call_count": totals["call_count"],
        "total_input_tokens": totals["total_input_tokens"],
        "total_output_tokens": totals["total_output_tokens"],
        "total_tokens": totals["total_tokens"],
        "total_cost_usd": totals["total_cost_usd"],
        "by_operation": _breakdown("operation"),
        "by_model": _breakdown("model"),
    }
