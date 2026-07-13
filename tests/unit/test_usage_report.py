"""usage_report.py の単体テスト（Issue #68）。

argv パースは触らず、純粋関数の format_summary のみを検証する。
main() については DB 接続が確実にクローズされることのみ、フェイク接続で検証する。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import kotolog.usage_report as usage_report
from kotolog.usage_report import format_summary


class _FakeConn:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


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


def _fake_config():
    return SimpleNamespace(db_url="x", turso_auth_token=None)


def test_main_closes_connection_on_success(monkeypatch):
    fake_conn = _FakeConn()
    monkeypatch.setattr(usage_report, "load_config", _fake_config)
    monkeypatch.setattr(usage_report, "connect", lambda db_url, auth_token=None: fake_conn)
    monkeypatch.setattr(usage_report.crud, "monthly_usage_summary", lambda conn, month: _summary())

    usage_report.main(["--month", "2026-07"])

    assert fake_conn.closed is True


def test_main_closes_connection_even_if_summary_raises(monkeypatch):
    """集計中に例外が起きても finally で接続をクローズする（Gemini指摘対応 #68）。"""
    fake_conn = _FakeConn()
    monkeypatch.setattr(usage_report, "load_config", _fake_config)
    monkeypatch.setattr(usage_report, "connect", lambda db_url, auth_token=None: fake_conn)

    def _raise(conn, month):
        raise RuntimeError("boom")

    monkeypatch.setattr(usage_report.crud, "monthly_usage_summary", _raise)

    with pytest.raises(RuntimeError):
        usage_report.main(["--month", "2026-07"])

    assert fake_conn.closed is True
