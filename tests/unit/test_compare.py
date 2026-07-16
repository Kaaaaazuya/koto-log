"""evals/compare.py の単体テスト（E2-1: モデル比較）。

build_comparison_markdown() は手組みの結果 dict（evals/runner.py の run() が返す形）
を受け取り、実 LLM 呼び出しなしで比較 Markdown を組み立てる純関数として検証する。
"""

from __future__ import annotations

import json
import pathlib

from evals.compare import build_comparison_markdown, load_results, main


def _make_result(
    model: str,
    *,
    passed: int,
    total: int,
    fp_rate: float | None,
    by_tag: dict,
    total_cost_usd: float | None,
    avg_latency_ms: float | None,
) -> dict:
    """run() が返す結果 dict の最小形を組み立てる。"""
    return {
        "model": model,
        "prompt_versions": {"extract": "v1", "loop": "v1"},
        "timestamp": "2026-07-10T10:00:00+09:00",
        "golden_set_size": total,
        "summary": {
            "overall": {"total": total, "passed": passed},
            "by_tag": by_tag,
            "false_positive_rate": fp_rate,
        },
        "failures": [],
        "cost": {
            "total_cost_usd": total_cost_usd,
            "total_input_tokens": 1000,
            "total_output_tokens": 200,
            "total_tokens": 1200,
            "call_count": total,
            "avg_latency_ms": avg_latency_ms,
            "p50_latency_ms": avg_latency_ms,
            "max_latency_ms": avg_latency_ms,
            "by_stage": {},
        },
    }


def _model_a():
    # 安い・速いが正答率はやや低いモデル
    return _make_result(
        "anthropic/claude-haiku-4-5-20251001",
        passed=8,
        total=10,
        fp_rate=0.1,
        by_tag={"feeding": {"total": 5, "passed": 4}, "query": {"total": 5, "passed": 4}},
        total_cost_usd=0.0123,
        avg_latency_ms=120.0,
    )


def _model_b():
    # 高い・遅いが正答率は高いモデル
    return _make_result(
        "anthropic/claude-sonnet-4-5-20250929",
        passed=9,
        total=10,
        fp_rate=0.05,
        by_tag={"feeding": {"total": 5, "passed": 5}, "query": {"total": 5, "passed": 4}},
        total_cost_usd=0.0567,
        avg_latency_ms=300.5,
    )


# ---------------------------------------------------------------------------
# build_comparison_markdown
# ---------------------------------------------------------------------------


def test_markdown_includes_both_model_names():
    md = build_comparison_markdown([_model_a(), _model_b()])
    assert "anthropic/claude-haiku-4-5-20251001" in md
    assert "anthropic/claude-sonnet-4-5-20250929" in md


def test_markdown_summary_table_has_pass_rates_and_cost_and_latency():
    md = build_comparison_markdown([_model_a(), _model_b()])
    # 80.0% (haiku: 8/10), 90.0% (sonnet: 9/10)
    assert "80.0%" in md
    assert "90.0%" in md
    # コスト
    assert "0.0123" in md
    assert "0.0567" in md
    # レイテンシ
    assert "120.0" in md
    assert "300.5" in md


def test_markdown_has_per_tag_comparison_row():
    md = build_comparison_markdown([_model_a(), _model_b()])
    assert "feeding" in md
    assert "query" in md
    # feeding: haiku 4/5, sonnet 5/5
    assert "4/5" in md
    assert "5/5" in md


def test_markdown_has_diff_section_highlighting_winners():
    md = build_comparison_markdown([_model_a(), _model_b()])
    assert "差分" in md
    # 正答率は sonnet が上、コスト・レイテンシは haiku が上、という差分言及があること
    assert "anthropic/claude-sonnet-4-5-20250929" in md
    assert "anthropic/claude-haiku-4-5-20251001" in md


def test_markdown_single_result_has_no_diff_crash():
    """1件だけでもクラッシュせず、差分なしの旨を出す。"""
    md = build_comparison_markdown([_model_a()])
    assert "anthropic/claude-haiku-4-5-20251001" in md
    assert "差分" in md


