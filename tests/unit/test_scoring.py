"""evals/scoring.py の単体テスト（E1-2: 採点ロジック）。

実 LLM を呼ばず、extract_fn / loop_fn をスタブ化してロジックのみ検証する。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from evals.scoring import aggregate_results, match_record, match_records, score_case

JST = timezone(timedelta(hours=9))
NOW = datetime(2026, 6, 18, 10, 0, 0, tzinfo=JST)


# ---------------------------------------------------------------------------
# match_record
# ---------------------------------------------------------------------------


def test_match_record_type_match():
    assert match_record({"type": "feeding"}, {"type": "feeding"}, now=NOW)


def test_match_record_type_mismatch():
    assert not match_record({"type": "feeding"}, {"type": "sleep"}, now=NOW)


def test_match_record_sub_type_synonym_normalized():
    """『おっぱい』と『母乳』は正規化後に同一とみなす。"""
    expected = {"type": "feeding", "sub_type": "母乳"}
    actual = {"type": "feeding", "sub_type": "おっぱい"}
    assert match_record(expected, actual, now=NOW)


def test_match_record_sub_type_mismatch():
    expected = {"type": "feeding", "sub_type": "母乳"}
    actual = {"type": "feeding", "sub_type": "ミルク"}
    assert not match_record(expected, actual, now=NOW)


def test_match_record_amount_within_tolerance():
    expected = {"type": "temp", "amount": 36.8}
    actual = {"type": "temp", "amount": 36.8000001}
    assert match_record(expected, actual, now=NOW)


def test_match_record_amount_mismatch():
    expected = {"type": "feeding", "amount": 120}
    actual = {"type": "feeding", "amount": 80}
    assert not match_record(expected, actual, now=NOW)


def test_match_record_amount_expected_but_actual_missing():
    """expected に amount があるのに actual が持たない場合は不一致。"""
    expected = {"type": "feeding", "amount": 120}
    actual = {"type": "feeding"}
    assert not match_record(expected, actual, now=NOW)


def test_match_record_amount_not_checked_when_not_expected():
    """expected に amount が無い場合は actual の値を問わない。"""
    expected = {"type": "baby_food"}
    actual = {"type": "baby_food", "amount": 999}
    assert match_record(expected, actual, now=NOW)


def test_match_record_started_at_within_time_tolerance():
    """相対表現同士でも正規化後の絶対時刻差が許容幅（既定30分）以内なら合格。"""
    expected = {"type": "feeding", "started_at": "9時"}
    actual = {"type": "feeding", "started_at": "9時5分"}
    assert match_record(expected, actual, now=NOW)


def test_match_record_started_at_exceeds_time_tolerance():
    expected = {"type": "feeding", "started_at": "9時"}
    actual = {"type": "feeding", "started_at": "11時"}
    assert not match_record(expected, actual, now=NOW)


def test_match_record_unit_mismatch():
    expected = {"type": "weight", "amount": 6.5, "unit": "kg"}
    actual = {"type": "weight", "amount": 6.5, "unit": "g"}
    assert not match_record(expected, actual, now=NOW)


# ---------------------------------------------------------------------------
# match_records（配列同士、順序対応）
# ---------------------------------------------------------------------------


def test_match_records_all_match():
    expected = [{"type": "feeding"}, {"type": "sleep"}]
    actual = [{"type": "feeding"}, {"type": "sleep"}]
    assert match_records(expected, actual, now=NOW)


def test_match_records_length_mismatch():
    expected = [{"type": "feeding"}, {"type": "sleep"}]
    actual = [{"type": "feeding"}]
    assert not match_records(expected, actual, now=NOW)


def test_match_records_order_mismatch_fails():
    """現状は発話順＝抽出順を期待するため、順序が違うと不一致。"""
    expected = [{"type": "feeding"}, {"type": "sleep"}]
    actual = [{"type": "sleep"}, {"type": "feeding"}]
    assert not match_records(expected, actual, now=NOW)


def test_match_records_empty_both():
    assert match_records([], [], now=NOW)


# ---------------------------------------------------------------------------
# score_case
# ---------------------------------------------------------------------------


def _case(id_, stage, **expected_extra):
    expected = {"stage": stage, **expected_extra}
    return {"id": id_, "utterance": "テスト発話", "tags": ["t"], "expected": expected}


def test_score_case_extract_pass():
    case = _case("c1", "extract", records=[{"type": "feeding", "started_at": "9時"}])
    extract_fn = lambda text: ([{"type": "feeding", "started_at": "9時"}], None)  # noqa: E731
    loop_fn = lambda text: None  # noqa: E731
    result = score_case(case, extract_fn, loop_fn, now=NOW)
    assert result["passed"] is True
    assert result["id"] == "c1"


def test_score_case_extract_fail_wrong_type():
    case = _case("c2", "extract", records=[{"type": "feeding"}])
    extract_fn = lambda text: ([{"type": "sleep"}], None)  # noqa: E731
    loop_fn = lambda text: None  # noqa: E731
    result = score_case(case, extract_fn, loop_fn, now=NOW)
    assert result["passed"] is False


def test_score_case_extract_child_mismatch():
    case = _case("c3", "extract", records=[{"type": "feeding"}], child="たろう")
    extract_fn = lambda text: ([{"type": "feeding"}], "はなこ")  # noqa: E731
    loop_fn = lambda text: None  # noqa: E731
    result = score_case(case, extract_fn, loop_fn, now=NOW)
    assert result["passed"] is False
    assert "child" in result["reason"]


def test_score_case_loop_pass():
    case = _case("c4", "loop", tool="query_records", args={"period": "today", "type": "feeding"})
    extract_fn = lambda text: ([], None)  # noqa: E731
    loop_fn = lambda text: ("query_records", {"period": "today", "type": "feeding"})  # noqa: E731
    result = score_case(case, extract_fn, loop_fn, now=NOW)
    assert result["passed"] is True


def test_score_case_loop_fails_when_extract_non_empty():
    """loop 期待なのに extract_records が非空を返したら誤発火として不合格。"""
    case = _case("c5", "loop", tool="query_records", args={"period": "today"})
    extract_fn = lambda text: ([{"type": "feeding"}], None)  # noqa: E731
    loop_fn = lambda text: None  # noqa: E731
    result = score_case(case, extract_fn, loop_fn, now=NOW)
    assert result["passed"] is False
    assert result["reason"] == "unexpected_extract"


def test_score_case_loop_tool_name_mismatch():
    case = _case("c6", "loop", tool="query_records", args={"period": "today"})
    extract_fn = lambda text: ([], None)  # noqa: E731
    loop_fn = lambda text: ("update_or_delete_record", {"target": "last", "action": "delete"})  # noqa: E731
    result = score_case(case, extract_fn, loop_fn, now=NOW)
    assert result["passed"] is False


def test_score_case_loop_args_subset_mismatch():
    """expected.args に存在するキーだけを厳密照合する。"""
    case = _case("c7", "loop", tool="query_records", args={"period": "today", "type": "feeding"})
    extract_fn = lambda text: ([], None)  # noqa: E731
    loop_fn = lambda text: ("query_records", {"period": "today", "type": "sleep"})  # noqa: E731
    result = score_case(case, extract_fn, loop_fn, now=NOW)
    assert result["passed"] is False


def test_score_case_loop_nested_new_values_match():
    """update_or_delete_record の new_values（ネストしたdict）も部分一致で照合できる。"""
    case = _case(
        "c8b",
        "loop",
        tool="update_or_delete_record",
        args={"target": "last", "action": "update", "new_values": {"amount": 150}},
    )
    extract_fn = lambda text: ([], None)  # noqa: E731
    loop_fn = lambda text: (  # noqa: E731
        "update_or_delete_record",
        {"target": "last", "action": "update", "new_values": {"amount": 150, "note": "ignored extra key"}},
    )
    result = score_case(case, extract_fn, loop_fn, now=NOW)
    assert result["passed"] is True


def test_score_case_loop_nested_new_values_mismatch():
    case = _case(
        "c8c",
        "loop",
        tool="update_or_delete_record",
        args={"target": "last", "action": "update", "new_values": {"amount": 150}},
    )
    extract_fn = lambda text: ([], None)  # noqa: E731
    loop_fn = lambda text: (  # noqa: E731
        "update_or_delete_record",
        {"target": "last", "action": "update", "new_values": {"amount": 100}},
    )
    result = score_case(case, extract_fn, loop_fn, now=NOW)
    assert result["passed"] is False


def test_score_case_loop_none_vs_string_none_not_conflated():
    """expected の None と actual の文字列 "None" を同一視しない（str()合成の落とし穴回避）。"""
    case = _case("c8d", "loop", tool="query_records", args={"sub_type": None})
    extract_fn = lambda text: ([], None)  # noqa: E731
    loop_fn = lambda text: ("query_records", {"sub_type": "None"})  # noqa: E731
    result = score_case(case, extract_fn, loop_fn, now=NOW)
    assert result["passed"] is False


def test_score_case_loop_extra_args_ignored():
    """expected.args に無いキーが actual にあっても問題なし。"""
    case = _case("c8", "loop", tool="query_records", args={"period": "today"})
    extract_fn = lambda text: ([], None)  # noqa: E731
    loop_fn = lambda text: ("query_records", {"period": "today", "type": "feeding"})  # noqa: E731
    result = score_case(case, extract_fn, loop_fn, now=NOW)
    assert result["passed"] is True


def test_score_case_none_pass():
    case = _case("c9", "none")
    extract_fn = lambda text: ([], None)  # noqa: E731
    loop_fn = lambda text: None  # noqa: E731
    result = score_case(case, extract_fn, loop_fn, now=NOW)
    assert result["passed"] is True


def test_score_case_none_fails_on_extract_fire():
    case = _case("c10", "none")
    extract_fn = lambda text: ([{"type": "feeding"}], None)  # noqa: E731
    loop_fn = lambda text: None  # noqa: E731
    result = score_case(case, extract_fn, loop_fn, now=NOW)
    assert result["passed"] is False
    assert result["false_positive"] is True


def test_score_case_none_fails_on_loop_tool_fire():
    case = _case("c11", "none")
    extract_fn = lambda text: ([], None)  # noqa: E731
    loop_fn = lambda text: ("query_records", {"period": "today"})  # noqa: E731
    result = score_case(case, extract_fn, loop_fn, now=NOW)
    assert result["passed"] is False
    assert result["false_positive"] is True


def test_score_case_unknown_stage_raises():
    case = _case("c12", "bogus")
    with pytest.raises(ValueError):
        score_case(case, lambda t: ([], None), lambda t: None, now=NOW)


# ---------------------------------------------------------------------------
# aggregate_results
# ---------------------------------------------------------------------------


def test_aggregate_results_overall_and_by_tag():
    results = [
        {"id": "a", "tags": ["feeding"], "passed": True, "false_positive": False},
        {"id": "b", "tags": ["feeding"], "passed": False, "false_positive": False},
        {"id": "c", "tags": ["sleep"], "passed": True, "false_positive": False},
    ]
    summary = aggregate_results(results)
    assert summary["overall"]["total"] == 3
    assert summary["overall"]["passed"] == 2
    assert summary["by_tag"]["feeding"]["total"] == 2
    assert summary["by_tag"]["feeding"]["passed"] == 1
    assert summary["by_tag"]["sleep"]["passed"] == 1


def test_aggregate_results_false_positive_rate():
    results = [
        {"id": "a", "tags": ["no-tool"], "stage": "none", "passed": True, "false_positive": False},
        {"id": "b", "tags": ["no-tool"], "stage": "none", "passed": False, "false_positive": True},
        {"id": "c", "tags": ["no-tool"], "stage": "none", "passed": False, "false_positive": True},
        {"id": "d", "tags": ["feeding"], "stage": "extract", "passed": True, "false_positive": False},
    ]
    summary = aggregate_results(results)
    assert summary["false_positive_rate"] == pytest.approx(2 / 3)


def test_aggregate_results_false_positive_rate_no_none_cases():
    results = [{"id": "a", "tags": ["feeding"], "stage": "extract", "passed": True, "false_positive": False}]
    summary = aggregate_results(results)
    assert summary["false_positive_rate"] is None


# ---------------------------------------------------------------------------
# ゴールデンセット全体のスモークテスト
# ---------------------------------------------------------------------------


def test_golden_set_loads_and_scores_with_stub():
    """実際の85件YAMLをロードし、スタブ関数で全件エラーなく採点できることを確認する。"""
    import pathlib

    import yaml

    path = pathlib.Path(__file__).resolve().parents[2] / "evals" / "golden" / "utterances.yaml"
    data = yaml.safe_load(path.read_text())
    all_cases = data["good_cases"] + data["hard_cases"] + data["multi_child_cases"]
    assert len(all_cases) == 85

    def extract_fn(text):
        return [], None

    def loop_fn(text):
        return None

    results = [score_case(c, extract_fn, loop_fn, now=NOW) for c in all_cases]
    assert len(results) == 85
    summary = aggregate_results(results)
    assert summary["overall"]["total"] == 85
