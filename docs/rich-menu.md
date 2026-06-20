# LINE リッチメニュー設定ガイド（T4.7）

LINE アプリのチャット画面下部に常設ボタンを表示する設定手順。
頻繁に使う記録ボタン（母乳・ミルク・うんち等）をワンタップで送れるようにする。

---

## 完成イメージ

```
┌──────────────┬──────────────┐
│     母乳     │    ミルク    │
├──────────────┼──────────────┤
│    うんち    │   おしっこ   │
├──────────────┼──────────────┤
│     寝た     │   操作一覧   │
└──────────────┴──────────────┘
```

タップすると、対応するテキスト（「母乳」「ミルク」等）が自動送信される。

---

## 前提確認

以下は koto-log では設定済み：
- LINE Developers Console でチャネル（Messaging API）作成済み
- `LINE_CHANNEL_ACCESS_TOKEN` 取得済み（Render 環境変数に設定済み）

---

## 方法の選択

| 方法 | 難易度 | できること | おすすめ |
|---|---|---|---|
| **OA Manager（UI）** | 簡単 | 2〜4ボタンのシンプルな構成 | ✗（2×3 の6ボタン不可） |
| **Messaging API（curl）** | 普通 | 6ボタンの2×3グリッド、全カスタマイズ | ✅ |

6ボタンの構成は OA Manager では作れないため、API を使う方法を採用する。

---

## 手順（API 方式）

### ステップ 1: メニューを作成する

以下の curl を実行する。`{TOKEN}` は `LINE_CHANNEL_ACCESS_TOKEN` に置き換える。

```bash
curl -X POST https://api.line.me/v2/bot/richmenu \
  -H "Authorization: Bearer {TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "size": {"width": 2500, "height": 1686},
    "selected": true,
    "name": "kotolog-main",
    "chatBarText": "メニュー",
    "areas": [
      {
        "bounds": {"x": 0,    "y": 0,    "width": 1250, "height": 562},
        "action": {"type": "message", "label": "母乳", "text": "母乳"}
      },
      {
        "bounds": {"x": 1250, "y": 0,    "width": 1250, "height": 562},
        "action": {"type": "message", "label": "ミルク", "text": "ミルク"}
      },
      {
        "bounds": {"x": 0,    "y": 562,  "width": 1250, "height": 562},
        "action": {"type": "message", "label": "うんち", "text": "うんち"}
      },
      {
        "bounds": {"x": 1250, "y": 562,  "width": 1250, "height": 562},
        "action": {"type": "message", "label": "おしっこ", "text": "おしっこ"}
      },
      {
        "bounds": {"x": 0,    "y": 1124, "width": 1250, "height": 562},
        "action": {"type": "message", "label": "寝た", "text": "寝た"}
      },
      {
        "bounds": {"x": 1250, "y": 1124, "width": 1250, "height": 562},
        "action": {"type": "message", "label": "操作一覧", "text": "操作一覧"}
      }
    ]
  }'
```

成功すると以下が返る。**`richMenuId` を控えておく**:

```json
{"richMenuId": "richmenu-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"}
```

---

### ステップ 2: 背景画像を用意する

LINE に表示する背景画像（PNG）が必要。

**仕様:**
| 項目 | 値 |
|---|---|
| サイズ | **2500 × 1686 px**（必須） |
| 形式 | PNG または JPEG |
| ファイルサイズ | 1 MB 以下 |
| 各ボタン領域 | 1250 × 562 px |

**作成ツール:**
- **Figma**（無料・推奨）: 2500×1686 のフレームを作り、6マスに色分け＋テキストを入れてエクスポート
- **Canva**（無料）: カスタムサイズで作成
- **macOS Keynote**: スライドサイズを 2500×1686 に変更して作成→書き出し

**最低限の例（単色＋テキストだけでも動く）:**
- 背景: 白や淡い色
- 各マスに黒文字でラベル（母乳・ミルク…）
- マス境界は罫線を引くだけでOK

画像が用意できたら `richmenu.png` として保存する。

---

### ステップ 3: 画像をアップロードする

```bash
curl -X POST \
  "https://api-data.line.me/v2/bot/richmenu/{richMenuId}/content" \
  -H "Authorization: Bearer {TOKEN}" \
  -H "Content-Type: image/png" \
  -T richmenu.png
```

- `{richMenuId}` はステップ1で取得した ID に置き換える
- 成功すると空レスポンス（HTTP 200）が返る

---

### ステップ 4: 全ユーザーのデフォルトに設定する

```bash
curl -X POST \
  "https://api.line.me/v2/bot/user/all/richmenu/{richMenuId}" \
  -H "Authorization: Bearer {TOKEN}"
```

成功すると空レスポンス（HTTP 200）。以上で完了。

---

### 確認方法

1. LINE アプリでボットとのチャット画面を開く
2. 画面下部に「メニュー」バーが表示されていれば成功
3. バーをタップするとリッチメニューが展開する
4. 各ボタンをタップしてテキストが自動送信されることを確認

> **注意:** リッチメニューは iOS / Android の LINE アプリのみ表示される。LINE for PC (Windows/macOS) では表示されない。

---

## 既存メニューの確認・削除

現在設定されているメニューを確認する:

```bash
# デフォルトメニューの ID を確認
curl -X GET https://api.line.me/v2/bot/user/all/richmenu \
  -H "Authorization: Bearer {TOKEN}"

# メニュー一覧
curl -X GET https://api.line.me/v2/bot/richmenu/list \
  -H "Authorization: Bearer {TOKEN}"

# デフォルトを解除
curl -X DELETE https://api.line.me/v2/bot/user/all/richmenu \
  -H "Authorization: Bearer {TOKEN}"

# メニューを削除
curl -X DELETE https://api.line.me/v2/bot/richmenu/{richMenuId} \
  -H "Authorization: Bearer {TOKEN}"
```

---

## 応用: 寝た / 起きた トグル（上級）

現状の構成では「寝た」ボタンのみで、「起きた」はテキスト入力が必要。
ボタンを睡眠状態に応じて自動切替したい場合は **リッチメニュー切替**が使える。

**仕組み:** メニュー A（起床中→「寝た」表示）とメニュー B（睡眠中→「起きた」表示）を用意し、
ボタンタップでメニューを切り替える。切替は LINE アプリ内で完結し、サーバーへの通信は不要。

**ただし:** 切替アクション（`richmenuswitch`）は「ポストバックイベント」を発火する（テキスト送信ではない）。
現状の `webhook.py` はメッセージイベントのみ処理しているため、ポストバックハンドラーを追加する実装が別途必要になる。

→ 出産後に実際の使い勝手を確認してから対応を検討する。

---

## トークンの取得場所

`LINE_CHANNEL_ACCESS_TOKEN` は Render の Environment 画面に設定済み。
ローカルで curl を実行する場合は `.env` から取得するか、LINE Developers Console で確認:

[LINE Developers Console](https://developers.line.biz/console/) → チャネル選択 → Messaging API 設定 → チャネルアクセストークン
