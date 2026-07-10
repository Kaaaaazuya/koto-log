# コスト計測とコスト予測（Issue #68 / ADR-0002）

出産後の実運用負荷（1日20〜30記録想定）で API コストがどの程度になるかを見積もる
ための、**計測の仕組み**と**予測の方法（テンプレート）**をまとめる。

このドキュメントは方法論であり、実測値の確定版ではない。**具体的な単価・トークン数は
TODO**（→ 後述「TODO: 実測待ち」、Issue #67 で実際の Haiku/Sonnet 実行結果を取得してから
埋める）。ここに書く数値はすべて構造を示すためのプレースホルダである。

## 1. 計測の仕組み（実装済み・Issue #68）

```
LLMClient.complete()  ← 全 LLM 呼び出しの唯一の通り道（ADR-0002）
        │
        ▼
   UsageEvent 組み立て（trace_id / operation / model / tokens / cost_usd / ts）
        │
        ▼
   Sink（環境変数で選択・fan-out 可能）
     - KOTOLOG_USAGE_LOG=1 → JsonLogSink（標準ログへ1行JSON）
     - KOTOLOG_USAGE_DB=1  → DbSink（`usage_log` テーブルへ永続化。マイグレーション0006）
```

- `usage_log` テーブルは `trace_id` / `operation`（extract・loop・push）/ `model` /
  トークン数各種 / `cost_usd` / `ts` のみを持つ。**育児ログ本文・line_user_id 等の
  PII は一切保存しない**（[[project-pii-check]] 準拠、ADR-0002 のスキーマ表と同一）。
- `cost_usd` は `litellm.completion_cost()` 由来。単価表が新モデルに追随していない
  場合は `NULL`（0 として集計）になりうる（ADR-0002 記載のリスク）。
- 月次集計は `crud.monthly_usage_summary(conn, "YYYY-MM")` が担う。**世帯全体の合計**
  （「今月の家族の育児記録にかかった API コスト」）を返し、ユーザー別内訳は持たない
  （個人利用規模につき不要、PII 最小化の方針にも合致）。
- CLI から確認する:

  ```bash
  # KOTOLOG_USAGE_DB=1 で運用しているDBに対して実行
  uv run python -m kotolog.usage_report --month 2026-07
  ```

  出力は合計コスト・トークン数・呼び出し回数に加え、`operation` 別（extract/loop/push）・
  `model` 別の内訳を含む。

## 2. コスト予測の方法（テンプレート）

### 2.1 基本式

```
月間コスト(USD)
  = Σ_operation ( 1メッセージあたりの平均トークン数[operation]
                   × 単価(USD / 1M tokens)[operation, token種別]
                   × 1日あたりの発生回数[operation]
                   × 30日 )
```

`operation` は `extract`（構造化抽出、毎メッセージ必ず1回）・`loop`（tool-use ループ、
0〜数回）・`push`（朝カウントダウン・夜サマリー、1日2回固定）の3種。

出産後の想定利用量:

| 変数 | 想定値 | 根拠 |
|---|---|---|
| 1日あたりの育児記録メッセージ数 | 20〜30件 | 課題設定（授乳・おむつ・睡眠等の頻度から） |
| 1メッセージあたりの LLM 呼び出し回数 | 1（extract）+ 0〜2（loop） | ADR-0002 のトークン消費構造。多くは extract 1回で完結 |
| push 呼び出し | 2回/日（朝・夜、固定） | スケジューラー仕様（P5） |

### 2.2 ワークシート（埋める順）

1. `uv run python -m kotolog.usage_report --month <実運用した月>` を実行し、
   `operation` 別の「平均 input/output トークン数」「呼び出し回数」を得る
   （`= (by_operation[op].input_tokens) / (by_operation[op].calls)` 等で算出）。
2. 使用モデルの単価表（Anthropic 公式 pricing、または `litellm` の内蔵単価）から
   `USD / 1M input tokens`・`USD / 1M output tokens` を確認する。
