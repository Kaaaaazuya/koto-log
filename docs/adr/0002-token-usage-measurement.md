# ADR-0002: トークン使用量の最小計測（Langfuse 移行可能な形）

- **日付:** 2026-06-26
- **状態:** 採用（実装済み・P7）

## コンテキスト

本番（Render / Claude Haiku）で「操作回数の割に入力トークンが多い」事象を確認した。
原因の切り分け・改善効果の検証には、まず**どのパスが何トークン使っているか**を
実数で計測できる仕組みが要る。現状はトークン使用量をどこにも記録していない。

トークンを食う構造（`agent/loop.py::handle()`）：

```
ユーザー1発言
 ├─ ① extract_records()      ← 毎回必ず呼ぶ（force tool call）
 │     EXTRACT_SYSTEM + EXTRACT_TOOL スキーマ + 本文
 └─ ② tool-use ループ（最大5回）
       各回 SYSTEM_PROMPT + TOOLS(4スキーマ) + 履歴 + tool結果
```

加えてスケジューラ（毎朝7時カウントダウン・毎晩21時サマリ）も LLM を叩くため、
ユーザー操作ゼロでもトークンが発生する。計測では `extract` / `loop` / `push` を
区別できる必要がある。

### 要件

- 追加インフラ・外部依存ゼロで即日入れられる（個人運用規模に見合う最小実装）
- 後で **Langfuse へ機械的に移行できる**（呼び出し側コードを書き換えない）
- 育児ログ本文（個人情報）を計測ログに含めない（→ [[project-pii-check]] の方針）
- 業界標準（OpenTelemetry GenAI semconv）の属性名に寄せ、移行コストを下げる

## 決定

**自前の最小計測を「単一の計測シーム＋差し替え可能な Sink」で実装する。Langfuse 自体の
導入は後回しにする。**

1. **計測シームは `LLMClient.complete()` の1か所に集約する。** ここが全 LLM 呼び出しの
   唯一の通り道なので、ログ処理を loop.py / extractor.py / push に散らかさない。
2. **出力先（Sink）を Protocol で抽象化する。** 当面の実装は「1行 JSON を標準ログに出す」
   `JsonLogSink` のみ。Langfuse 移行時は `LangfuseSink` を1つ追加するだけで、
   呼び出し側は無変更。
3. **計測スキーマは Langfuse / OTel GenAI に対応づく形にする**（後述）。
   - 1回の `handle()`（または1 push ジョブ）= **1 トレース**（`trace_id`）
   - 1回の LLM 呼び出し = **1 ジェネレーション**（`operation`: extract / loop / push）
4. **当面は DB 永続化しない。** Render のログに 1行 JSON を吐き、必要になったら
   `usage_log` テーブル化（または Langfuse 移行）する。

## 理由

- 計測点が1か所なので、抜け漏れなく全呼び出しを捕捉でき、後の改修も局所で済む
- Sink 抽象化により「いま JSON ログ → あとで Langfuse」がコード差分最小で行える
- トレース／ジェネレーションの2階層を最初から持つので、Langfuse のデータモデル
  （Trace → Generation）にそのまま写る
- OTel 準拠の属性名にしておけば、将来 Datadog 等へ移す場合も機械的

## 却下した案の理由

**案: 最初から Langfuse を導入する。**
セルフホスト（GCP/コンテナ）または SaaS の運用が増える。個人運用規模では現状オーバー
スペック。まず実数を見てから判断したい。ただし本 ADR の設計は**いつでも移行できる**形に
してある。

**案: 自前で `usage_log` テーブルに永続化＋ /admin にグラフ。**
価値はあるが、Langfuse がいずれ担う領域と重複する。最小実装の段階では JSON ログで十分
（Render がログを保持する）。永続化は「移行しない」と決めた場合の次段とする。

**案: litellm の組み込みコールバック（`litellm.callbacks`）に寄せる。**
キャッシュ等プロバイダ固有フックは拾えるが、`complete()` ラッパで戻り値を読めば
同じことが追加機構なしでできる。最小構成を優先しインラインで捕捉する。

**案: 計測しない／コンソールのダッシュボードだけ見る。**
Anthropic コンソール（`platform.claude.com/usage` と `/usage/cache`）は一次切り分けに
有用なので**併用する**。ただしアプリ内訳（extract / loop / push 別）は出せないため、
自前計測は別途必要。

## トレードオフ・リスク

- JSON ログは Render のログ保持期間に依存し、長期集計には向かない（移行/永続化で解決）
- `litellm.completion_cost()` の単価表が新モデルに追随していない場合、コストが 0 や
  不正確になりうる → 失敗時は `cost_usd: null` とし、トークン数は必ず残す
- キャッシュ関連トークンはプロバイダ差があるため、欠損は 0 埋めで防御的に取得する
- トレース紐付けに contextvar を使う。`asyncio.to_thread`（webhook 経由）でも
  `copy_context` により伝播するが、新規スレッド/プロセスでは引き継がれない点に注意

---

## 実装計画（別セッションで着手可能）

### 計測スキーマ（1イベント = 1 LLM 呼び出し）

OTel GenAI / Langfuse に対応づくフィールド。**本文・引数値は含めない。**

