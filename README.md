# koto-log — 育児記録エージェント（LINE × Tool Use）

授乳・睡眠・おむつなどの育児記録を、**自然言語の対話だけ**で記録・集計・修正できる
エージェント。「3時に120ml飲んだ」と打てば構造化して保存し、「今日何回飲んだ？」と
聞けば集計して返す。中核は LLM の **Tool Use**：入力に応じて LLM が「どのツールを・
どの引数で呼ぶか」を判断し、アプリ側のコードが DB を更新・参照する。

> ステータス: **P3 完了（Render + Turso + Claude Haiku で本番稼働中）**
> URL: `https://koto-log.onrender.com`

## できること

- 自由文を解釈して `feeding / sleep / diaper / temp` を構造化保存
  （例: 「3時にミルク120ml」「さっき寝た」「うんちした」）
- 期間・種別・サブ種別を指定した集計（例: 「今日は何回飲んだ？」「母乳は何回？」）
- 「今日のまとめ」「前回の授乳はいつ？」などの振り返りクエリ
- 直近記録の修正・取り消し（例: 「150に直して」「さっきのなし」）
- 「操作一覧」「help」「？」で使い方を即返答（LLM バイパス）
- ダッシュボード（`/dashboard`）で授乳タイムライン・7日間サマリをブラウザで確認
- ローカル Ollama で完全無料動作。`KOTOLOG_MODEL` の変更だけで Claude へ切替可能

## LLM が担うこと・担わないこと

設計の核心は「**LLM に決めさせる範囲を最小化する**」こと。
LLM は意図の解釈とツール選択だけを担い、計算・DB操作・時刻解決はアプリコードが確定値で行う。

| | 担当 | 理由 |
|---|---|---|
| 「うんち」→ `diaper` と判断する | **LLM** | 自然言語の解釈 |
| どのツール（save/query/update）を呼ぶか | **LLM** | 意図の分類 |
| `started_at="さっき"` をそのままツールに渡す | **LLM** | 変換は行わない |
| 「さっき」→ JST 絶対時刻に変換する | **アプリ** (`timeparse.py`) | LLM は時刻計算しない |
| 「粉ミルク」→「ミルク」に正規化する | **アプリ** (`subtype.py`) | 集計ブレを防ぐ |
| DB に INSERT / SELECT する | **アプリ** (`crud.py`) | LLM は SQL を書かない |
| 件数・合計量・経過時間を計算する | **アプリ** (`executor.py`) | LLM は数え直さない |
| 計算結果を自然な文章にする | **LLM** | 文章生成 |
| 情報不足なら聞き返す | **LLM** | 対話の制御 |

## アーキテクチャ

### コンポーネント図

```mermaid
graph TD
    subgraph LINE["LINE Platform"]
        LA[LINE App]
    end

    subgraph Render["Render (Docker)"]
        subgraph FastAPI
            WH["webhook.py\n署名検証・冪等化\nショートカット"]
            DASH["dashboard.py\nJinja2 + Chart.js"]
        end
        subgraph Agent["Agent Layer"]
            LOOP["agent/loop.py\nTool Use ループ"]
            LLM["llm/client.py\nLiteLLM"]
            EXEC["tools/executor.py"]
            TP["timeparse.py\n時刻正規化"]
            ST["subtype.py\nサブタイプ正規化"]
        end
        REPLY["reply.py\nReply API"]
    end

    subgraph External["External Services"]
        CLAUDE["Claude Haiku\nAnthropic API"]
        DB[("Turso / libSQL")]
    end

    LA -->|"POST /webhook"| WH
    WH -->|"BackgroundTask"| LOOP
    LOOP --> LLM
    LLM <-->|"tool_calls / results"| EXEC
    EXEC --> TP
    EXEC --> ST
    EXEC --> DB
    LLM <-->|"Messages API"| CLAUDE
    LOOP --> REPLY
    REPLY -->|"Reply API"| LA
    Browser -->|"GET /dashboard"| DASH
    DASH --> DB
```

### メッセージ処理シーケンス図

```mermaid
sequenceDiagram
    actor User as ユーザー (LINE)
    participant W as webhook.py
    participant A as agent/loop.py
    participant L as Claude Haiku
    participant E as executor.py
    participant D as Turso DB
    participant R as reply.py

    User->>W: POST /webhook<br/>"ミルク120ml"
    W-->>User: 200 OK（即 ACK）
    W->>A: BackgroundTask

    A->>L: messages + ツール定義
    L-->>A: tool_call: save_record<br/>(type=feeding, amount=120, started_at="さっき")
    A->>E: execute(save_record, args)
    E->>E: 「さっき」→ JST 絶対時刻に変換
    E->>D: INSERT INTO records
    D-->>E: {id: 42}
    E-->>A: {ok: true, id: 42}

    A->>L: tool result
    L-->>A: "ミルク 120ml（14:00）記録した"
    A->>R: send_reply(text)
    R->>User: LINE Reply API
```

> 外部サービス（LINE / Anthropic / Turso / Render / Ollama）のセットアップ手順は [docs/external-services.md](docs/external-services.md) を参照。

## プロジェクト構成

