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

from datetime import datetime, timedelta, timezone
from importlib import resources

JST = timezone(timedelta(hours=9))

BASELINE_VERSION = 1

# baseline より後（version >= 2）の前進マイグレーション。(version, SQL) の昇順。
# P9.1 以降でここに追加する（例: 0002 users / default_child_id）。
MIGRATIONS: list[tuple[int, str]] = []

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
    for version, sql in sorted(MIGRATIONS, key=lambda m: m[0]):
        if version not in applied:
            conn.executescript(sql)
            _record(conn, version)
            applied.add(version)
            conn.commit()
