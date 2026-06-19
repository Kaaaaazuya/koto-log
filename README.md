# koto-log — 育児記録エージェント（LINE × Tool Use）

授乳・睡眠・おむつなどの育児記録を、**自然言語の対話だけ**で記録・集計・修正できる
エージェント。「3時に120ml飲んだ」と打てば構造化して保存し、「今日何回飲んだ？」と
聞けば集計して返す。中核は LLM の **Tool Use**：入力に応じて LLM が「どのツールを・
どの引数で呼ぶか」を判断し、アプリ側のコードが DB を更新・参照する。

> ステータス: **P2 実装中（Webhook・冪等化・Reply API 配線完了。ngrok 実機確認が次のステップ）**。
> 設計は [育児記録エージェント_DesignDoc.md] / [開発計画.md](開発計画.md) を参照。

## できること（現状）

- 自由文を解釈して `feeding / sleep / diaper` を構造化保存（例: 「3時にミルク120ml」）
- 期間・種別・サブ種別を指定した集計に応答（例: 「今日は何回飲んだ？」「母乳は何回？」）
- 「今日のまとめ」「前回の授乳はいつ？」などの振り返りクエリ
- 直近記録の修正・取り消し（例: 「150に直して」「さっきのなし」）
- 書き込み後は確認サマリを返し、情報不足なら聞き返す
- ローカルLLM（Ollama）で完全無料動作。`KOTOLOG_MODEL` の変更だけで Claude へ切替可能
- LINE Messaging API Webhook の受信・署名検証・冪等化・返信（P2 配線完了）

## アーキテクチャ

```
LINE App ─── POST /webhook ──▶ FastAPI (line/webhook.py)
                                  │ 署名検証 (HMAC-SHA256)
                                  │ 冪等化 (processed_events)
                                  ▼ BackgroundTask
CLI (cli.py) ──────────────▶ Agent ループ (agent/loop.py)
                                  ├─ LLM クライアント (llm/client.py, LiteLLM)
                                  └─ ツール実行 (tools/executor.py)
                                       ├─ save / query / update_or_delete
                                       ├─ 時刻正規化 (utils/timeparse.py)
                                       └─ sub_type 正規化 (utils/subtype.py)
                                  ▼
                               SQLite DB (db/)
                                  │
                               Reply API (line/reply.py) ──▶ LINE App
```

LLM はツールを「選ぶ」だけ。実際の DB 操作・時刻解決はアプリ側コードが行うため、
ツール定義（JSONスキーマ）と実行コードはモデル非依存に保たれている。

## プロジェクト構成

```
src/kotolog/
├── config.py           # .env からの設定読込
├── db/                 # connection・crud・schema.sql
├── utils/
│   ├── timeparse.py    # 相対時刻 → JST絶対時刻
│   └── subtype.py      # sub_type 表記ゆれ正規化
├── tools/              # definitions(JSONスキーマ) / executor(DB操作)
├── llm/client.py       # LiteLLM ラッパ（local⇄Claude）
├── agent/loop.py       # tool-use ループ＋フォールバック解析
├── line/
│   ├── webhook.py      # FastAPI app / 署名検証 / 冪等化 / イベント配線
│   └── reply.py        # LINE Reply API クライアント
└── cli.py              # 対話CLI エントリ（LINE と同じ Agent を共有）
evals/tool_selection.py # ツール選択の正答率評価スクリプト
tests/                  # unit / integration / e2e
```

## セットアップ（CLI）