def test_markdown_empty_results_returns_placeholder():
    md = build_comparison_markdown([])
    assert "結果" in md


def test_markdown_handles_missing_cost_data_gracefully():
    """cost.total_cost_usd が None（sink 未検出）でも N/A 表記でクラッシュしない。"""
    result = _make_result(
        "ollama_chat/qwen2.5:7b",
        passed=5,
        total=10,
        fp_rate=None,
        by_tag={},
        total_cost_usd=None,
        avg_latency_ms=None,
    )
    md = build_comparison_markdown([result, _model_a()])
    assert "N/A" in md


# ---------------------------------------------------------------------------
# load_results / main（ファイル読み込み・CLI）
# ---------------------------------------------------------------------------


def test_markdown_handles_null_summary_cost_by_tag_and_model_gracefully():
    """外部結果JSONで summary/cost/by_tag が明示的に null、model が null/欠落でもクラッシュしない。

    evals/runner.py 以外が生成した結果JSONでは、キー自体はあっても値が JSON の null
    （Python の None）になっているケースがあり得る。`.get("summary", {})` は
    キーが存在して値が None のときは None をそのまま返すため、後続の `.get()` チェーンで
    AttributeError になっていた（Gemini Code Assist 指摘）。
    """
    result_all_null = {
        "model": None,
        "summary": None,
        "cost": None,
    }
    result_by_tag_null = {
        # model キー自体が欠落しているケース
        "summary": {
            "overall": {"total": 10, "passed": 5},
            "by_tag": None,
            "false_positive_rate": 0.1,
        },
        "cost": {
            "total_cost_usd": 0.01,
            "avg_latency_ms": 100.0,
        },
    }

    # クラッシュしないこと（AttributeError が発生しない）
    md = build_comparison_markdown([result_all_null, result_by_tag_null])

    # model が None/欠落の場合は index 付きのユニークなプレースホルダーが表示され、
    # 複数の欠落モデルでも列・行を区別できること（Gemini Code Assist 指摘フォローアップ）
    assert "不明なモデル-0" in md
    assert "不明なモデル-1" in md
    # summary/cost が None の行では N/A 表記になること
    assert "N/A" in md


def test_load_results_reads_json_files(tmp_path: pathlib.Path):
    path_a = tmp_path / "a.json"
    path_b = tmp_path / "b.json"
    path_a.write_text(json.dumps(_model_a(), ensure_ascii=False), encoding="utf-8")
    path_b.write_text(json.dumps(_model_b(), ensure_ascii=False), encoding="utf-8")

    results = load_results([path_a, path_b])

    assert [r["model"] for r in results] == [
        "anthropic/claude-haiku-4-5-20251001",
        "anthropic/claude-sonnet-4-5-20250929",
    ]


def test_main_writes_markdown_to_out_path(tmp_path: pathlib.Path, capsys):
    path_a = tmp_path / "a.json"
    path_b = tmp_path / "b.json"
    path_a.write_text(json.dumps(_model_a(), ensure_ascii=False), encoding="utf-8")
    path_b.write_text(json.dumps(_model_b(), ensure_ascii=False), encoding="utf-8")
    out_path = tmp_path / "compare.md"

    main([str(path_a), str(path_b), "--out", str(out_path)])

    assert out_path.exists()
    content = out_path.read_text(encoding="utf-8")
    assert "anthropic/claude-haiku-4-5-20251001" in content
    assert "anthropic/claude-sonnet-4-5-20250929" in content


def test_main_prints_markdown_when_no_out_given(tmp_path: pathlib.Path, capsys):
    path_a = tmp_path / "a.json"
    path_a.write_text(json.dumps(_model_a(), ensure_ascii=False), encoding="utf-8")

    main([str(path_a)])

    captured = capsys.readouterr()
    assert "anthropic/claude-haiku-4-5-20251001" in captured.out
