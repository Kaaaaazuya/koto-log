# koto-log — 育児記録エージェント（LINE × Tool Use）

授乳・睡眠・おむつなどの育児記録を、**自然言語の対話だけ**で記録・集計・修正できる
エージェント。「3時に120ml飲んだ」と打てば構造化して保存し、「今日何回飲んだ？」と
聞けば集計して返す。中核は LLM の **Tool Use**：入力に応じて LLM が「どのツールを・
どの引数で呼ぶか」を判断し、アプリ側のコードが DB を更新・参照する。

> ステータス: **P5+P6 実装済み（Render + Turso + Claude Haiku で本番稼働中）**
> URL: `https://koto-log.onrender.com`

## できること

- 自由文を解釈して `feeding / sleep / diaper / temp` を構造化保存
  （例: 「3時にミルク120ml」「さっき寝た」「うんちした」）
- **まとめ入力**（P6）：複数記録を1文で一括登録
  （例: 「9時に母乳、10時から11時まで昼寝、11時におしっこ交換」→ 3件を1回の LLM 呼び出しで保存）
- 期間・種別・サブ種別を指定した集計（例: 「今日は何回飲んだ？」「母乳は何回？」）
- 「今日のまとめ」「前回の授乳はいつ？」などの振り返りクエリ
- 直近記録の修正・取り消し（例: 「150に直して」「さっきのなし」）
- 「操作一覧」「help」「？」で使い方を即返答（LLM バイパス）
- ダッシュボード（`/dashboard`）で授乳・睡眠・おむつを3タブで確認、7日間サマリーグラフ付き
- 管理画面（`/admin`）でブラウザから出産予定日・LINE ユーザー ID を設定
- **毎朝7時**に出産カウントダウン + LLM 一言を LINE Push（P5）
- **毎晩21時**に当日の育児サマリーを LINE Push（記録なしはスキップ）（P5）
- LINE チャット内から `due_date` を設定（`set_config` ツール）
- ローカル Ollama で完全無料動作。`KOTOLOG_MODEL` の変更だけで Claude へ切替可能

## LLM が担うこと・担わないこと

設計の核心は「**LLM に決めさせる範囲を最小化する**」こと。
LLM は意図の解釈とツール選択だけを担い、計算・DB操作・時刻解決はアプリコードが確定値で行う。

| | 担当 | 理由 |
|---|---|---|
| 「うんち」→ `diaper` と判断する | **LLM** | 自然言語の解釈 |
| どのツール（save/query/update）を呼ぶか | **LLM** | 意図の分類 |
| まとめ文から全記録を一括抽出する（P6） | **LLM** | force tool calling で構造化 |
| `started_at="さっき"` をそのままツールに渡す | **LLM** | 変換は行わない |
| 「さっき」→ JST 絶対時刻に変換する | **アプリ** (`timeparse.py`) | LLM は時刻計算しない |
| 「粉ミルク」→「ミルク」に正規化する | **アプリ** (`subtype.py`) | 集計ブレを防ぐ |
| DB に INSERT / SELECT する | **アプリ** (`crud.py`) | LLM は SQL を書かない |
| 件数・合計量・経過時間を計算する | **アプリ** (`executor.py`) | LLM は数え直さない |
| 計算結果を自然な文章にする | **LLM** | 文章生成 |
| 情報不足なら聞き返す | **LLM** | 対話の制御 |
| 毎朝の Push 通知テキストを生成する | **LLM** | パーソナルな一言 |

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
            DASH["dashboard.py\nJinja2 + Chart.js\n3タブ（授乳/睡眠/おむつ）"]
            ADM["admin.py\n/admin 設定画面"]
        end
        subgraph Agent["Agent Layer"]
            LOOP["agent/loop.py\nまとめ抽出 → Tool Use ループ"]
            EXT["agent/extractor.py\nforce tool calling\n一括抽出（P6）"]
            LLM["llm/client.py\nLiteLLM"]
            EXEC["tools/executor.py"]
            TP["timeparse.py\n時刻正規化"]
            ST["subtype.py\nサブタイプ正規化"]
        end
        subgraph Proactive["Proactive（P5）"]
            SCH["scheduler.py\nAPScheduler\n7時・21時 JST"]
            PUSH["push.py\nLINE Push API"]
        end
        REPLY["reply.py\nReply API"]
    end

    subgraph External["External Services"]
        CLAUDE["Claude Haiku\nAnthropic API"]
        DB[("Turso / libSQL")]
    end

    LA -->|"POST /webhook"| WH
    WH -->|"BackgroundTask"| LOOP
    LOOP --> EXT
    EXT -->|"records[]"| EXEC
    LOOP --> LLM
    LLM <-->|"tool_calls / results"| EXEC
    EXEC --> TP
    EXEC --> ST
    EXEC --> DB
    LLM <-->|"Messages API"| CLAUDE
    LOOP --> REPLY
    REPLY -->|"Reply API"| LA
    SCH -->|"毎朝7時・毎晩21時"| PUSH
    SCH --> DB
    SCH --> CLAUDE
    PUSH -->|"Push API"| LA
    Browser -->|"GET /dashboard"| DASH
    Browser -->|"GET /admin"| ADM
    DASH --> DB
    ADM --> DB