| フィールド | 由来(litellm) | OTel/Langfuse 対応 | 備考 |
|---|---|---|---|
| `trace_id` | 自前(uuid) | Trace | 1 handle / 1 push = 1 |
| `operation` | 自前 | Generation name | `extract` / `loop` / `push` |
| `model` | `response.model` | `gen_ai.response.model` | |
| `input_tokens` | `usage.prompt_tokens` | `gen_ai.usage.input_tokens` | |
| `output_tokens` | `usage.completion_tokens` | `gen_ai.usage.output_tokens` | |
| `total_tokens` | `usage.total_tokens` | — | 欠損時は in+out |
| `cache_read_input_tokens` | `usage.prompt_tokens_details.cached_tokens` | — | 防御的取得・0埋め |
| `cache_creation_input_tokens` | `usage.cache_creation_input_tokens` | — | 防御的取得・0埋め |
| `cost_usd` | `litellm.completion_cost(response)` | — | 失敗時 null |
| `ts` | 自前 | timestamp | ISO8601(JST) |

### 設計（差し替え可能 Sink）

新規 `src/kotolog/obs/usage.py`（イメージ）：

```python
from typing import Protocol
from dataclasses import dataclass, asdict

@dataclass
class UsageEvent:
    trace_id: str
    operation: str          # "extract" | "loop" | "push"
    model: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cache_read_input_tokens: int
    cache_creation_input_tokens: int
    cost_usd: float | None
    ts: str

class UsageSink(Protocol):
    def record(self, event: UsageEvent) -> None: ...

class NullSink:               # デフォルト（計測オフ時）
    def record(self, event): pass

class JsonLogSink:            # 当面の実装：1行 JSON をログ出力
    def record(self, event):
        logger.info("usage %s", json.dumps(asdict(event), ensure_ascii=False))

# 移行時に追加するだけ（呼び出し側は無変更）:
# class LangfuseSink: ...  trace_id→trace, operation→generation, usage/cost をそのまま送る
```

`LLMClient` 側の捕捉（`complete()` の戻り値直後、防御的に getattr）：

```python
resp = litellm.completion(**kwargs)
self._sink.record(_build_event(resp, operation=operation, trace_id=current_trace_id()))
return resp
```

- `operation` は `complete(..., operation="extract")` で受ける（extractor / loop / push が指定）
- `trace_id` は `Agent.handle()` と各 push ジョブで contextvar にセット → `complete()` が読む
  （明示引数で渡す実装でも可。contextvar の方が呼び出し側が綺麗）

### 結線

- `cli.py::build_agent()` で `KOTOLOG_USAGE_LOG=1` のとき `JsonLogSink` を、未設定なら
  `NullSink` を `LLMClient` に注入する（`config.py` に `usage_log: bool` 追加）
- `agent/loop.py::handle()` 冒頭で `trace_id` を発行・セット。`extract_records` 呼び出しに
  `operation="extract"`、ループ内 `complete` に `operation="loop"` を渡す
- push 系（`scheduler.py` / `push.py` の LLM 呼び出し）に `operation="push"` とトレース発行

### タスク分解

| ID | 内容 | 完了条件 |
|---|---|---|
| 1 | `obs/usage.py`: `UsageEvent` / `UsageSink` / `NullSink` / `JsonLogSink` | 単体テストで JSON 整形を検証 |
| 2 | `LLMClient.complete()` に `operation` 引数＋使用量捕捉＋ Sink 通知 | FakeLLM が usage 付き応答を返し、Sink が正しいイベントを受ける |
| 3 | `handle()`／push にトレース発行・operation タグ付与 | 1 save＝extract1件、1 query＝extract+loop複数件がタグ別に出る |
| 4 | `config.py` に `usage_log` 追加・`build_agent` で Sink 注入 | `KOTOLOG_USAGE_LOG=1` で出力、未設定で無出力 |
| 5 | テスト（FakeLLM に usage を載せる／PII 非混入の確認） | `operation`・トークン数を検証、本文がイベントに**無い**ことを検証 |
| 6 | README・開発計画・`.env.example` 更新（`/sync-docs`） | ドキュメント反映（→ [[feedback-sync-docs]]） |

### テスト方針（既存 FakeLLM パターンを拡張）

- `tests/conftest.py` の `make_resp` に `usage`（prompt/completion/total）を載せられるよう拡張
- 記録系: `handle()` 1回で `operation="extract"` のイベントが1件
- 集計系: `extract` 1件＋`loop` 複数件が出ることを検証
- **PII**: `UsageEvent` のどのフィールドにもユーザー本文・引数値が含まれないことを assert
- `cost_usd` 取得失敗時に `None` でイベントが成立すること

### 受け入れ条件（Definition of Done）

1. `KOTOLOG_USAGE_LOG=1` でローカル実行すると、save / query で operation 別の 1行 JSON が出る
2. トークン数・モデル・cost（取得できれば）が記録される
3. 計測ログに育児ログ本文・引数値が一切含まれない
4. Langfuse 移行は `LangfuseSink` 追加＋環境変数のみで、呼び出し側コード無変更で可能

## 参考

- LiteLLM: completion_cost / usage — https://docs.litellm.ai/docs/completion/token_usage
- OpenTelemetry GenAI semconv（`gen_ai.usage.*`）— https://opentelemetry.io/docs/specs/semconv/gen-ai/
- Anthropic Prompt Caching（`cache_read_input_tokens` 等）— https://platform.claude.com/docs/en/build-with-claude/prompt-caching
- Langfuse Token & Cost Tracking — https://langfuse.com/docs/observability/features/token-and-cost-tracking
- 国内本番事例（ZOZO / Langfuse 導入）— https://techblog.zozo.com/entry/llmops-observability-with-langfuse
