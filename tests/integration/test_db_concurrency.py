"""DB接続の並行アクセス制御のテスト（Issue #33）。

`conn` フィクスチャ（初期化済みインメモリDB, check_same_thread=False）を
複数スレッドから共有し、read-then-write な複合操作が直列化されることを検証する。
"""

import threading

from kotolog.db import crud


def test_ensure_child_concurrent_calls_do_not_create_duplicates(conn):
    """同名の子を並行して ensure_child しても子が1件しか作られない。"""
    barrier = threading.Barrier(8)
    results: list[int] = []
    lock = threading.Lock()

    def worker():
        barrier.wait()
        cid = crud.ensure_child(conn, "たろう")
        with lock:
            results.append(cid)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == 8
    assert len(set(results)) == 1  # 全スレッドが同じ id を得る
    rows = conn.execute("SELECT id FROM children WHERE name_alias = ?", ("たろう",)).fetchall()
    assert len(rows) == 1


def test_increment_rate_limit_concurrent_calls_do_not_lose_updates(conn):
    """同一ユーザーへの並行インクリメントでカウントが失われない。"""
    barrier = threading.Barrier(10)

    def worker():
        barrier.wait()
        crud.increment_rate_limit(conn, "U123", "message")

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    row = conn.execute(
        "SELECT message_count FROM user_rate_limits WHERE line_user_id = ?", ("U123",)
    ).fetchone()
    assert row["message_count"] == 10


def test_get_or_create_default_child_concurrent_calls_create_single_child(conn):
    """子が皆無の状態から並行して get_or_create_default_child しても1人しか作られない。"""
    barrier = threading.Barrier(6)
    results: list[int] = []
    lock = threading.Lock()

    def worker():
        barrier.wait()
        cid = crud.get_or_create_default_child(conn, "baby")
        with lock:
            results.append(cid)

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(set(results)) == 1
    assert len(crud.list_children(conn)) == 1


def test_unwrapped_single_statement_calls_do_not_corrupt_connection(conn, child_id):
    """個々の execute() 呼び出しも自動ロックされ、並行実行で例外を起こさない。

    conn.lock を明示的に obtain しない crud 関数（insert_record など）でも、
    _KotoSqliteConnection.execute() 自体が自動ロックするため安全であることを検証する
    （Issue #33 レビュー指摘: execute 単体の並行呼び出しも保護する必要がある）。
    """
    errors: list[Exception] = []

    def writer(i: int):
        try:
            crud.insert_record(
                conn,
                child_id=child_id,
                type="feeding",
                started_at=f"2026-01-01T00:{i:02d}:00+09:00",
            )
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    def reader():
        try:
            crud.list_children(conn)
            crud.get_default_child_id(conn)
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(20)]
    threads += [threading.Thread(target=reader) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    rows = conn.execute("SELECT COUNT(*) AS c FROM records WHERE child_id = ?", (child_id,)).fetchone()
    assert rows["c"] == 20
