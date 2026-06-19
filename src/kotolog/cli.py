"""対話CLI（T1.6）。

手動テスト用のエントリ。config から組み立てた agent と標準入力で往復する。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from kotolog.agent.loop import Agent
from kotolog.config import load_config
from kotolog.db import crud
from kotolog.db.connection import connect
from kotolog.llm.client import LLMClient
from kotolog.tools.executor import ToolExecutor

JST = timezone(timedelta(hours=9))


def build_agent(config=None) -> Agent:
    """設定から DB・executor・LLM を結線した Agent を返す。"""
    config = config or load_config()
    conn = connect(config.db_url)
    crud.init_db(conn)
    child_id = crud.ensure_child(conn, config.default_child)
    executor = ToolExecutor(conn=conn, child_id=child_id, now=datetime.now(JST))
    return Agent(client=LLMClient(config), executor=executor)


def main() -> None:
    config = load_config()
    agent = build_agent(config)
    print(f"koto-log CLI (model={config.model}) — 終了は Ctrl-D / 'quit'")
    while True:
        try:
            text = input("> ").strip()
        except EOFError:
            print()
            break
        if not text:
            continue
        if text in {"quit", "exit"}:
            break
        try:
            reply = agent.handle(text)
        except Exception as e:  # noqa: BLE001 - 対話を落とさない
            reply = f"[エラー] {type(e).__name__}: {e}"
        print(reply)


if __name__ == "__main__":
    main()
