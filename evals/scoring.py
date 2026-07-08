"""E1-2: ゴールデンセット（evals/golden/utterances.yaml）の採点ロジック。

本番の Agent.handle と同じ2段構成（extract_records 優先→空ならTOOLSループ）
を前提に、各ケースの expected.stage（extract/loop/none）に応じて一致判定する。
"""

from __future__ import annotations

from datetime import datetime

from kotolog.utils.subtype import normalize_sub_type
from kotolog.utils.timeparse import normalize as normalize_time

TIME_TOLERANCE_MINUTES = 30


def _times_close(expected: str, actual: str, now: datetime) -> bool:
    exp_dt = datetime.fromisoformat(normalize_time(expected, now=now))
    act_dt = datetime.fromisoformat(normalize_time(actual, now=now))
    return abs((exp_dt - act_dt).total_seconds()) <= TIME_TOLERANCE_MINUTES * 60


def match_record(expected: dict, actual: dict, now: datetime) -> bool:
    """1件のレコードが expected の条件を満たすか判定する。

    type は常に一致必須。それ以外は expected に含まれるキーだけを照合する
    （未指定のフィールドは問わない）。
    """
    if expected.get("type") != actual.get("type"):
        return False

    if "sub_type" in expected:
        rec_type = expected.get("type")
        exp_sub = normalize_sub_type(rec_type, expected.get("sub_type"))
        act_sub = normalize_sub_type(rec_type, actual.get("sub_type"))
        if exp_sub != act_sub:
            return False

    if "amount" in expected:
        try:
            if abs(float(expected["amount"]) - float(actual.get("amount"))) > 1e-6:
                return False
        except (TypeError, ValueError):
            return False

    if "unit" in expected and expected["unit"] != actual.get("unit"):
        return False

    for field in ("started_at", "ended_at"):
        if field in expected:
            actual_value = actual.get(field)
            if not actual_value or not _times_close(expected[field], actual_value, now):
                return False

    return True


def match_records(expected: list[dict], actual: list[dict], now: datetime) -> bool:
    """レコード配列同士を発話順（インデックス対応）で照合する。"""
    if len(expected) != len(actual):
        return False
    return all(match_record(exp, act, now) for exp, act in zip(expected, actual))


def _value_match(want, got) -> bool:
    if isinstance(want, dict):
        return isinstance(got, dict) and _args_match(want, got)
    if want is None or got is None:
        return want is got
    return str(got) == str(want)


def _args_match(expected_args: dict, actual_args: dict) -> bool:
    """expected_args に存在するキーだけを厳密照合する（actual 側の余分なキーは無視）。

    値が dict（例: update_or_delete_record の new_values）の場合は再帰的に
    同じルールで部分一致させる。
    """
    for key, want in expected_args.items():
        if not _value_match(want, actual_args.get(key)):
            return False
    return True


def score_case(case: dict, extract_fn, loop_fn, now: datetime) -> dict:
    """1ケースを採点する。

    Args:
        extract_fn: text -> (records: list[dict], child_name: str | None)
        loop_fn: text -> (tool_name, args) | None（ツール未呼び出しなら None）
    """
    expected = case["expected"]
    stage = expected.get("stage")
    utterance = case["utterance"]
    base = {"id": case["id"], "tags": case.get("tags", []), "stage": stage, "false_positive": False}

    if stage == "extract":
        records, child_name = extract_fn(utterance)
        if not match_records(expected.get("records", []), records, now):
            return {**base, "passed": False, "reason": "records_mismatch"}
        if "child" in expected and expected["child"] != child_name:
            return {**base, "passed": False, "reason": "child_mismatch"}
        return {**base, "passed": True, "reason": "ok"}

    if stage == "loop":
        records, _ = extract_fn(utterance)
        if records:
            return {**base, "passed": False, "reason": "unexpected_extract", "false_positive": True}
        call = loop_fn(utterance)
        if call is None:
            return {**base, "passed": False, "reason": "no_tool_call"}
        tool_name, args = call
        if tool_name != expected.get("tool"):
            return {**base, "passed": False, "reason": f"tool_mismatch:{tool_name}"}
        if not _args_match(expected.get("args", {}), args):
            return {**base, "passed": False, "reason": f"args_mismatch:{args}"}
        return {**base, "passed": True, "reason": "ok"}

    if stage == "none":
        records, _ = extract_fn(utterance)
        if records:
            return {**base, "passed": False, "reason": "unexpected_extract", "false_positive": True}
        call = loop_fn(utterance)
        if call is not None:
            return {**base, "passed": False, "reason": f"unexpected_tool:{call[0]}", "false_positive": True}
        return {**base, "passed": True, "reason": "ok"}

    raise ValueError(f"unknown stage: {stage!r}")


def aggregate_results(results: list[dict]) -> dict:
    """全体正解率・タグ別正解率・stage=none ケースの誤発火率を集計する。"""
    overall_total = len(results)
    overall_passed = sum(1 for r in results if r["passed"])

    by_tag: dict[str, dict[str, int]] = {}
    for r in results:
        for tag in r.get("tags", []):
            bucket = by_tag.setdefault(tag, {"total": 0, "passed": 0})
            bucket["total"] += 1
            bucket["passed"] += int(r["passed"])

    none_cases = [r for r in results if r.get("stage") == "none"]
    fp_rate = sum(1 for r in none_cases if r["false_positive"]) / len(none_cases) if none_cases else None

    return {
        "overall": {"total": overall_total, "passed": overall_passed},
        "by_tag": by_tag,
        "false_positive_rate": fp_rate,
    }
