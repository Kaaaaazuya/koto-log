"""月次コストレポート CLI（Issue #68 / ADR-0002 DB永続化）。

`usage_log` テーブル（`KOTOLOG_USAGE_DB=1` で蓄積、マイグレーション0006）を集計し、
「今月の家族の育児記録にかかった API コスト」を世帯全体（ユーザー別内訳なし）で表示する。

実行例:
    uv run python -m kotolog.usage_report                # 当月（JST）
    uv run python -m kotolog.usage_report --month 2026-07
    uv run python -m kotolog.usage_report --month 2026-07 --db-url kotolog.db
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone

from kotolog.config import load_config
from kotolog.db import crud
from kotolog.db.connection import connect

JST = timezone(timedelta(hours=9))


def _current_month() -> str:
    return datetime.now(JST).strftime("%Y-%m")


def _format_breakdown(title: str, breakdown: dict) -> list[str]:
    if not breakdown:
        return []
    lines = ["", f"--- {title} ---"]
    for key, stats in sorted(breakdown.items()):
        lines.append(
            f"  {key}: {stats['calls']}回 / in={stats['input_tokens']:,} out={stats['output_tokens']:,}"
            f" / ${stats['cost_usd']:.4f}"
        )
    return lines


def format_summary(summary: dict, month: str) -> str:
    """`crud.monthly_usage_summary` の戻り値を人が読めるテキストに整形する（純粋関数）。"""
    lines = [
        f"=== コスト計測サマリー {month} ===",
        f"合計コスト: ${summary['total_cost_usd']:.4f}",
        f"呼び出し回数: {summary['call_count']}",
        f"入力トークン: {summary['total_input_tokens']:,}",
        f"出力トークン: {summary['total_output_tokens']:,}",
        f"合計トークン: {summary['total_tokens']:,}",
    ]
    lines += _format_breakdown("operation 別", summary["by_operation"])
    lines += _format_breakdown("model 別", summary["by_model"])

    if summary["call_count"] == 0:
        lines += ["", "(該当データなし。KOTOLOG_USAGE_DB=1 が有効か確認してください)"]

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="koto-log 月次コストレポート（世帯全体・内訳なし）")
    parser.add_argument("--month", default=None, help="集計対象月 YYYY-MM（省略時は当月・JST）")
    parser.add_argument("--db-url", default=None, help="DB URL（省略時は KOTOLOG_DB_URL / .env）")
    args = parser.parse_args(argv)

    config = load_config()
    month = args.month or _current_month()
    db_url = args.db_url or config.db_url

    conn = connect(db_url, auth_token=config.turso_auth_token)
    try:
        summary = crud.monthly_usage_summary(conn, month)
        print(format_summary(summary, month))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
