"""スキーママイグレーション基盤（P9.0 / ADR-0006）。

`schema_migrations` テーブルで適用済みバージョンを管理し、起動時に未適用の
前進マイグレーションを順に適用する。baseline（version 1）は既存 schema.sql。

- 新規DB: baseline を実行してから記録する。
- 既存DB（テーブルあり・schema_migrations なし）: baseline は再実行せずスタンプのみ。
- 冪等: 適用済みバージョンは再適用しない。

sqlite3 / libsql の両接続に対応（どちらも execute/executescript/commit を持つ）。

注意（マイグレーション作成時の規約）:
- `executescript` は暗黙コミットを伴うため、複数文マイグレーションが途中で失敗すると
  一部だけ適用された状態になりうる。各マイグレーションは **`IF NOT EXISTS` / `IF EXISTS`
  等で冪等に書き**、途中失敗後の再実行に耐えるようにすること。
- バージョンは `MIGRATIONS` のリスト順ではなく version 昇順で適用する（順序は自己強制）。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from importlib import resources

JST = timezone(timedelta(hours=9))

BASELINE_VERSION = 1

# baseline より後（version >= 2）の前進マイグレーション。version 昇順で適用される。
# 各マイグレーションは冪等に書く（モジュール docstring の規約参照）。

# 0002: users テーブル（P9.1 / ADR-0006）。current_child_id は会話の現在の対象児。
_M0002_USERS = """
CREATE TABLE IF NOT EXISTS users (
    line_user_id     TEXT PRIMARY KEY,
    nickname         TEXT,
    notify_enabled   INTEGER NOT NULL DEFAULT 1,
    current_child_id INTEGER REFERENCES children(id) ON DELETE SET NULL,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);
"""


# 0003: children.sex カラム追加（P11 成長曲線で男女別標準値を参照するため）。
# ALTER TABLE ADD COLUMN は IF NOT EXISTS 不可のため、PRAGMA table_info で存在確認してから実行。
def _M0003_CHILD_SEX(conn) -> None:
    cursor = conn.execute("PRAGMA table_info(children)")
    columns = [row["name"] for row in cursor.fetchall()]
    if "sex" not in columns:
        conn.execute("ALTER TABLE children ADD COLUMN sex TEXT CHECK (sex IN ('male', 'female'))")


# 0004: users.approved カラム追加（Issue #29: 外部ユーザー承認フロー）。
# デフォルト値 0（False）で新規ユーザーは未承認から開始。既存ユーザーは承認済みと見なす。
def _M0004_USER_APPROVAL(conn) -> None:
    cursor = conn.execute("PRAGMA table_info(users)")
    columns = [row["name"] for row in cursor.fetchall()]
    if "approved" not in columns:
        conn.execute("ALTER TABLE users ADD COLUMN approved INTEGER NOT NULL DEFAULT 0")
    # 既存ユーザーは承認済みと見なす（新規外部ユーザーのみ未承認で登録）
    conn.execute("UPDATE users SET approved = 1 WHERE approved = 0")


MIGRATIONS: list[tuple[int, str | Callable]] = [
    (2, _M0002_USERS),
    (3, _M0003_CHILD_SEX),
    (4, _M0004_USER_APPROVAL),
]

# 既存DB判定に使うコアテーブル（どれかがあれば「既存DB」とみなす）
_CORE_TABLES = ("records", "children")


def _now() -> str:
    return datetime.now(JST).isoformat()


def _baseline_sql() -> str:
    return resources.files("kotolog.db").joinpath("schema.sql").read_text(encoding="utf-8")


def _ensure_migrations_table(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version    INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
        """
    )


def _applied_versions(conn) -> set[int]:
    rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
    return {r["version"] for r in rows}


def _record(conn, version: int) -> None:
    conn.execute(
        "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
        (version, _now()),
    )


def _has_core_tables(conn) -> bool:
    placeholders = ", ".join("?" for _ in _CORE_TABLES)
    row = conn.execute(
        f"SELECT name FROM sqlite_master WHERE type='table' AND name IN ({placeholders}) LIMIT 1",
        _CORE_TABLES,
    ).fetchone()
    return row is not None


def migrate(conn) -> None:
    """未適用のマイグレーションを順に適用する（前進のみ・冪等）。"""
    versions = [v for v, _ in MIGRATIONS]
    if len(versions) != len(set(versions)):
        raise ValueError(f"MIGRATIONS に重複した version があります: {sorted(versions)}")
    if any(v <= BASELINE_VERSION for v in versions):
        raise ValueError(
            f"MIGRATIONS の version は BASELINE_VERSION ({BASELINE_VERSION}) より大きい必要があります: "
            f"{sorted(versions)}"
        )

    _ensure_migrations_table(conn)
    applied = _applied_versions(conn)

    if BASELINE_VERSION not in applied:
        if _has_core_tables(conn):
            # 既存DB: baseline は再実行せずスタンプのみ。
            # （本番DBは旧 init_db で full schema を作成済み＝全テーブル存在のため安全。
            #  部分的な旧DBは存在しない前提。）
            _record(conn, BASELINE_VERSION)
        else:
            # 新規DB: baseline schema を適用してから記録
            conn.executescript(_baseline_sql())
            _record(conn, BASELINE_VERSION)
        applied.add(BASELINE_VERSION)
        conn.commit()

    # version 昇順で適用（MIGRATIONS のリスト順には依存しない）。各版を適用直後にコミットし、
    # 後続の失敗で既適用版の記録を失わないようにする。
    for version, migration in sorted(MIGRATIONS, key=lambda m: m[0]):
        if version not in applied:
            if callable(migration):
                migration(conn)
            else:
                conn.executescript(migration)
            _record(conn, version)
            applied.add(version)
            conn.commit()
