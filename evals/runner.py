"""E1-3/E2-1: evals専用の実行基盤。

本番と同じプロンプト（kotolog.agent.extractor / kotolog.agent.loop）・ツール定義
（kotolog.tools.definitions.TOOLS）を使い回し、ゴールデンセット
（evals/golden/utterances.yaml）に対する採点（evals/scoring.py）を実行する。
実行結果はプロンプトバージョン・モデル・スコア・失敗ケース一覧に加え、
コスト・トークン数・レイテンシ（E2-1: モデル比較）を含む JSON として保存する。

実行:
    uv run python -m evals.runner
    uv run python -m evals.runner --model anthropic/claude-haiku-4-5-20251001
"""

from __future__ import annotations

import argparse
import json
import pathlib
import time
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
from kotolog.obs.usage import ListSink
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


def _latency_stats(values: list[float]) -> dict:
    """レイテンシ値（ms）の avg/p50/max/count を計算する。空なら全て None/0。"""
    if not values:
        return {"avg_latency_ms": None, "p50_latency_ms": None, "max_latency_ms": None, "count": 0}
    srt = sorted(values)
    n = len(srt)
    avg = sum(srt) / n
    mid = n // 2
    p50 = srt[mid] if n % 2 == 1 else (srt[mid - 1] + srt[mid]) / 2
    return {
        "avg_latency_ms": round(avg, 3),
        "p50_latency_ms": round(p50, 3),
        "max_latency_ms": round(srt[-1], 3),
        "count": n,
    }


def _resolve_sink(client, sink) -> ListSink | None:
    """コスト集計に使う ListSink を決める。

    明示的な sink があればそれを使う。無ければ client が公開する `_sink`
    （LLMClient 互換で ListSink が注入されていれば）を読む。どちらも無ければ
    None（コスト集計は 0/None のまま）。
    """
    if sink is not None:
        return sink
    candidate = getattr(client, "_sink", None)
    return candidate if isinstance(candidate, ListSink) else None


def run(
    model: str | None = None,
    golden_path: pathlib.Path = GOLDEN_PATH,
    now: datetime | None = None,
    client=None,
    sink: ListSink | None = None,
) -> dict:
    """ゴールデンセット全件を採点し、結果サマリ（コスト・レイテンシ込み）を組み立てる。

    client を渡さなければ config から LLMClient を組み立てる。テストでは
    FakeLLM 等を注入して実 LLM 呼び出しなしに run() 全体を検証できる。

    sink（E2-1）: コスト・トークン集計に使う ListSink。client を渡さない場合は
    ここで作った（または渡された）ListSink を自前の LLMClient へ注入する。client を
    渡す場合、sink を明示しなければ client._sink（ListSink であれば）を自動検出する。
    どちらも無ければコスト集計は 0/None のまま失敗しない（FakeLLM 注入時など）。
    """
    config = load_config()
    if model:
        config = replace(config, model=model)
    if client is None:
        sink = sink or ListSink()
        client = LLMClient(config, sink=sink)
    else:
        sink = _resolve_sink(client, sink)
    extract_fn = build_extract_fn(client)
    loop_fn = build_loop_fn(client)
    now = now or datetime.now(JST)

    cases = load_golden_cases(golden_path)
    results = []
    all_latencies: list[float] = []
    stage_latencies: dict[str, list[float]] = {}
    for case in cases:
        prior_event_count = len(sink.events) if sink is not None else 0
        start = time.perf_counter()
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
        latency_ms = (time.perf_counter() - start) * 1000

        case_cost_usd = None
        case_tokens = None
        if sink is not None:
            case_events = sink.events[prior_event_count:]
            case_cost_usd = sum((ev.cost_usd or 0) for ev in case_events)
            case_tokens = sum(ev.total_tokens for ev in case_events)

        result = {
            **result,
            "latency_ms": round(latency_ms, 3),
            "cost_usd": case_cost_usd,
            "tokens": case_tokens,
        }
        results.append(result)

        all_latencies.append(latency_ms)
        stage = result.get("stage")
        if stage:
            stage_latencies.setdefault(stage, []).append(latency_ms)

    summary = aggregate_results(results)
    failures = [r for r in results if not r["passed"]]

    call_count = len(sink.events) if sink is not None else 0
    if sink is not None and sink.events:
        total_input_tokens = sum(ev.input_tokens for ev in sink.events)
        total_output_tokens = sum(ev.output_tokens for ev in sink.events)
        total_tokens = sum(ev.total_tokens for ev in sink.events)
        total_cost_usd = sum((ev.cost_usd or 0) for ev in sink.events)
    else:
        total_input_tokens = 0
        total_output_tokens = 0
        total_tokens = 0
        total_cost_usd = None

    overall_latency = _latency_stats(all_latencies)
    cost = {
        "total_cost_usd": total_cost_usd,
        "total_input_tokens": total_input_tokens,
        "total_output_tokens": total_output_tokens,
        "total_tokens": total_tokens,
        "call_count": call_count,
        "avg_latency_ms": overall_latency["avg_latency_ms"],
        "p50_latency_ms": overall_latency["p50_latency_ms"],
        "max_latency_ms": overall_latency["max_latency_ms"],
        "by_stage": {stage: _latency_stats(values) for stage, values in stage_latencies.items()},
    }

    return {
        "model": config.model,
        "prompt_versions": PROMPT_VERSIONS,
        "timestamp": datetime.now(JST).isoformat(),
        "golden_set_size": len(cases),
        "summary": summary,
        "failures": failures,
        "cost": cost,
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

    cost = result["cost"]
    cost_str = f"${cost['total_cost_usd']:.4f}" if cost["total_cost_usd"] is not None else "N/A"
    print(f"\ncost: {cost_str}  calls={cost['call_count']}")
    print(
        f"tokens: in={cost['total_input_tokens']} out={cost['total_output_tokens']} "
        f"total={cost['total_tokens']}"
    )
    print(
        f"latency_ms: avg={cost['avg_latency_ms']} p50={cost['p50_latency_ms']} max={cost['max_latency_ms']}"
    )
    for stage, stats in sorted(cost["by_stage"].items()):
        print(
            f"  {stage}: avg={stats['avg_latency_ms']} p50={stats['p50_latency_ms']} "
            f"max={stats['max_latency_ms']} (n={stats['count']})"
        )
    print(f"\nsaved: {out_path}")


if __name__ == "__main__":
    main()