```

### メッセージ処理シーケンス図（単発記録）

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

    A->>L: extract_records（force tool calling）
    L-->>A: records: []（単発→空）
    A->>L: messages + ツール定義
    L-->>A: tool_call: save_record<br/>(type=feeding, amount=120)
    A->>E: execute(save_record, args)
    E->>E: 「さっき」→ JST 絶対時刻に変換
    E->>D: INSERT INTO records
    D-->>E: {id: 42}
    E-->>A: {ok: true, record: {...}}
    A->>L: tool result
    L-->>A: "ミルク 120ml（14:00）記録した"
    A->>R: send_reply(text)
    R->>User: LINE Reply API
```

### まとめ入力シーケンス図（P6）

```mermaid
sequenceDiagram
    actor User as ユーザー (LINE)
    participant W as webhook.py
    participant A as agent/loop.py
    participant Ext as extractor.py
    participant L as Claude Haiku
    participant E as executor.py
    participant D as Turso DB
    participant R as reply.py

    User->>W: POST /webhook<br/>"9時に母乳、10時から昼寝、11時におしっこ"
    W-->>User: 200 OK（即 ACK）
    W->>A: BackgroundTask

    A->>Ext: extract_records(text)
    Ext->>L: force tool calling<br/>（extract_records ツール指定）
    L-->>Ext: records: [feeding, sleep, diaper]
    Ext-->>A: 3件の records[]

    loop 各 record を保存
        A->>E: execute(save_record, record)
        E->>D: INSERT INTO records
        D-->>E: {id: N}
        E-->>A: {ok: true, record: {...}}
    end

    A->>R: テンプレート確認文<br/>（追加 LLM 呼び出し不要）
    R->>User: "授乳(母乳)（09:00）記録した\n睡眠（10:00）記録した\nおむつ(おしっこ)（11:00）記録した"
```

> 外部サービス（LINE / Anthropic / Turso / Render / Ollama）のセットアップ手順は [docs/external-services.md](docs/external-services.md) を参照。

## プロジェクト構成

```
src/kotolog/
├── types.py            # RecordType / FeedingSubType / DiaperSubType enum
├── config.py           # .env からの設定読込
├── db/                 # connection・crud・schema.sql（settings テーブル含む）
├── utils/
│   ├── timeparse.py    # 相対時刻 → JST絶対時刻
│   └── subtype.py      # sub_type 表記ゆれ正規化
├── tools/              # definitions(JSONスキーマ) / executor(DB操作)
├── llm/client.py       # LiteLLM ラッパ（local⇄Claude、tool_choice 対応）
├── agent/
│   ├── loop.py         # まとめ抽出 → tool-use ループ（フォールバック解析付き）
│   └── extractor.py    # force tool calling による一括記録抽出（P6）
├── templates/
│   ├── dashboard.html  # 3タブ（授乳/睡眠/おむつ）+ 7日間グラフ
│   └── admin.html      # 設定管理画面（予定日・ユーザーID）
├── line/
│   ├── webhook.py      # FastAPI app / 署名検証 / 冪等化 / イベント配線
│   ├── dashboard.py    # /dashboard ルーター
│   ├── admin.py        # /admin ルーター（設定 GET/POST + テスト Push）
│   ├── push.py         # LINE Push API クライアント
│   ├── scheduler.py    # APScheduler（7時カウントダウン・21時サマリー）
│   └── reply.py        # LINE Reply API クライアント
└── cli.py              # 対話CLI エントリ（LINE と同じ Agent を共有）
evals/                  # ツール選択の正答率評価
tests/                  # unit / integration / e2e
docs/
├── external-services.md  # 外部サービスの URL・セットアップ手順
└── adr/
    └── 0001-scheduler-apscheduler-in-process.md
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

> 出産予定日・LINE ユーザー ID は `/admin` 画面または LINE チャット内で設定。環境変数不要。

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
| `LINE_CHANNEL_ACCESS_TOKEN` | （LINE利用時必須） | Reply/Push API に使用 |
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
| P4 Enhance | ダッシュボード 3タブ（授乳/睡眠/おむつ）+ 7日グラフ | ✅ 完了 |
| P5 Proactive | APScheduler・毎朝カウントダウン・毎晩サマリー・管理画面 | ✅ 完了 |
| P6 Structured Output | まとめ入力・force tool calling 一括抽出 | ✅ 完了 |
| 今後 | リッチメニュー設定・授乳リマインダー | 任意 |

詳細なタスク分解は [開発計画.md](開発計画.md) を参照。
