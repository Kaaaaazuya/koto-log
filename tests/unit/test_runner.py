"""evals/runner.py の単体テスト（E1-3: 実行基盤）。

実 LLM を呼ばず、tests/conftest.py の FakeLLM/make_resp/make_tc を使って
run() 全体を検証する。
"""

from __future__ import annotations

import json
import pathlib

import yaml

from evals.runner import (
    GOLDEN_PATH,
    build_extract_fn,
    build_loop_fn,
    load_golden_cases,
    run,
    save_result,
)

# ---------------------------------------------------------------------------
# load_golden_cases
# ---------------------------------------------------------------------------


def _write_golden(tmp_path: pathlib.Path) -> pathlib.Path:
    """good/hard/multi_child を1件ずつ持つ最小限のゴールデンYAMLを書く。"""
    data = {
        "good_cases": [
            {
                "id": "good-1",
                "utterance": "3時にミルク120ml飲んだ",
                "tags": ["feeding"],
                "expected": {
                    "stage": "extract",
                    "records": [{"type": "feeding", "sub_type": "ミルク", "amount": 120, "unit": "ml"}],
                },
            }
        ],
        "hard_cases": [
            {
                "id": "hard-1",
                "utterance": "今日は何回飲んだ？",
                "tags": ["query"],
                "expected": {"stage": "loop", "tool": "query_records", "args": {"period": "today"}},
            }
        ],
        "multi_child_cases": [
            {
                "id": "multi-1",
                "utterance": "たろうに授乳した",
                "tags": ["multi_child"],
                "expected": {"stage": "extract", "records": [{"type": "feeding"}], "child": "たろう"},
            }
        ],
    }
    path = tmp_path / "golden.yaml"
    path.write_text(yaml.dump(data, allow_unicode=True), encoding="utf-8")
    return path


def test_load_golden_cases_combines_three_categories(tmp_path):
    """good/hard/multi_child の全ケースが結合されて返る。"""
    path = _write_golden(tmp_path)
    cases = load_golden_cases(path)
    ids = [c["id"] for c in cases]
    assert ids == ["good-1", "hard-1", "multi-1"]


def test_load_golden_cases_real_golden_set_smoke():
    """実際の evals/golden/utterances.yaml（85件）が読み込めることを確認する。"""
    cases = load_golden_cases(GOLDEN_PATH)
    assert len(cases) == 85
    assert all("id" in c and "utterance" in c and "expected" in c for c in cases)


# ---------------------------------------------------------------------------
# build_extract_fn
# ---------------------------------------------------------------------------


def test_build_extract_fn_returns_records_and_child(fake_llm, resp, tc):
    """FakeLLM の tool_call から (records, child_name) を正しく取り出す。"""
    args = {"records": [{"type": "feeding", "sub_type": "ミルク", "amount": 120}], "child": "たろう"}
    llm = fake_llm([resp(tool_calls=[tc("extract_records", args)])])
    extract_fn = build_extract_fn(llm)

    records, child_name = extract_fn("3時にたろうにミルク120ml")

    assert records == [{"type": "feeding", "sub_type": "ミルク", "amount": 120}]
    assert child_name == "たろう"


def test_build_extract_fn_empty_when_no_tool_call(fake_llm, resp):
    """tool_call が無い応答なら空リスト・child_name=None を返す。"""
    llm = fake_llm([resp(content="わかりません")])
    extract_fn = build_extract_fn(llm)

    records, child_name = extract_fn("今日は何回飲んだ？")

    assert records == []
    assert child_name is None


# ---------------------------------------------------------------------------
# build_loop_fn
# ---------------------------------------------------------------------------


def test_build_loop_fn_returns_tool_and_args(fake_llm, resp, tc):
    """tool_call ありの応答なら (tool_name, args) を返す。"""
    args = {"period": "today", "type": "feeding"}
    llm = fake_llm([resp(tool_calls=[tc("query_records", args)])])
    loop_fn = build_loop_fn(llm)

    result = loop_fn("今日は何回飲んだ？")

    assert result == ("query_records", args)