### 前提
- [uv](https://docs.astral.sh/uv/)（パッケージ管理）
- Docker（ローカルLLM の Ollama 用）

### 手順

```bash
# 1. 依存をインストール
uv sync

# 2. Ollama を Docker で起動
docker run -d --name kotolog-ollama -p 11434:11434 \
  -v docker_ollama:/root/.ollama ollama/ollama:latest
docker exec kotolog-ollama ollama pull qwen2.5:7b

# 3. 設定ファイルを用意
cp .env.example .env   # 必要に応じて編集

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

## LINE 実機確認（T2.4）の準備手順

ngrok でローカルサーバを公開し、スマホの LINE から記録・集計できるようにする。

### Step 1: LINE チャネルを作成する

1. [LINE Developers Console](https://developers.line.biz/console/) にアクセス（LINE アカウントでログイン）
2. 「新規プロバイダー作成」→ 任意の名前を入力
3. 「チャネル作成」→「Messaging API」を選択
4. チャネル名・説明・カテゴリを入力して作成

チャネル作成後に取得するもの：

| 取得場所 | 値 | .env に設定する変数 |
|---|---|---|
| 「チャネル基本設定」タブ → チャネルシークレット | `xxxx...` | `LINE_CHANNEL_SECRET` |
| 「Messaging API設定」タブ → チャネルアクセストークン（長期）→「発行」 | `xxxx...` | `LINE_CHANNEL_ACCESS_TOKEN` |

**Messaging API設定タブで必ず変更する設定：**
- 「Webhook使用」→ **オン**
- 「自動応答メッセージ」→ **オフ**（LINE 公式の自動返信と競合するため）
- 「あいさつメッセージ」→ オフ推奨

### Step 2: ngrok をインストール・設定する

ngrok は「PC の中のサーバをインターネットに公開するトンネルツール」。
LINE のサーバが localhost に届かないため開発時だけ使う（本番デプロイ後は不要）。

> **ngrok トークンは LINE トークンとは別物。** LINE の Channel Secret / Access Token とは
> 無関係で、プロジェクトの `.env` には入れない。ngrok 自身の設定ファイル
> (`~/.config/ngrok/ngrok.yml`) に保存される。

```bash
# macOS（Homebrew）
brew install ngrok
```

**ngrok アカウントの作成とトークン設定（初回のみ）:**

1. [https://dashboard.ngrok.com](https://dashboard.ngrok.com) でアカウント作成（無料・GitHub ログイン可）
2. ログイン後、左メニュー「Getting Started」→「Your Authtoken」を開く
3. 表示されたトークンをコピーして以下を実行:

```bash
ngrok config add-authtoken <dashboard に表示されたトークン>
```

設定は `~/.config/ngrok/ngrok.yml` に書き込まれ、以降は自動で読まれる。

### Step 3: .env を設定する

```bash
cp .env.example .env
```

`.env` に以下を追記：

```
LINE_CHANNEL_SECRET=<Step 1 で取得したシークレット>
LINE_CHANNEL_ACCESS_TOKEN=<Step 1 で発行したアクセストークン>
```

### Step 4: サーバ起動 → ngrok でトンネル

**ターミナル 1**（サーバ起動）:

```bash
uv run uvicorn kotolog.line.webhook:app --reload --port 8000
```

**ターミナル 2**（ngrok でトンネル）:

```bash
ngrok http 8000
```

出力例:
```
Forwarding   https://abcd-1234.ngrok-free.app -> http://localhost:8000
```

### Step 5: LINE の Webhook URL を設定する

1. LINE Developers Console → 「Messaging API設定」タブ
2. Webhook URL に `https://abcd-1234.ngrok-free.app/webhook` を入力
3. 「更新」→「検証」ボタンを押して 200 OK が返ることを確認

### Step 6: スマホで友だち追加して動作確認

- 「Messaging API設定」タブの QR コードをスマホで読み込んで友だち追加
- LINE でメッセージを送ると返信が届くことを確認

```
送信: 3時にミルク120ml飲んだ
返信: ミルク120mlを3時に記録しました。

送信: 今日のまとめは？
返信: 今日は授乳1回（120ml）でした。
```

## P3 デプロイ準備手順（Render + Turso + Claude）

ローカル依存をすべてクラウドに移して「PC なしで 24 時間動く」状態にする。
実装（Dockerfile・libsql対応・コード変更）は実際の P3 作業で行う。
ここでは**アカウント作成と認証情報の取得**だけを先に済ませる。

### Step A: Turso — クラウド DB を作る

Turso は SQLite 互換のクラウド DB。ローカルの `kotolog.db` をそのまま置き換える。

```bash
# Turso CLI をインストール
brew install tursodatabase/tap/turso

# アカウント作成・ログイン（GitHubアカウントでOK）
turso auth login

# DB を作成（名前は任意）
turso db create koto-log

# 接続URLを確認
turso db show koto-log --url
# → libsql://koto-log-xxxx.turso.io  ← KOTOLOG_DB_URL に設定する値

# 認証トークンを発行
turso db tokens create koto-log
# → eyJh...  ← TURSO_AUTH_TOKEN に設定する値
```

取得したら `.env` に追記：
```
KOTOLOG_DB_URL=libsql://koto-log-xxxx.turso.io
TURSO_AUTH_TOKEN=eyJh...
```

> 無料プランで DB 1つ・月500M行読み取りまで。個人利用には十分。

### Step B: Anthropic API キー — Claude Haiku に切り替える

```bash
# ローカルでも先に動作確認できる
KOTOLOG_MODEL=claude-3-5-haiku-latest \
KOTOLOG_API_KEY=sk-ant-... \
uv run kotolog
```

取得場所: [https://console.anthropic.com/settings/keys](https://console.anthropic.com/settings/keys)

`.env` に追記：
```
KOTOLOG_MODEL=claude-3-5-haiku-latest
KOTOLOG_API_KEY=sk-ant-...
```

### Step C: Render — アプリをデプロイする

Render は「GitHub リポジトリを連携してボタン1つでデプロイ」できる PaaS。
ngrok は不要になり、固定の公開 URL が発行される。

1. [https://render.com](https://render.com) でアカウント作成（GitHubログイン可）
2. GitHub リポジトリを連携しておく（このリポジトリを public にするか、Render に private アクセスを許可）
3. 「New Web Service」→ リポジトリを選択

Render には `render.yaml` を置くと設定が自動で読み込まれる（P3 作業で追加予定）。

**デプロイ後に Render の環境変数として設定するもの：**

| 変数 | 値 |
|---|---|
| `KOTOLOG_MODEL` | `claude-3-5-haiku-latest` |
| `KOTOLOG_API_KEY` | Anthropic API キー |
| `KOTOLOG_DB_URL` | Turso の libsql URL |
| `TURSO_AUTH_TOKEN` | Turso の認証トークン |
| `LINE_CHANNEL_SECRET` | LINE チャネルシークレット |
| `LINE_CHANNEL_ACCESS_TOKEN` | LINE アクセストークン |

**デプロイ完了後に LINE の Webhook URL を更新：**
```
https://<your-app>.onrender.com/webhook
```
（ngrok の URL の代わりにこれを LINE Dev Console に設定する）

> スリープ問題: 無料プランは15分アイドルでスリープ。LINE から使うなら月$7の
> Starter プランを推奨（常時起動）。

## 設定（環境変数）

| 変数 | 既定 | 説明 |
|---|---|---|
| `KOTOLOG_MODEL` | `ollama_chat/qwen2.5:7b` | LiteLLM のモデル文字列。本番例: `claude-3-5-haiku-latest` |
| `KOTOLOG_API_KEY` | （空） | ホスト型モデル用 APIキー（ローカルでは不要） |
| `KOTOLOG_OLLAMA_BASE` | `http://localhost:11434` | Ollama のベースURL（ローカル時のみ使用） |
| `KOTOLOG_DB_URL` | `kotolog.db` | DB URL。本番（Turso）例: `libsql://koto-log-xxxx.turso.io` |
| `TURSO_AUTH_TOKEN` | （空） | Turso 接続トークン（本番のみ必要） |
| `KOTOLOG_DEFAULT_CHILD` | `baby` | 子の別名（実名は保持しない方針） |
| `LINE_CHANNEL_SECRET` | （必須: LINE利用時） | LINE チャネルシークレット（署名検証に使用） |
| `LINE_CHANNEL_ACCESS_TOKEN` | （必須: LINE利用時） | LINE チャネルアクセストークン（Reply API に使用） |

## テスト

3 層に分けて配置し、フォルダから対応マーカーを自動付与する。

| 層 | 置き場所 | 内容 |
|---|---|---|
| 単体 (unit) | `tests/unit/` | 純ロジック。DB/ネットワーク非依存 |
| 結合 (integration) | `tests/integration/` | 実DB・複数コンポーネント結線（CRUD・executor・LINE webhook）|
| E2E (e2e) | `tests/e2e/` | 入口からの一気通し（決定論版＋実Ollama版） |

```bash
uv run pytest                 # 高速スイート（live は自動スキップ）
uv run pytest -m unit
uv run pytest -m integration
uv run pytest -m e2e
uv run pytest -m live         # 実Ollama E2E（要・Ollama起動）
```

### ツール選択の評価

```bash
uv run python evals/tool_selection.py            # 既定 3 回/シナリオ
KOTOLOG_MODEL=claude-3-5-haiku-latest uv run python evals/tool_selection.py
```

## 既知の制約

- **ローカル qwen2.5:7b のツール選択精度は約 57%**（`evals/` 計測）。特に短い保存系
  発話で tool-call 出力が崩れることがある。本番想定の Claude へ切替で改善する見込み。
- `update_or_delete_record` の対象は現状「直近記録(last)」のみ。
- 個人利用前提。多ユーザー・認証・課金は非対応。
- LINE 連携時の LLM 呼び出しは BackgroundTask で同期実行（シングルユーザー前提）。

## ロードマップ

| フェーズ | 内容 | 状態 |
|---|---|---|
| P1 Core (CLI) | 記録・集計・修正・確認サマリ | ✅ 完了 |
| P1.5 MVP+ | 集計強化・sub_type正規化・前回いつ・まとめ | ✅ 完了 |
| P2 LINE | Webhook・署名検証・冪等化・Reply API | ✅ 完了 |
| P3 Deploy | Dockerfile + Render デプロイ + Turso + Claude Haiku 切替 | 未着手 |
| P4 Enhance | 所見・リマインダー・グラフ等 | 任意 |

詳細なタスク分解は [開発計画.md](開発計画.md) を参照。

[育児記録エージェント_DesignDoc.md]: 育児記録エージェント_DesignDoc.md
