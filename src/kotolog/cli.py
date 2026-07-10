"""対話CLI（T1.6）。

手動テスト用のエントリ。config から組み立てた agent と標準入力で往復する。
"""

from __future__ import annotations

from kotolog.agent.loop import Agent
from kotolog.config import load_config
from kotolog.db import crud
from kotolog.db.connection import connect
from kotolog.llm.client import LLMClient
from kotolog.obs.usage import sink_from_config


def build_agent(config=None) -> Agent:
    """設定から DB・LLM を結線した Agent を返す。"""
    config = config or load_config()
    conn = connect(config.db_url, auth_token=config.turso_auth_token)
    crud.init_db(conn)
    crud.get_or_create_default_child(conn, config.default_child)
    client = LLMClient(config, sink=sink_from_config(config, conn=conn))
    return Agent(client=client, conn=conn, config=config)


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