def test_build_loop_fn_returns_none_when_no_tool_call(fake_llm, resp):
    """本文のみ（tool_call 無し）の応答なら None を返す。"""
    llm = fake_llm([resp(content="それはわかりません")])
    loop_fn = build_loop_fn(llm)

    result = loop_fn("今日の天気は？")

    assert result is None


def test_build_loop_fn_uses_system_prompt_and_calls_once(fake_llm, resp, tc):
    """本番の SYSTEM_PROMPT で1回だけ問い合わせる（operation="loop"）。"""
    from kotolog.agent.loop import SYSTEM_PROMPT

    llm = fake_llm([resp(tool_calls=[tc("query_records", {"period": "today"})])])
    loop_fn = build_loop_fn(llm)
    loop_fn("今日は何回飲んだ？")

    assert len(llm.seen_messages) == 1
    messages = llm.seen_messages[0]
    assert messages[0] == {"role": "system", "content": SYSTEM_PROMPT}
    assert messages[1] == {"role": "user", "content": "今日は何回飲んだ？"}
    assert llm.seen_operations == ["loop"]


# ---------------------------------------------------------------------------
# run()
# ---------------------------------------------------------------------------


def _run_golden(tmp_path: pathlib.Path) -> pathlib.Path:
    """stage=extract/loop/none を1件ずつ含む小さなゴールデンYAMLを書く。"""
    data = {
        "good_cases": [
            {
                "id": "e-1",
                "utterance": "3時にミルク120ml飲んだ",
                "tags": ["feeding"],
                "expected": {
                    "stage": "extract",
                    "records": [{"type": "feeding", "sub_type": "ミルク", "amount": 120}],
                },
            }
        ],
        "hard_cases": [
            {
                "id": "l-1",
                "utterance": "今日は何回飲んだ？",
                "tags": ["query"],
                "expected": {"stage": "loop", "tool": "query_records", "args": {"period": "today"}},
            },
            {
                "id": "n-1",
                "utterance": "こんにちは",
                "tags": ["chitchat"],
                "expected": {"stage": "none"},
            },
        ],
        "multi_child_cases": [],
    }
    path = tmp_path / "golden_small.yaml"
    path.write_text(yaml.dump(data, allow_unicode=True), encoding="utf-8")
    return path


def test_run_returns_expected_structure(tmp_path, fake_llm, resp, tc):
    """run() が model/prompt_versions/summary/failures を含む結果を返す。"""
    golden_path = _run_golden(tmp_path)
    milk_record = {"type": "feeding", "sub_type": "ミルク", "amount": 120}
    llm = fake_llm(
        [
            # e-1: extract 呼び出し1回で records を返す
            resp(tool_calls=[tc("extract_records", {"records": [milk_record]})]),
            # l-1: extract は空、loop で query_records を返す
            resp(tool_calls=[tc("extract_records", {"records": []})]),
            resp(tool_calls=[tc("query_records", {"period": "today"})]),
            # n-1: extract は空、loop もツール未呼び出し
            resp(tool_calls=[tc("extract_records", {"records": []})]),
            resp(content="こんにちは！"),
        ]
    )

    result = run(golden_path=golden_path, client=llm)

    assert set(result.keys()) == {
        "model",
        "prompt_versions",
        "timestamp",
        "golden_set_size",
        "summary",
        "failures",
    }
    assert result["golden_set_size"] == 3
    assert result["prompt_versions"] == {"extract": "v1", "loop": "v1"}
    assert result["summary"]["overall"]["total"] == 3
    assert result["summary"]["overall"]["passed"] == 3
    assert result["failures"] == []


def test_run_model_override_reflected_in_result(tmp_path, fake_llm, resp, tc):
    """model 引数を渡すと config の既定モデルを上書きし、結果に反映される。"""
    golden_path = _run_golden(tmp_path)
    milk_record = {"type": "feeding", "sub_type": "ミルク", "amount": 120}
    llm = fake_llm(
        [
            resp(tool_calls=[tc("extract_records", {"records": [milk_record]})]),
            resp(tool_calls=[tc("extract_records", {"records": []})]),
            resp(tool_calls=[tc("query_records", {"period": "today"})]),
            resp(tool_calls=[tc("extract_records", {"records": []})]),
            resp(content="こんにちは！"),
        ]
    )

    result = run(model="anthropic/claude-haiku-4-5-20251001", golden_path=golden_path, client=llm)

    assert result["model"] == "anthropic/claude-haiku-4-5-20251001"


