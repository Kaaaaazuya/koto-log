# koto-log — 育児記録エージェント（LINE × Tool Use）

授乳・睡眠・おむつなどの育児記録を、**自然言語の対話だけ**で記録・集計・修正できる
エージェント。「3時に120ml飲んだ」と打てば構造化して保存し、「今日何回飲んだ？」と
聞けば集計して返す。中核は LLM の **Tool Use**：入力に応じて LLM が「どのツールを・
どの引数で呼ぶか」を判断し、アプリ側のコードが DB を更新・参照する。

> ステータス: **MVP（CLI で記録・集計・修正・取り消しが動作）**。LINE 連携・本番
> デプロイは未実装（[ロードマップ](#ロードマップ)参照）。設計は
> [育児記録エージェント_DesignDoc.md] / [開発計画.md](開発計画.md) を参照。

## できること（現状）

- 自由文を解釈して `feeding / sleep / diaper` を構造化保存（例: 「3時にミルク120ml」）
- 期間・種別を指定した集計に応答（例: 「今日は何回飲んだ？」）
- 直近記録の修正・取り消し（例: 「150に直して」「さっきのなし」）
- 書き込み後は確認サマリを返し、情報不足なら聞き返す
- ローカルLLM（Ollama）で完全無料動作。`KOTOLOG_MODEL` の変更だけで Claude へ切替可能

## アーキテクチャ

```
ユーザー入力（現状: CLI）
        │
        ▼
   Agent ループ（agent/loop.py）
     ├─ LLM クライアント（llm/client.py, LiteLLM: local⇄Claude 切替）
     └─ ツール実行（tools/executor.py）: save / query / update_or_delete
        │   └─ 時刻正規化（utils/timeparse.py）: 「さっき/3時/お昼」→ JST絶対時刻
        ▼
     DB（db/, SQLite。本番は Turso/libSQL を想定）
```

LLM はツールを「選ぶ」だけ。実際の DB 操作・時刻解決はアプリ側コードが行うため、
ツール定義（JSONスキーマ）と実行コードはモデル非依存に保たれている。

## プロジェクト構成

```
src/kotolog/
├── config.py          # .env からの設定読込（model/APIキー/DB URL）
├── db/                # connection・crud・schema.sql（children/records/sessions）
├── utils/timeparse.py # 相対時刻 → JST絶対時刻
├── tools/             # definitions(JSONスキーマ) / executor(DB操作マッピング)
├── llm/client.py      # LiteLLM ラッパ（local⇄Claude）
├── agent/loop.py      # tool-use ループ＋確認サマリ＋フォールバック
└── cli.py             # 対話CLI エントリ
evals/tool_selection.py # ツール選択の正答率を測る評価スクリプト
tests/                 # unit / integration / e2e
```

## セットアップ

### 前提
- [uv](https://docs.astral.sh/uv/)（パッケージ管理）
- Docker（ローカルLLM の Ollama 用）

### 手順

```bash
# 1. 依存をインストール
uv sync

# 2. Ollama を Docker で起動（モデルは docker_ollama ボリュームを共有）
docker run -d --name kotolog-ollama -p 11434:11434 \
  -v docker_ollama:/root/.ollama ollama/ollama:latest
docker exec kotolog-ollama ollama pull qwen2.5:7b   # 未取得の場合

# 3. 設定ファイルを用意
cp .env.example .env        # 必要に応じて編集

# 4. 起動
uv run kotolog
```

```
koto-log CLI (model=ollama_chat/qwen2.5:7b) — 終了は Ctrl-D / 'quit'
> 3時にミルク120ml飲んだ
ミルク120mlを3時に記録しました。
> 今日は何回飲んだ？
今日は1回、合計120mlです。
```

## 設定（環境変数）

| 変数 | 既定 | 説明 |
|---|---|---|
| `KOTOLOG_MODEL` | `ollama_chat/qwen2.5:7b` | LiteLLM のモデル文字列。本番例: `claude-3-5-haiku-latest` |
| `KOTOLOG_API_KEY` | （空） | ホスト型モデル用 APIキー（ローカルでは不要） |
| `KOTOLOG_OLLAMA_BASE` | `http://localhost:11434` | Ollama のベースURL（ローカル時のみ使用） |
| `KOTOLOG_DB_URL` | `kotolog.db` | DB URL。本番例: `libsql://...turso.io` |
| `KOTOLOG_DEFAULT_CHILD` | `baby` | 子の別名（実名は保持しない方針） |

## テスト

3 層に分けて配置し、フォルダから対応マーカー（`unit`/`integration`/`e2e`）を自動付与する。

| 層 | 置き場所 | 内容 |
|---|---|---|
| 単体 (unit) | `tests/unit/` | 純ロジック。DB/ネットワーク非依存（config・時刻正規化・LLMラッパはモック） |
| 結合 (integration) | `tests/integration/` | 実DB・複数コンポーネント結線（CRUD・executor・agentループ・CLI結線） |
| E2E (e2e) | `tests/e2e/` | 入口からの一気通し（決定論版＋実Ollama版） |

```bash
uv run pytest                 # 高速スイート（live は自動スキップ）
uv run pytest -m unit         # 層を選んで実行
uv run pytest -m integration
uv run pytest -m e2e
uv run pytest -m live         # 実Ollama E2E（要・Ollama起動）
```

- `e2e/test_cli_flow.py` … LLM のみ FakeLLM に差し替え、保存→集計→修正→取消を
  ファイルDB相手に一気通し。モデル非依存で安定して緑。
- `e2e/test_live_ollama.py` … 実モデル込みで配線を確認するスモークテスト。Ollama
  未起動なら自動スキップ。`-m live` 指定時のみ実行。

### ツール選択の評価（モデル品質の定量化）

テストは「配線が正しいか」を見る。一方、**実モデルがどれだけ正しくツールを選べるか**
は非決定論的なので、別途スクリプトで正答率を測る:

```bash
uv run python evals/tool_selection.py            # 既定 3 回/シナリオ
KOTOLOG_MODEL=claude-3-5-haiku-latest uv run python evals/tool_selection.py
```

## 既知の制約

- **ローカル qwen2.5:7b のツール選択精度は約 57%**（`evals/` 計測）。特に短い保存系
  発話（「9時に寝た」等）で tool-call 出力が崩れることがある。本番想定の Claude へ
  切替で改善する見込み。安定性が必要ならローカルは 14B 以上の検討を。
- `update_or_delete_record` の対象は現状「直近記録(last)」のみ。
- 個人利用前提。多ユーザー・認証・課金は非対応。

## ロードマップ

| フェーズ | 内容 | 状態 |
|---|---|---|
| P1 Core (CLI) | 記録・集計・修正・確認サマリ | ✅ 完了（= 本MVP） |
| P2 LINE | Webhook＋署名検証＋冪等化＋Reply | 未着手 |
| P3 Deploy | コンテナ化＋Cloud Run/Render＋Turso＋Claude切替 | 未着手 |
| P4 Enhance | 所見・リマインダー・グラフ等 | 任意 |

詳細なタスク分解は [開発計画.md](開発計画.md) を参照。

[育児記録エージェント_DesignDoc.md]: 育児記録エージェント_DesignDoc.md
