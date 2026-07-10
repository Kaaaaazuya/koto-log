"""E2-1: モデル比較のレポート生成。

evals/runner.py の run()/save_result() が書き出す結果 JSON を2件（以上）読み込み、
正答率・誤発火率・コスト・レイテンシを並べた比較 Markdown を組み立てる。

実行:
    uv run python -m evals.compare evals/results/xxx_haiku.json evals/results/yyy_sonnet.json
    uv run python -m evals.compare evals/results/*.json --out evals/results/compare.md
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

# ---------------------------------------------------------------------------
# フォーマット用の小さなヘルパー
# ---------------------------------------------------------------------------


def _rate_str(passed: int, total: int) -> str:
    if total == 0:
        return "N/A"
    return f"{passed}/{total} ({passed / total:.1%})"


def _pct_str(rate: float | None) -> str:
    return f"{rate:.1%}" if rate is not None else "N/A"


def _cost_str(cost_usd: float | None) -> str:
    return f"${cost_usd:.4f}" if cost_usd is not None else "N/A"


def _latency_str(latency_ms: float | None) -> str:
    return f"{latency_ms:.1f}" if latency_ms is not None else "N/A"


def _model_name(result: dict, index: int) -> str:
    return result.get("model") or f"model-{index}"


# ---------------------------------------------------------------------------
# 差分（勝敗）判定
# ---------------------------------------------------------------------------


def _best(results: list[dict], value_fn, *, higher_is_better: bool) -> tuple[dict, float] | None:
    """value_fn(result) が None でない結果の中から最良の (result, value) を返す。

    全件 None（データなし）なら None を返す。
    """
    scored = [(r, value_fn(r)) for r in results]
    scored = [(r, v) for r, v in scored if v is not None]
    if not scored:
        return None
    key = (lambda rv: rv[1]) if higher_is_better else (lambda rv: -rv[1])
    return max(scored, key=key)


def _pass_rate(result: dict) -> float | None:
    overall = result.get("summary", {}).get("overall", {})
    total = overall.get("total", 0)
    if not total:
        return None
    return overall.get("passed", 0) / total


def _total_cost(result: dict) -> float | None:
    return result.get("cost", {}).get("total_cost_usd")


def _avg_latency(result: dict) -> float | None:
    return result.get("cost", {}).get("avg_latency_ms")


def _diff_bullets(results: list[dict]) -> list[str]:
    """正答率・コスト・レイテンシそれぞれで最良のモデルを1行ずつ挙げる。"""
    bullets: list[str] = []

    best_rate = _best(results, _pass_rate, higher_is_better=True)
    if best_rate is not None:
        r, v = best_rate
        bullets.append(f"- 正答率: **{r.get('model')}** が最高（{v:.1%}）")

    best_cost = _best(results, _total_cost, higher_is_better=False)
    if best_cost is not None:
        r, v = best_cost
        bullets.append(f"- コスト: **{r.get('model')}** が最安（${v:.4f}）")

    best_latency = _best(results, _avg_latency, higher_is_better=False)
    if best_latency is not None:
        r, v = best_latency
        bullets.append(f"- レイテンシ: **{r.get('model')}** が最速（平均 {v:.1f}ms）")

    return bullets


# ---------------------------------------------------------------------------
# 本体: build_comparison_markdown（純関数。実LLM呼び出し・ファイルI/Oなし）
# ---------------------------------------------------------------------------


def build_comparison_markdown(results: list[dict]) -> str:
    """run() が返す結果 dict のリストから比較 Markdown を組み立てる。"""
    if not results:
        return "# モデル比較\n\n(結果なし)\n"

    lines: list[str] = ["# モデル比較", ""]

    # --- サマリーテーブル -----------------------------------------------
    lines.append("## サマリー")
    lines.append("")
    lines.append("| モデル | 正答率 | 誤発火率 | 総コスト(USD) | 平均レイテンシ(ms) |")
    lines.append("|---|---|---|---|---|")
    for i, r in enumerate(results):
        model = _model_name(r, i)
        overall = r.get("summary", {}).get("overall", {"total": 0, "passed": 0})
        rate = _rate_str(overall.get("passed", 0), overall.get("total", 0))
        fp = _pct_str(r.get("summary", {}).get("false_positive_rate"))
        cost = _cost_str(_total_cost(r))
        latency = _latency_str(_avg_latency(r))
        lines.append(f"| {model} | {rate} | {fp} | {cost} | {latency} |")
    lines.append("")

    # --- タグ別正答率テーブル ---------------------------------------------
    lines.append("## タグ別正答率")
    lines.append("")
    model_names = [_model_name(r, i) for i, r in enumerate(results)]
    all_tags = sorted({tag for r in results for tag in r.get("summary", {}).get("by_tag", {})})
    lines.append("| タグ | " + " | ".join(model_names) + " |")
    lines.append("|---|" + "---|" * len(model_names))
    for tag in all_tags:
        row = [tag]
        for r in results:
            bucket = r.get("summary", {}).get("by_tag", {}).get(tag)
            row.append(_rate_str(bucket["passed"], bucket["total"]) if bucket else "-")
        lines.append("| " + " | ".join(row) + " |")
    lines.append("")

    # --- 差分 --------------------------------------------------------------
    lines.append("## 差分")
    lines.append("")
    if len(results) < 2:
        lines.append("- 比較対象が1件のため差分なし")
    else:
        bullets = _diff_bullets(results)
        lines.extend(bullets if bullets else ["- 比較可能なデータがありません"])
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# ファイルI/O・CLI
# ---------------------------------------------------------------------------


def load_results(paths: list[pathlib.Path]) -> list[dict]:
    """結果 JSON ファイル群を読み込み、渡した順のまま dict のリストを返す。"""
    return [json.loads(pathlib.Path(p).read_text(encoding="utf-8")) for p in paths]


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="koto-log evals: 結果JSON同士のモデル比較Markdown生成")
    parser.add_argument("results", nargs="+", help="比較する結果JSONのパス（2件以上を推奨）")
    parser.add_argument("--out", default=None, help="出力先Markdownパス（省略時は標準出力へ）")
    args = parser.parse_args(argv)

    paths = [pathlib.Path(p) for p in args.results]
    results = load_results(paths)
    markdown = build_comparison_markdown(results)

    if args.out:
        out_path = pathlib.Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(markdown, encoding="utf-8")
        print(f"saved: {out_path}")
    else:
        print(markdown)


if __name__ == "__main__":
    main(sys.argv[1:])