def test_run_continues_after_case_error(tmp_path, fake_llm, resp, tc):
    """1ケースで例外が起きても他ケースの採点は続行し、当該ケースは失敗として記録される。"""
    golden_path = _run_golden(tmp_path)

    class RaisingLLM:
        """1回目の呼び出しで例外を投げ、以降は正常応答を返すフェイク。"""

        def __init__(self, scripted):
            self.scripted = list(scripted)
            self.calls = 0

        def complete(self, messages, tools=None, tool_choice=None, *, operation="loop"):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("boom")
            return self.scripted.pop(0)

    llm = RaisingLLM(
        [
            # l-1: extract は空、loop で query_records を返す
            resp(tool_calls=[tc("extract_records", {"records": []})]),
            resp(tool_calls=[tc("query_records", {"period": "today"})]),
            # n-1: extract は空、loop もツール未呼び出し
            resp(tool_calls=[tc("extract_records", {"records": []})]),
            resp(content="こんにちは！"),
        ]
    )

    result = run(golden_path=golden_path, client=llm)

    assert result["golden_set_size"] == 3
    # e-1 が例外で失敗し、残り2件は続行して採点されている
    assert result["summary"]["overall"]["total"] == 3
    failures = result["failures"]
    assert len(failures) == 1
    assert failures[0]["id"] == "e-1"
    assert failures[0]["passed"] is False
    assert failures[0]["reason"].startswith("error:RuntimeError")


# ---------------------------------------------------------------------------
# save_result
# ---------------------------------------------------------------------------


def test_save_result_writes_file_and_round_trips_json(tmp_path):
    """保存したファイルが実在し、JSON往復で元の dict と一致する。"""
    result = {
        "model": "ollama_chat/qwen2.5:7b",
        "prompt_versions": {"extract": "v1", "loop": "v1"},
        "timestamp": "2026-07-10T10:00:00+09:00",
        "golden_set_size": 3,
        "summary": {"overall": {"total": 3, "passed": 3}, "by_tag": {}, "false_positive_rate": None},
        "failures": [],
    }
    out_path = save_result(result, out_dir=tmp_path)

    assert out_path.exists()
    loaded = json.loads(out_path.read_text(encoding="utf-8"))
    assert loaded == result


def test_save_result_replaces_slash_in_model_name(tmp_path):
    """モデル名に含まれる `/` はファイル名で `_` に置換される。"""
    result = {
        "model": "anthropic/claude-haiku-4-5-20251001",
        "prompt_versions": {"extract": "v1", "loop": "v1"},
        "timestamp": "2026-07-10T10:00:00+09:00",
        "golden_set_size": 0,
        "summary": {"overall": {"total": 0, "passed": 0}, "by_tag": {}, "false_positive_rate": None},
        "failures": [],
    }
    out_path = save_result(result, out_dir=tmp_path)

    assert "/" not in out_path.name
    assert "anthropic_claude-haiku-4-5-20251001" in out_path.name


def test_save_result_creates_missing_directory(tmp_path):
    """出力先ディレクトリが無ければ作成する。"""
    out_dir = tmp_path / "nested" / "results"
    result = {
        "model": "ollama_chat/qwen2.5:7b",
        "prompt_versions": {"extract": "v1", "loop": "v1"},
        "timestamp": "2026-07-10T10:00:00+09:00",
        "golden_set_size": 0,
        "summary": {"overall": {"total": 0, "passed": 0}, "by_tag": {}, "false_positive_rate": None},
        "failures": [],
    }
    assert not out_dir.exists()
    out_path = save_result(result, out_dir=out_dir)
    assert out_path.exists()