3. 上記の基本式に、1日の想定メッセージ数（20〜30件）を掛けて月間コストを算出する。
4. `cost_usd`（`litellm.completion_cost()` 実測値）が取得できていれば、その月の
   実測合計と 3. の予測値を突き合わせて予測式の精度を検証する。

### 2.3 ワークサンプル（プレースホルダ数値・TODO差し替え前提）

**注意: 以下はすべて仮の数値であり、実測ではない。** 単価・トークン数は
`<PLACEHOLDER>` を実測値に置き換えて使うこと。

| operation | 平均 input tokens/回 | 平均 output tokens/回 | 呼び出し回数/日 |
|---|---|---|---|
| extract | `<PLACEHOLDER_INPUT_TOKENS_EXTRACT>`（例: 800） | `<PLACEHOLDER_OUTPUT_TOKENS_EXTRACT>`（例: 80） | 25（=1メッセージ1回想定） |
| loop | `<PLACEHOLDER_INPUT_TOKENS_LOOP>`（例: 1500） | `<PLACEHOLDER_OUTPUT_TOKENS_LOOP>`（例: 100） | 10（=メッセージの4割で発生と仮定） |
| push | `<PLACEHOLDER_INPUT_TOKENS_PUSH>`（例: 300） | `<PLACEHOLDER_OUTPUT_TOKENS_PUSH>`（例: 50） | 2（固定） |

単価（プレースホルダ。実際の Haiku/Sonnet 単価に差し替えること）:

| 項目 | 値 |
|---|---|
| input 単価 | `<PLACEHOLDER_PRICE_INPUT_PER_1M_USD>`（例: $1.00 / 1M tokens） |
| output 単価 | `<PLACEHOLDER_PRICE_OUTPUT_PER_1M_USD>`（例: $5.00 / 1M tokens） |

上記の「例」の数値をそのまま当てはめた場合の**参考計算**（実測ではない）:

```
extract: (800×$1.00 + 80×$5.00) / 1,000,000 × 25回/日 × 30日
        = (800 + 400) / 1,000,000 × 750
        = 0.0012 × 750 ≈ $0.90 / 月

loop:    (1500×$1.00 + 100×$5.00) / 1,000,000 × 10回/日 × 30日
        = (1500 + 500) / 1,000,000 × 300
        = 0.002 × 300 = $0.60 / 月

push:    (300×$1.00 + 50×$5.00) / 1,000,000 × 2回/日 × 30日
        = (300 + 250) / 1,000,000 × 60
        = 0.00055 × 60 ≈ $0.03 / 月

合計 ≈ $1.53 / 月（プレースホルダ数値による参考値。実測ではない）
```

この参考計算は「式の使い方」を示すためのものであり、**実際のコストを表すものではない**。
個人・家族運用規模（1世帯）を想定しているため、たとえ実測が数倍ずれても月額は
低コストに収まる可能性が高いと考えられるが、これも実測前の推測に留まる。

## 3. TODO: 実測待ち

- [ ] Issue #67（実際の Claude Haiku/Sonnet を用いた evals 実行）の結果から、
      `operation` 別の実測トークン数・単価を取得し、本ドキュメントの
      「2.3 ワークサンプル」を実測値で置き換える。
- [ ] `KOTOLOG_USAGE_DB=1` を本番（Render + Turso）で最低1ヶ月有効化し、
      `uv run python -m kotolog.usage_report --month <該当月>` の実測合計と
      本ドキュメントの予測式を突き合わせて精度を検証する。
- [ ] 実測後、必要であれば P13.3（トークンコストの軽量可観測・モデル/プロンプト
      最適化、開発計画.md 参照）でモデル選定・プロンプト圧縮の要否を判断する。

## 4. 関連ドキュメント

- [ADR-0002: トークン使用量の最小計測](adr/0002-token-usage-measurement.md)（計測方式の設計判断）
- `src/kotolog/obs/usage.py`（UsageEvent / Sink 実装）
- `src/kotolog/db/migrations.py`（`usage_log` テーブル、マイグレーション0006）
- `src/kotolog/db/crud.py::monthly_usage_summary`（月次集計）
- `src/kotolog/usage_report.py`（CLI レポート）
