"""E1-3: evals専用の実行基盤。

本番と同じプロンプト（kotolog.agent.extractor / kotolog.agent.loop）・ツール定義
（kotolog.tools.definitions.TOOLS）を使い回し、ゴールデンセット
（evals/golden/utterances.yaml）に対する採点（evals/scoring.py）を実行する。
実行結果はプロンプトバージョン・モデル・スコア・失敗ケース一覧を含む JSON として保存する。

実行:
    uv run python -m evals.runner
    uv run python -m evals.runner --model anthropic/claude-haiku-4-5-20251001
"""

from __future__ import annotations

import argparse
import json
import pathlib
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import yaml

from evals.scoring import aggregate_results, score_case
from kotolog.agent.extractor import PROMPT_VERSION as EXTRACT_PROMPT_VERSION
from kotolog.agent.extractor import extract_records
from kotolog.agent.loop import PROMPT_VERSION as LOOP_PROMPT_VERSION
from kotolog.agent.loop import SYSTEM_PROMPT, _extract_calls
from kotolog.config import load_config
from kotolog.llm.client import LLMClient
from kotolog.tools.definitions import TOOLS

JST = timezone(timedelta(hours=9))

GOLDEN_PATH = pathlib.Path(__file__).parent / "golden" / "utterances.yaml"
RESULTS_DIR = pathlib.Path(__file__).parent / "results"

# extractor.py / loop.py の PROMPT_VERSION をそのまま参照する（二重管理を避け、
# 評価結果のメタデータが本番で実際に読み込まれているバージョンと乖離しないようにする）。
PROMPT_VERSIONS = {"extract": EXTRACT_PROMPT_VERSION, "loop": LOOP_PROMPT_VERSION}


def load_golden_cases(path: pathlib.Path = GOLDEN_PATH) -> list[dict]:
    """ゴールデンセット（good/hard/multi_child の全ケース）を読み込む。"""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [*data["good_cases"], *data["hard_cases"], *data["multi_child_cases"]]


def build_extract_fn(client: LLMClient):
    """score_case が期待する extract_fn（text -> (records, child_name)）を返す。"""

    def extract_fn(text: str):
        return extract_records(text, client)

    return extract_fn


def build_loop_fn(client: LLMClient):
    """score_case が期待する loop_fn（text -> (tool_name, args) | None）を返す。

    本番の TOOLS ループと同じ SYSTEM_PROMPT・TOOLS で1回だけ問い合わせ、
    実行はせず最初のツール呼び出しの意思決定のみを返す
    （evals/tool_selection.py と同じ流儀）。
    """

    def loop_fn(text: str):
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ]
        resp = client.complete(messages, tools=TOOLS, operation="loop")
        calls = _extract_calls(resp.choices[0].message)
        if not calls:
            return None
        call = calls[0]
        return call.name, call.args

    return loop_fn


def run(
    model: str | None = None,
    golden_path: pathlib.Path = GOLDEN_PATH,
    now: datetime | None = None,
    client=None,
) -> dict:
    """ゴールデンセット全件を採点し、結果サマリを組み立てる。

    client を渡さなければ config から LLMClient を組み立てる。テストでは
    FakeLLM 等を注入して実 LLM 呼び出しなしに run() 全体を検証できる。
    """
    config = load_config()
    if model:
        config = replace(config, model=model)
    if client is None:
        client = LLMClient(config)
    extract_fn = build_extract_fn(client)
    loop_fn = build_loop_fn(client)
    now = now or datetime.now(JST)

    cases = load_golden_cases(golden_path)
    results = []
    for case in cases:
        try:
            result = score_case(case, extract_fn, loop_fn, now=now)
        except Exception as e:  # noqa: BLE001 - 1ケースの実LLM呼び出し失敗で全体を止めない
            result = {
                "id": case["id"],
                "tags": case.get("tags", []),
                "stage": case.get("expected", {}).get("stage"),
                "passed": False,
                "false_positive": False,
                "reason": f"error:{type(e).__name__}: {e}",
            }
        results.append(result)

    summary = aggregate_results(results)
    failures = [r for r in results if not r["passed"]]

    return {
        "model": config.model,
        "prompt_versions": PROMPT_VERSIONS,
        "timestamp": datetime.now(JST).isoformat(),
        "golden_set_size": len(cases),
        "summary": summary,
        "failures": failures,
    }


def save_result(result: dict, out_dir: pathlib.Path = RESULTS_DIR) -> pathlib.Path:
    """結果 JSON を `<timestamp>_<model>.json` として保存し、そのパスを返す。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = result["timestamp"].replace(":", "").replace("-", "").split("+")[0].split(".")[0]
    model_slug = result["model"].replace("/", "_").replace(":", "_")
    out_path = out_dir / f"{ts}_{model_slug}.json"
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(description="koto-log evals: ゴールデンセット採点ランナー")
    parser.add_argument("--model", default=None, help="KOTOLOG_MODEL を上書き")
    parser.add_argument("--golden", default=None, help="ゴールデンセットYAMLのパス（既定: evals/golden/）")
    parser.add_argument("--out", default=None, help="結果JSONの保存先ディレクトリ（既定: evals/results/）")
    args = parser.parse_args()

    golden_path = pathlib.Path(args.golden) if args.golden else GOLDEN_PATH
    result = run(model=args.model, golden_path=golden_path)

    out_dir = pathlib.Path(args.out) if args.out else RESULTS_DIR
    out_path = save_result(result, out_dir)

    summary = result["summary"]
    overall = summary["overall"]
    print(f"model={result['model']}  prompt_versions={result['prompt_versions']}")
    print(f"overall: {overall['passed']}/{overall['total']} passed")
    if summary["false_positive_rate"] is not None:
        print(f"false_positive_rate (stage=none): {summary['false_positive_rate']:.1%}")
    print("by_tag:")
    for tag, stats in sorted(summary["by_tag"].items()):
        print(f"  {tag}: {stats['passed']}/{stats['total']}")
    if result["failures"]:
        print(f"\nfailures ({len(result['failures'])}):")
        for f in result["failures"]:
            print(f"  {f['id']}: {f['reason']}")
    print(f"\nsaved: {out_path}")


if __name__ == "__main__":
    main()
