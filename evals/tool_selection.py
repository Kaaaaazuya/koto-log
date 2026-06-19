"""T1.7: ツール選択の評価（独立スクリプト）。

代表入力を実 LLM に複数回投げ、「期待ツール + 必須引数」を選べるかの正答率を出す。
実 LLM は非決定論的で遅いため、決定論的な pytest とは分離している。

実行:
    uv run python evals/tool_selection.py            # 既定 3 回/シナリオ
    uv run python evals/tool_selection.py --runs 5
    KOTOLOG_MODEL=claude-3-5-haiku-latest uv run python evals/tool_selection.py
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass

from kotolog.agent.loop import SYSTEM_PROMPT, _extract_calls
from kotolog.config import load_config
from kotolog.llm.client import LLMClient
from kotolog.tools.definitions import TOOLS


@dataclass
class Scenario:
    input: str
    tool: str
    must: dict  # 一致を要求する構造的引数のみ


SCENARIOS = [
    # --- 保存（T1.1） ---
    Scenario("3時にミルク120ml飲んだ", "save_record", {"type": "feeding"}),
    Scenario("9時に寝た", "save_record", {"type": "sleep"}),
    Scenario("さっきうんちした", "save_record", {"type": "diaper"}),
    # --- 保存（T1.9: sub_type付き） ---
    Scenario("おっぱいあげた", "save_record", {"type": "feeding"}),
    # --- 集計（T1.1/T1.8） ---
    Scenario("今日は何回ミルク飲んだ？", "query_records", {"period": "today", "type": "feeding"}),
    Scenario("昨日の睡眠はどうだった？", "query_records", {"period": "yesterday", "type": "sleep"}),
    # --- 日次・週次サマリ（T1.10） ---
    Scenario("今日のまとめは？", "query_records", {"period": "today"}),
    Scenario("今週の授乳をまとめて", "query_records", {"period": "last_7days", "type": "feeding"}),
    # --- 前回いつ（T1.11: latest） ---
    Scenario("前回の授乳はいつ？", "query_records", {"period": "latest", "type": "feeding"}),
    Scenario("最後におむつ替えたのいつ？", "query_records", {"period": "latest", "type": "diaper"}),
    # --- 修正・取消（T1.6） ---
    Scenario("さっきのなし", "update_or_delete_record", {"action": "delete"}),
    Scenario("さっきのを150に直して", "update_or_delete_record", {"action": "update"}),
]


def _args_match(got: dict, must: dict) -> bool:
    for key, want in must.items():
        if str(got.get(key)).lower() != str(want).lower():
            return False
    return True


def _run_once(client: LLMClient, sc: Scenario) -> tuple[bool, str]:
    """1 回実行し (pass?, 説明) を返す。"""
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": sc.input},
    ]
    try:
        resp = client.complete(messages, tools=TOOLS)
    except Exception as e:  # noqa: BLE001 - 崩れた応答での例外も失敗として計上
        return False, f"error:{type(e).__name__}"
    calls = _extract_calls(resp.choices[0].message)
    if not calls:
        return False, "no_tool"
    call = calls[0]
    if call.name != sc.tool:
        return False, f"tool={call.name}"
    if not _args_match(call.args, sc.must):
        return False, f"args={call.args}"
    return True, "ok"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=3, help="シナリオごとの試行回数")
    args = parser.parse_args()

    config = load_config()
    client = LLMClient(config)
    print(f"model = {config.model} / runs = {args.runs}\n")

    total_pass = 0
    total = 0
    for sc in SCENARIOS:
        passes = 0
        reasons: list[str] = []
        for _ in range(args.runs):
            ok, why = _run_once(client, sc)
            passes += int(ok)
            if not ok:
                reasons.append(why)
        total_pass += passes
        total += args.runs
        mark = "OK " if passes == args.runs else "   "
        detail = "" if not reasons else "  ← " + ", ".join(reasons[:3])
        print(f"{mark}{passes}/{args.runs}  {sc.input}  (期待:{sc.tool}){detail}")

    rate = total_pass / total * 100 if total else 0
    print(f"\n全体正答率: {total_pass}/{total} = {rate:.0f}%")


if __name__ == "__main__":
    main()
