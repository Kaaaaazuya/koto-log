"""obs/usage.py の単体テスト（P7 / ADR-0002）。

UsageEvent の組み立て・Sink の出力・トレース ID・PII 非混入を検証する。
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from types import SimpleNamespace

from kotolog.db import crud
from kotolog.db.connection import connect
from kotolog.obs.usage import (
    DbSink,
    FanOutSink,
    JsonLogSink,
    ListSink,
    NullSink,
    UsageEvent,
    build_event,
    current_trace_id,
    new_trace_id,
    sink_from_config,
)


def _resp(prompt=100, completion=30, total=None, model="claude-3-5-haiku-latest", **usage_extra):
    usage = SimpleNamespace(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=total,
        **usage_extra,
    )
    return SimpleNamespace(model=model, usage=usage)


# --- build_event -------------------------------------------------------------


def test_build_event_extracts_tokens_and_model():
    ev = build_event(_resp(total=130), operation="loop", trace_id="t1")
    assert ev.operation == "loop"
    assert ev.trace_id == "t1"
    assert ev.model == "claude-3-5-haiku-latest"
    assert ev.input_tokens == 100
    assert ev.output_tokens == 30
    assert ev.total_tokens == 130
    assert ev.ts  # ISO8601 文字列が入る


def test_total_tokens_falls_back_to_sum_when_missing():
    ev = build_event(_resp(prompt=80, completion=20, total=None), operation="loop", trace_id="t")
    assert ev.total_tokens == 100


def test_cache_tokens_default_zero_when_absent():
    ev = build_event(_resp(), operation="loop", trace_id="t")
    assert ev.cache_read_input_tokens == 0
    assert ev.cache_creation_input_tokens == 0


def test_cache_tokens_extracted_when_present():
    resp = _resp(cache_creation_input_tokens=12)
    resp.usage.prompt_tokens_details = SimpleNamespace(cached_tokens=34)
    ev = build_event(resp, operation="loop", trace_id="t")
    assert ev.cache_read_input_tokens == 34
    assert ev.cache_creation_input_tokens == 12


def test_cost_is_none_when_unavailable(monkeypatch):
    # litellm.completion_cost が例外を投げても None で成立する。
    monkeypatch.setattr(
        "kotolog.obs.usage.litellm.completion_cost",
        lambda **kw: (_ for _ in ()).throw(Exception("no price")),
    )
    ev = build_event(_resp(), operation="loop", trace_id="t")
    assert ev.cost_usd is None


def test_missing_usage_is_zero_filled():
    ev = build_event(SimpleNamespace(model="m"), operation="push", trace_id="t")
    assert ev.input_tokens == 0
    assert ev.output_tokens == 0
    assert ev.total_tokens == 0


# --- PII 非混入 --------------------------------------------------------------


def test_event_schema_has_no_body_or_argument_fields():
    """UsageEvent は計測スキーマの固定フィールドのみを持つ（本文・引数値を持たない）。"""
    ev = build_event(_resp(), operation="loop", trace_id="t")
    assert set(asdict(ev).keys()) == {
        "trace_id",
        "operation",
        "model",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
        "cost_usd",
        "ts",
    }


def test_build_event_ignores_response_content():
    """レスポンス本文（育児ログを含みうる）はイベントに混入しない。"""
    resp = _resp()
    resp.choices = [SimpleNamespace(message=SimpleNamespace(content="母乳120ml 9時 太郎"))]
    ev = build_event(resp, operation="loop", trace_id="t")
    assert "母乳" not in json.dumps(asdict(ev), ensure_ascii=False)
    assert "太郎" not in json.dumps(asdict(ev), ensure_ascii=False)


# --- Sink --------------------------------------------------------------------


def test_json_log_sink_emits_one_json_line(caplog):
    ev = UsageEvent(
        trace_id="abc",
        operation="extract",
        model="m",
        input_tokens=10,
        output_tokens=2,
        total_tokens=12,
        cache_read_input_tokens=0,
        cache_creation_input_tokens=0,
        cost_usd=None,
        ts="2026-06-26T07:00:00+09:00",
    )
    with caplog.at_level(logging.INFO, logger="kotolog.usage"):
        JsonLogSink().record(ev)

    assert len(caplog.records) == 1
    payload = json.loads(caplog.records[0].getMessage().removeprefix("usage "))
    assert payload["operation"] == "extract"
    assert payload["input_tokens"] == 10
    assert payload["trace_id"] == "abc"


def test_null_sink_does_nothing(caplog):
    ev = build_event(_resp(), operation="loop", trace_id="t")
    with caplog.at_level(logging.INFO, logger="kotolog.usage"):
        NullSink().record(ev)
    assert caplog.records == []


def test_list_sink_appends_events_in_order():
    """ListSink は record() のたびにイベントをリストへ追記する（evals ランナーの計測に使用）。"""
    sink = ListSink()
    assert sink.events == []

    events = [
        UsageEvent(
            trace_id=f"t{i}",
            operation="extract",
            model="m",
            input_tokens=10 * i,
            output_tokens=2 * i,
            total_tokens=12 * i,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
            cost_usd=0.001 * i,
            ts=f"2026-07-10T10:0{i}:00+09:00",
        )
        for i in range(1, 4)
    ]
    for ev in events:
        sink.record(ev)

    assert len(sink.events) == 3
    assert sink.events == events


def test_list_sink_instances_do_not_share_state():
    """複数インスタンス間でリストが共有されない（クラス変数のミュータブルデフォルト回避）。"""
    a = ListSink()
    b = ListSink()
    a.record(build_event(_resp(), operation="loop", trace_id="a"))
    assert a.events != b.events
    assert b.events == []


def test_sink_from_config():
    assert isinstance(sink_from_config(SimpleNamespace(usage_log=True)), JsonLogSink)
    assert isinstance(sink_from_config(SimpleNamespace(usage_log=False)), NullSink)
    assert isinstance(sink_from_config(SimpleNamespace()), NullSink)


# --- DbSink（Issue #68 / ADR-0002 DB永続化） ----------------------------------


def _usage_db_conn():
    conn = connect(":memory:")
    crud.init_db(conn)
    return conn


def _sample_event(**overrides) -> UsageEvent:
    fields = dict(
        trace_id="t1",
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


def test_db_sink_inserts_one_row():
    conn = _usage_db_conn()
    ev = _sample_event()

    DbSink(conn).record(ev)

    rows = conn.execute("SELECT * FROM usage_log").fetchall()
    assert len(rows) == 1
    row = rows[0]
    assert row["trace_id"] == "t1"
    assert row["operation"] == "loop"
    assert row["model"] == "claude-3-5-haiku-latest"
    assert row["input_tokens"] == 10
    assert row["output_tokens"] == 5
    assert row["total_tokens"] == 15
    assert row["cache_read_input_tokens"] == 0
    assert row["cache_creation_input_tokens"] == 0
    assert row["cost_usd"] == 0.001
    assert row["ts"] == "2026-07-10T09:00:00+09:00"
    conn.close()


def test_db_sink_handles_null_cost():
    conn = _usage_db_conn()
    ev = _sample_event(cost_usd=None)

    DbSink(conn).record(ev)

    row = conn.execute("SELECT cost_usd FROM usage_log").fetchone()
    assert row["cost_usd"] is None
    conn.close()


def test_db_sink_row_columns_match_usage_event_plus_id():
    """PII非混入の確認: usage_log の列は UsageEvent のフィールド + id のみ。"""
    conn = _usage_db_conn()
    DbSink(conn).record(_sample_event())

    cursor = conn.execute("SELECT * FROM usage_log LIMIT 1")
    columns = {d[0] for d in cursor.description}
    assert columns == {"id"} | set(asdict(_sample_event()).keys())
    conn.close()


def test_db_sink_multiple_records_append_rows():
    conn = _usage_db_conn()
    sink = DbSink(conn)
    sink.record(_sample_event(operation="extract"))
    sink.record(_sample_event(operation="push"))

    rows = conn.execute("SELECT operation FROM usage_log ORDER BY id").fetchall()
    assert [r["operation"] for r in rows] == ["extract", "push"]
    conn.close()


# --- FanOutSink / sink_from_config(conn=...) ----------------------------------


def test_fan_out_sink_forwards_to_all_sinks():
    ev = _sample_event()
    calls: list[str] = []

    class _Recorder:
        def __init__(self, name):
            self.name = name

        def record(self, event):
            calls.append(self.name)

    FanOutSink([_Recorder("a"), _Recorder("b")]).record(ev)
    assert calls == ["a", "b"]


def test_sink_from_config_db_only_returns_db_sink():
    conn = _usage_db_conn()
    sink = sink_from_config(SimpleNamespace(usage_log=False, usage_db=True), conn=conn)
    assert isinstance(sink, DbSink)
    conn.close()


def test_sink_from_config_both_flags_returns_fan_out():
    conn = _usage_db_conn()
    sink = sink_from_config(SimpleNamespace(usage_log=True, usage_db=True), conn=conn)
    assert isinstance(sink, FanOutSink)
    conn.close()


def test_sink_from_config_db_flag_without_conn_falls_back():
    """usage_db=True でも conn が無ければ DbSink は組み立てられず graceful に fallback する。"""
    sink = sink_from_config(SimpleNamespace(usage_log=False, usage_db=True), conn=None)
    assert isinstance(sink, NullSink)


def test_sink_from_config_neither_flag_is_null_sink():
    conn = _usage_db_conn()
    sink = sink_from_config(SimpleNamespace(usage_log=False, usage_db=False), conn=conn)
    assert isinstance(sink, NullSink)
    conn.close()


def test_sink_from_config_fan_out_actually_records_to_both():
    conn = _usage_db_conn()
    sink = sink_from_config(SimpleNamespace(usage_log=True, usage_db=True), conn=conn)
    sink.record(_sample_event())

    rows = conn.execute("SELECT * FROM usage_log").fetchall()
    assert len(rows) == 1
    conn.close()


# --- トレース ID -------------------------------------------------------------


def test_new_trace_id_sets_and_returns():
    tid = new_trace_id()
    assert tid
    assert current_trace_id() == tid


def test_new_trace_id_is_unique_per_call():
    assert new_trace_id() != new_trace_id()