```
src/kotolog/
├── types.py            # RecordType / FeedingSubType / DiaperSubType enum
├── config.py           # .env からの設定読込
├── db/                 # connection・crud・schema.sql
├── utils/
│   ├── timeparse.py    # 相対時刻 → JST絶対時刻
│   └── subtype.py      # sub_type 表記ゆれ正規化
├── tools/              # definitions(JSONスキーマ) / executor(DB操作)
├── llm/client.py       # LiteLLM ラッパ（local⇄Claude）
├── agent/loop.py       # tool-use ループ＋フォールバック解析
├── templates/
│   └── dashboard.html  # 授乳タイムライン・7日間サマリ
├── line/
│   ├── webhook.py      # FastAPI app / 署名検証 / 冪等化 / イベント配線
│   ├── dashboard.py    # /dashboard ルーター
│   └── reply.py        # LINE Reply API クライアント
└── cli.py              # 対話CLI エントリ（LINE と同じ Agent を共有）
evals/                  # ツール選択の正答率評価
tests/                  # unit / integration / e2e
```

## ローカル開発セットアップ

### 前提
- [uv](https://docs.astral.sh/uv/)（パッケージ管理）
- Ollama（ローカル LLM）

### 手順

```bash
# 依存をインストール
uv sync

# Ollama を Docker で起動
docker run -d --name kotolog-ollama -p 11434:11434 \
  -v docker_ollama:/root/.ollama ollama/ollama:latest
docker exec kotolog-ollama ollama pull qwen2.5:7b

# 設定ファイルを用意
cp .env.example .env   # 必要に応じて編集

# CLI で起動
uv run kotolog

# LINE Webhook サーバとして起動（ngrok でトンネル）
uv run uvicorn kotolog.line.webhook:app --reload --port 8000
```

## 本番環境（Render + Turso + Claude）

### 構成

| コンポーネント | サービス | 備考 |
|---|---|---|
| アプリ | Render (Docker) | `render.yaml` で設定 |
| DB | Turso (libSQL) | SQLite 互換のクラウド DB |
| LLM | Claude Haiku 4.5 | `anthropic/claude-haiku-4-5-20251001` |

### 環境変数（Render ダッシュボードで設定）

| 変数 | 説明 |
|---|---|
| `KOTOLOG_MODEL` | `anthropic/claude-haiku-4-5-20251001` |
| `KOTOLOG_API_KEY` | Anthropic API キー |
| `KOTOLOG_DB_URL` | `libsql://koto-log-xxxx.turso.io` |
| `TURSO_AUTH_TOKEN` | Turso 認証トークン |
| `LINE_CHANNEL_SECRET` | LINE チャネルシークレット |
| `LINE_CHANNEL_ACCESS_TOKEN` | LINE アクセストークン |
| `KOTOLOG_DASHBOARD_TOKEN` | ダッシュボード認証トークン（未設定で認証なし） |
| `KOTOLOG_DEFAULT_CHILD` | `baby`（render.yaml に記載済み） |

### デプロイ

```bash
git push  # main への push で Render が自動デプロイ
```

## 全環境共通の設定（環境変数）

| 変数 | 既定 | 説明 |
|---|---|---|
| `KOTOLOG_MODEL` | `ollama_chat/qwen2.5:7b` | LiteLLM のモデル文字列 |
| `KOTOLOG_API_KEY` | （空） | ホスト型モデル用 API キー |
| `KOTOLOG_OLLAMA_BASE` | `http://localhost:11434` | Ollama のベース URL |
| `KOTOLOG_DB_URL` | `kotolog.db` | DB URL（Turso: `libsql://...`） |
| `TURSO_AUTH_TOKEN` | （空） | Turso 接続トークン |
| `KOTOLOG_DEFAULT_CHILD` | `baby` | 子の別名 |
| `LINE_CHANNEL_SECRET` | （LINE利用時必須） | 署名検証に使用 |
| `LINE_CHANNEL_ACCESS_TOKEN` | （LINE利用時必須） | Reply API に使用 |
| `KOTOLOG_DASHBOARD_TOKEN` | （空） | ダッシュボード URL トークン |

## LINE リッチメニュー推奨構成

| マス | ラベル | 送信テキスト |
|---|---|---|
| 1 | 母乳 | `母乳` |
| 2 | ミルク | `ミルク` |
| 3 | うんち | `うんち` |
| 4 | おしっこ | `おしっこ` |
| 5 | 寝た / 起きた | `寝た` / `起きた` |
| 6 | 操作一覧 | `操作一覧` |

## テスト

```bash
uv run pytest                                        # 全テスト（live は自動スキップ）
uv run pytest tests/unit/                            # 単体テストのみ
uv run pytest tests/unit/ --cov --cov-report=term-missing  # カバレッジ付き
uv run pytest -m live                                # 実 Ollama E2E（要起動）
```

| 層 | 置き場所 | 内容 |
|---|---|---|
| 単体 (unit) | `tests/unit/` | 純ロジック。DB/ネットワーク非依存。FakeLLM パターンを使用 |
| 結合 (integration) | `tests/integration/` | 実 DB・複数コンポーネント結線 |
| E2E | `tests/e2e/` | 入口からの一気通し |

## ロードマップ

| フェーズ | 内容 | 状態 |
|---|---|---|
| P1 Core (CLI) | 記録・集計・修正・確認サマリ | ✅ 完了 |
| P1.5 MVP+ | 集計強化・sub_type正規化・前回いつ・まとめ | ✅ 完了 |
| P2 LINE | Webhook・署名検証・冪等化・Reply API | ✅ 完了 |
| P3 Deploy | Dockerfile + Render + Turso + Claude Haiku | ✅ 完了 |
| P4 Enhance | ダッシュボード（授乳タイムライン・7日サマリ）| ✅ 実装済み |
| P4 継続 | リッチメニュー設定・グラフ拡充・リマインダー | 任意 |

詳細なタスク分解は [開発計画.md](開発計画.md) を参照。
