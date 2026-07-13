"""crud.monthly_usage_summary のテスト（結合・実DB / Issue #68）。

usage_log テーブルへ直接行を挿入し、月・operation・model 別の集計を検証する。
"""

from __future__ import annotations

import pytest

from kotolog.db import crud
from kotolog.obs.usage import DbSink, UsageEvent


def _event(**overrides) -> UsageEvent:
    fields = dict(
        trace_id="t",
        operation="loop",
        model="claude-3-5-haiku-latest",
        input_tokens=10,
        output_tokens=5,
        total_tokens=15,
        cache_read_input_tokens=0,
        cache_creation_input_tokens=0,
        cost_usd=0.001,
        ts="2026-07-10T09:00:00+09:00",
    )
    fields.update(overrides)
    return UsageEvent(**fields)


def test_monthly_usage_summary_totals(conn):
    sink = DbSink(conn)
    sink.record(
        _event(operation="extract", cost_usd=0.001, input_tokens=10, output_tokens=5, total_tokens=15)
    )
    sink.record(_event(operation="loop", cost_usd=0.002, input_tokens=20, output_tokens=8, total_tokens=28))

    summary = crud.monthly_usage_summary(conn, "2026-07")

    assert summary["call_count"] == 2
    assert summary["total_input_tokens"] == 30
    assert summary["total_output_tokens"] == 13
    assert summary["total_tokens"] == summary["total_input_tokens"] + summary["total_output_tokens"]
    assert round(summary["total_cost_usd"], 3) == 0.003


def test_monthly_usage_summary_excludes_other_months(conn):
    sink = DbSink(conn)
    sink.record(_event(ts="2026-07-01T00:00:00+09:00"))
    sink.record(_event(ts="2026-06-30T23:59:59+09:00"))
    sink.record(_event(ts="2026-08-01T00:00:00+09:00"))

    summary = crud.monthly_usage_summary(conn, "2026-07")

    assert summary["call_count"] == 1


def test_monthly_usage_summary_null_cost_treated_as_zero(conn):
    sink = DbSink(conn)
    sink.record(_event(cost_usd=None))
    sink.record(_event(cost_usd=0.5))

    summary = crud.monthly_usage_summary(conn, "2026-07")

    assert summary["total_cost_usd"] == 0.5
    assert summary["call_count"] == 2


def test_monthly_usage_summary_by_operation(conn):
    sink = DbSink(conn)
    sink.record(_event(operation="extract", input_tokens=10, output_tokens=1, cost_usd=0.01))
    sink.record(_event(operation="extract", input_tokens=10, output_tokens=1, cost_usd=0.01))
    sink.record(_event(operation="loop", input_tokens=20, output_tokens=2, cost_usd=0.02))

    summary = crud.monthly_usage_summary(conn, "2026-07")

    assert summary["by_operation"]["extract"]["calls"] == 2
    assert summary["by_operation"]["extract"]["input_tokens"] == 20
    assert summary["by_operation"]["extract"]["output_tokens"] == 2
    assert round(summary["by_operation"]["extract"]["cost_usd"], 3) == 0.02
    assert summary["by_operation"]["loop"]["calls"] == 1


def test_monthly_usage_summary_by_model(conn):
    sink = DbSink(conn)
    sink.record(_event(model="claude-3-5-haiku-latest", cost_usd=0.01))
    sink.record(_event(model="claude-3-5-sonnet-latest", cost_usd=0.05))

    summary = crud.monthly_usage_summary(conn, "2026-07")

    assert summary["by_model"]["claude-3-5-haiku-latest"]["calls"] == 1
    assert summary["by_model"]["claude-3-5-sonnet-latest"]["calls"] == 1
    assert round(summary["by_model"]["claude-3-5-sonnet-latest"]["cost_usd"], 3) == 0.05


def test_monthly_usage_summary_empty_month_returns_zeros(conn):
    summary = crud.monthly_usage_summary(conn, "2099-01")

    assert summary["call_count"] == 0
    assert summary["total_cost_usd"] == 0
    assert summary["total_input_tokens"] == 0
    assert summary["total_output_tokens"] == 0
    assert summary["total_tokens"] == 0
    assert summary["by_operation"] == {}
    assert summary["by_model"] == {}


@pytest.mark.parametrize("bad_year_month", ["2026", "2026-1", "2026-%2", "2026-0_"])
def test_monthly_usage_summary_rejects_invalid_year_month(conn, bad_year_month):
    """YYYY-MM 形式以外（LIKE の % / _ を含むものを含む）は ValueError にする。"""
    with pytest.raises(ValueError, match="year_month must be in YYYY-MM format"):
        crud.monthly_usage_summary(conn, bad_year_month)


def test_monthly_usage_summary_accepts_valid_year_month(conn):
    summary = crud.monthly_usage_summary(conn, "2026-07")

    assert summary["call_count"] == 0


def test_monthly_usage_summary_household_wide_no_per_user_field(conn):
    """世帯全体のサマリーであり、ユーザー別内訳は含まない（PII最小化・仕様どおり）。"""
    sink = DbSink(conn)
    sink.record(_event())

    summary = crud.monthly_usage_summary(conn, "2026-07")

    assert "by_user" not in summary
    assert "line_user_id" not in summary
