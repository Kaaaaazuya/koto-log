"""usage_report.py の単体テスト（Issue #68）。

argv パースは触らず、純粋関数の format_summary のみを検証する。
"""

from __future__ import annotations

from kotolog.usage_report import format_summary


def _summary(**overrides):
    base = dict(
        total_cost_usd=0.1234,
        total_input_tokens=1000,
        total_output_tokens=200,
        total_tokens=1200,
        call_count=5,
        by_operation={
            "extract": {"calls": 3, "input_tokens": 600, "output_tokens": 100, "cost_usd": 0.07},
            "loop": {"calls": 2, "input_tokens": 400, "output_tokens": 100, "cost_usd": 0.0534},
        },
        by_model={
            "claude-3-5-haiku-latest": {
                "calls": 5,
                "input_tokens": 1000,
                "output_tokens": 200,
                "cost_usd": 0.1234,
            },
        },
    )
    base.update(overrides)
    return base


def test_format_summary_includes_month_and_totals():
    text = format_summary(_summary(), "2026-07")
    assert "2026-07" in text
    assert "0.1234" in text
    assert "1,000" in text or "1000" in text
    assert "5" in text  # call_count


def test_format_summary_includes_operation_breakdown():
    text = format_summary(_summary(), "2026-07")
    assert "extract" in text
    assert "loop" in text


def test_format_summary_includes_model_breakdown():
    text = format_summary(_summary(), "2026-07")
    assert "claude-3-5-haiku-latest" in text


def test_format_summary_empty_month_is_readable():
    empty = _summary(
        total_cost_usd=0,
        total_input_tokens=0,
        total_output_tokens=0,
        total_tokens=0,
        call_count=0,
        by_operation={},
        by_model={},
    )
    text = format_summary(empty, "2099-01")
    assert "2099-01" in text
    assert "0" in text


def test_format_summary_has_no_per_user_breakdown():
    """世帯全体のサマリーであり、ユーザー別出力は行わない（仕様どおり）。"""
    text = format_summary(_summary(), "2026-07")
    assert "line_user_id" not in text
