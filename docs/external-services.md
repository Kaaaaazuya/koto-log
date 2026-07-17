# 外部サービス一覧

koto-log が依存する外部サービスのURL・用途・セットアップ手順をまとめる。

---

## LINE Developers

**URL:** https://developers.line.biz/console/

**用途:** LINE Bot の作成・Webhook 設定・Push API・リッチメニュー

### セットアップ手順

1. LINE Developers Console でプロバイダーとチャネル（Messaging API）を作成
2. チャネル基本設定 → **チャネルシークレット** をコピー → `LINE_CHANNEL_SECRET`
3. Messaging API 設定 → **チャネルアクセストークン（長期）** を発行 → `LINE_CHANNEL_ACCESS_TOKEN`
4. Webhook URL に `https://<your-app>.onrender.com/webhook` を設定し「検証」
5. 応答設定 → **応答モードを「Bot」** に変更（「Chat」のままだと Webhook に届かない）

### リッチメニュー

詳細な設定手順（API curl コマンド付き）は [docs/rich-menu.md](rich-menu.md) を参照。

**推奨構成（2×3 グリッド）:**

| マス | ラベル | 送信テキスト |
|---|---|---|
| 1 | 母乳 | `母乳` |
| 2 | ミルク | `ミルク` |
| 3 | うんち | `うんち` |
| 4 | おしっこ | `おしっこ` |
| 5 | 寝た | `寝た` |
| 6 | 操作一覧 | `操作一覧` |

---

## Anthropic Console

**URL:** https://console.anthropic.com/

**用途:** Claude API キーの発行・使用量確認

### セットアップ手順

1. API Keys → **Create key** → `KOTOLOG_API_KEY` に設定
2. 使用モデル: `anthropic/claude-haiku-4-5-20251001`（`KOTOLOG_MODEL` に設定）

---

## Turso

**URL:** https://turso.tech/

**用途:** 本番 DB（SQLite 互換のクラウド DB）

### セットアップ手順

```bash
# Turso CLI インストール
brew install tursodatabase/tap/turso

# ログイン・DB 作成
turso auth login
turso db create koto-log
turso db show koto-log        # URL を確認 → KOTOLOG_DB_URL
turso db tokens create koto-log  # → TURSO_AUTH_TOKEN
```

### DB への直接アクセス

```bash
turso db shell koto-log
# → SQL が打てる（SELECT * FROM settings; など）
```

---

## Render

**URL:** https://render.com/

**用途:** Web サービスのホスティング・自動デプロイ

### セットアップ手順

1. GitHub リポジトリを接続
2. `render.yaml` が検出され Web Service が自動作成される
3. **Environment** タブで `sync: false` の環境変数を手動設定（下表参照）
4. `main` へ push するたびに自動デプロイ

### 設定が必要な環境変数

| 変数 | 説明 |
|---|---|
| `KOTOLOG_MODEL` | `anthropic/claude-haiku-4-5-20251001` |
| `KOTOLOG_API_KEY` | Anthropic API キー |
| `KOTOLOG_DB_URL` | `libsql://koto-log-xxxx.turso.io` |
| `TURSO_AUTH_TOKEN` | Turso 認証トークン |
| `LINE_CHANNEL_SECRET` | LINE チャネルシークレット |
| `LINE_CHANNEL_ACCESS_TOKEN` | LINE アクセストークン |
| `KOTOLOG_DASHBOARD_TOKEN` | ダッシュボード・管理画面のログイントークン（`/admin/login` でセッション認証。default-deny のため未設定ではアクセス不可） |

---

## Ollama（ローカル開発のみ）

**URL:** https://ollama.com/

**用途:** ローカル開発時の LLM（API コストなしで動作確認できる）

### セットアップ手順

```bash
# Docker で起動
docker run -d --name kotolog-ollama -p 11434:11434 \
  -v docker_ollama:/root/.ollama ollama/ollama:latest
docker exec kotolog-ollama ollama pull qwen2.5:7b
```

`.env` に以下を設定（デフォルト値なので明示しなくてもよい）:

```
KOTOLOG_MODEL=ollama_chat/qwen2.5:7b
KOTOLOG_OLLAMA_BASE=http://localhost:11434
```

---

## GitHub

**URL:** https://github.com/

**用途:** ソースコード管理・Render への自動デプロイトリガー

`main` ブランチへの push が Render の自動デプロイを起動する。
