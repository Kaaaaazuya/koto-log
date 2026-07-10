# eval-results: モデル比較の実行結果置き場（E2-1）

[Issue #67 [E2-1] モデル比較](https://github.com/Kaaaaazuya/koto-log/issues/67) の成果物置き場。
`evals/runner.py`（コスト・レイテンシ計測付き、E2-1で追加）と `evals/compare.py`
（結果JSON同士のMarkdown比較、E2-1で追加）を使って Haiku ⇔ Sonnet の精度×コスト×
レイテンシを実測し、比較結果をこのディレクトリに残す（ブログ素材にもする想定）。

## 現状（このPR時点）

**実測値はまだ入っていない。** このPRで届けるのは計測の「機械」（`evals/runner.py` の
コスト/レイテンシ計測、`evals/compare.py` の比較Markdown生成）とこのテンプレートまで。
Haiku・Sonnet を実際に叩くには `KOTOLOG_API_KEY`（Anthropic APIキー）が要るが、この
実装環境には用意されていないため、実行はスコープ外とした。**キーが使える環境で
下記コマンドを叩けば、実測→比較Markdown生成は一本道（one-liner の組み合わせ）で終わる。**

## 実行手順（キーが使える環境で）

```bash
# 1. Haiku で全golden（85件）を採点し、evals/results/ にJSON保存
uv run python -m evals.runner --model anthropic/claude-haiku-4-5-20251001

# 2. Sonnet でも同様に
uv run python -m evals.runner --model anthropic/claude-sonnet-4-5-20250929

# 3. 2つの結果JSONを比較Markdownにする
uv run python -m evals.compare \
  evals/results/<timestamp>_anthropic_claude-haiku-4-5-20251001.json \
  evals/results/<timestamp>_anthropic_claude-sonnet-4-5-20250929.json \
  --out docs/eval-results/haiku-vs-sonnet.md
```

`evals/runner.py` の結果JSONには `summary`（正答率・タグ別・誤発火率）に加えて
`cost`（`total_cost_usd` / トークン数 / `call_count` / `avg_latency_ms` ・ `p50_latency_ms` ・
`max_latency_ms` ・ステージ別レイテンシ）が入る。Anthropic モデルは litellm の単価表に
乗っているため `total_cost_usd` が実額で埋まる想定（Ollama 等の未対応モデルは `null`）。

## コスト計測の仕組み（E2-2 と共有）

`evals/runner.py` は `LLMClient` に `kotolog.obs.usage.ListSink`（本PRで追加、
[docs/adr/0002](../adr/0002-token-usage-measurement.md) の Sink 抽象の実装の1つ）を注入し、
ケースごとに「呼び出し前後のイベント数の差分」でコスト・トークンを帰属させている。
本番運用でのコスト可視化（[Issue #68 [E2-2] コスト計測](https://github.com/Kaaaaazuya/koto-log/issues/68)、
メッセージ単位で Turso に記録・月次集計）とは目的が異なるが、**捕捉の一次ソースは
同じ `UsageEvent` / `UsageSink`**（[docs/adr/0002](../adr/0002-token-usage-measurement.md)）
なので、E2-2 で `TursoSink` のようなものを足す際もこの evals 計測の実装がそのまま参考になる。

## タスク別使い分けの分析（実測後にやること）

Issue #67 の「まとめ入力（複数レコード一括）だけSonnetに振る等、タスク別使い分けの
損益分岐を検討」に対応する分析テンプレート。実測値が揃ったら以下を埋める。

1. **タグ別の精度差** — `evals/compare.py` が出すタグ別正答率表で、Haiku と Sonnet の差が
   大きいタグ（例: `multi_child` や長文の一括抽出系）を洗い出す。
2. **コスト差分** — `cost.total_cost_usd` の差（Sonnet / Haiku 比）と、該当タグの発話数の
   実利用比率（1日20〜30記録想定、[Issue #68](https://github.com/Kaaaaazuya/koto-log/issues/68) 参照）から、
   「そのタグだけSonnetに振った場合の月額差分」を試算する。
3. **損益分岐** — 2の追加コストが「精度改善で防げる誤りの実害（記録漏れ・誤記録の手直しコスト）」
   に見合うかを判断する。見合うタグがあれば、`agent/loop.py` や `agent/extractor.py` で
   タスク種別ごとにモデルを切り替える設計（`LLMClient` のモデル文字列をリクエスト単位で
   上書きできるようにする、等）を別Issueとして起こす。
4. 結論は本ファイルまたは `docs/eval-results/haiku-vs-sonnet.md` に追記し、
   「出産後の想定利用でも月額◯円、この精度」（E2 完了の定義）に繋げる。

## ファイル一覧

| ファイル | 内容 |
|---|---|
| `README.md`（本ファイル） | 手順・現状・分析テンプレート |
| `haiku-vs-sonnet.md`（未生成） | 実測後に `evals/compare.py --out` で生成される比較Markdown |
