"""マイグレーション基盤のテスト（結合・実DB / ADR-0006・P9.0）。

新規DBへのbaseline適用・既存DBのスタンプ（再実行しない）・冪等性を検証する。
"""

from __future__ import annotations

from kotolog.db import migrations as mig
from kotolog.db.connection import connect
from kotolog.db.migrations import BASELINE_VERSION, migrate


def _versions(conn) -> list[int]:
    rows = conn.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()
    return [r["version"] for r in rows]


def _table_exists(conn, name: str) -> bool:
    row = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name = ?", (name,)).fetchone()
    return row is not None


def test_fresh_db_applies_baseline(monkeypatch):
    """新規DBでは baseline が適用され、records/children と schema_migrations が作られる。"""
    monkeypatch.setattr(mig, "MIGRATIONS", [])  # baseline 挙動を分離して検証
    conn = connect(":memory:")
    migrate(conn)

    assert _versions(conn) == [BASELINE_VERSION]
    assert _table_exists(conn, "records")
    assert _table_exists(conn, "children")
    assert _table_exists(conn, "settings")
    conn.close()


def test_existing_db_stamps_baseline_without_rerun(monkeypatch):
    """既存DB（テーブルあり・schema_migrations なし）は baseline を再実行せずスタンプする。

    部分的に records だけ存在する状態で migrate すると、baseline がスタンプのみされ
    （= schema.sql は再実行されない）、settings 等が作られないことで検証する。
    """
    monkeypatch.setattr(mig, "MIGRATIONS", [])  # baseline 挙動を分離して検証
    conn = connect(":memory:")
    conn.execute("CREATE TABLE records (id INTEGER PRIMARY KEY)")
    conn.commit()

    migrate(conn)

    assert _versions(conn) == [BASELINE_VERSION]
    # baseline がスタンプのみなら schema.sql は走らず settings は作られない
    assert not _table_exists(conn, "settings")
    conn.close()


def test_migrate_is_idempotent(monkeypatch):
    """二重に migrate しても baseline は1回だけ記録される。"""
    monkeypatch.setattr(mig, "MIGRATIONS", [])  # baseline 挙動を分離して検証
    conn = connect(":memory:")
    migrate(conn)
    migrate(conn)

    assert _versions(conn) == [BASELINE_VERSION]
    conn.close()


def test_migrate_creates_schema_migrations_table():
    """schema_migrations テーブルが作成される。"""
    conn = connect(":memory:")
    migrate(conn)
    assert _table_exists(conn, "schema_migrations")
    conn.close()


def test_applies_pending_migrations_in_version_order(monkeypatch):
    """baseline 後の前進マイグレーションを version 昇順で適用・記録する。"""
    # リスト順は降順だが version 昇順で適用されること（依存関係の安全性）を確認
    monkeypatch.setattr(
        mig,
        "MIGRATIONS",
        [
            (3, "CREATE TABLE IF NOT EXISTS m3 (id INTEGER)"),
            (2, "CREATE TABLE IF NOT EXISTS m2 (id INTEGER)"),
        ],
    )
    conn = connect(":memory:")
    migrate(conn)

    assert _versions(conn) == [1, 2, 3]
    assert _table_exists(conn, "m2")
    assert _table_exists(conn, "m3")
    conn.close()


def test_pending_migration_applied_once(monkeypatch):
    """再 migrate で前進マイグレーションが二重適用されない。"""
    monkeypatch.setattr(mig, "MIGRATIONS", [(2, "CREATE TABLE IF NOT EXISTS m2 (id INTEGER)")])
    conn = connect(":memory:")
    migrate(conn)
    migrate(conn)

    assert _versions(conn) == [1, 2]
    conn.close()


def test_duplicate_versions_raise(monkeypatch):
    """MIGRATIONS に重複 version があれば ValueError（適用漏れ防止）。"""
    import pytest

    monkeypatch.setattr(
        mig,
        "MIGRATIONS",
        [(2, "CREATE TABLE IF NOT EXISTS a (id INTEGER)"), (2, "CREATE TABLE IF NOT EXISTS b (id INTEGER)")],
    )
    conn = connect(":memory:")
    with pytest.raises(ValueError):
        migrate(conn)
    conn.close()


def test_version_not_above_baseline_raises(monkeypatch):
    """BASELINE_VERSION 以下の version は ValueError（順序・管理の不整合防止）。"""
    import pytest

    monkeypatch.setattr(mig, "MIGRATIONS", [(1, "CREATE TABLE IF NOT EXISTS a (id INTEGER)")])
    conn = connect(":memory:")
    with pytest.raises(ValueError):
        migrate(conn)
    conn.close()
