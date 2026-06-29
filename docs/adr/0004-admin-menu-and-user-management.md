# ADR-0004: 管理画面の改善（メニュー画面・ユーザー管理・ニックネーム）

- **日付:** 2026-06-26
- **状態:** 採用（実装未着手） / 2026-06-28 ロードマップ v2 で **P9 に内包**

> **再配置（2026-06-28）**: ロードマップ v2 により、本 ADR の「メニュー画面・LINE
> ユーザー管理・通知 ON/OFF・Push fan-out」は **P9（子ども複数・夫婦共有＋データモデル
> 刷新）の一部**として実装する。`users` テーブルは将来の (channel, user) 拡張前提
> （[ADR-0005](0005-multi-child-strategy.md) は棄却）ではなく、user–child データモデル
> （P9 新 ADR）と整合させる。本 ADR の UI/通知の設計自体は有効。

## コンテキスト

管理系の Web 画面が増えてきたが（`/dashboard` 閲覧、`/admin` 設定、今後 P8 の
`/admin/records`）、**画面間を行き来するナビゲーションが無い**。URL を直打ちする必要がある。

ユーザー周りは事実上「単一ユーザー」設計になっている。

- Push 宛先は `settings.line_user_id` 1件のみで、webhook 受信のたびに
  **最後に話しかけた人で上書き**される（`webhook.py:119`）。複数人（両親）で使うと
  通知先が安定しない。
- 利用者の**一覧・ニックネーム付与・通知可否の管理**ができない。
- `sessions` テーブルはスキーマ定義のみで**どこからも読み書きされていない（dead）**。

マイグレーションは `init_db()` が `schema.sql` を `CREATE TABLE IF NOT EXISTS` で
流すだけ（`ALTER`/移行フレームワークは無い）。新規テーブル追加は起動時に自動反映される。

### 決定済みの方針（本 ADR の前提）

- 「登録ユーザー」「ニックネーム」は **LINE 利用者（パパ/ママ等）** を指す
- LINE 利用者は **話しかけたら自動登録**（webhook で upsert）
- 定期 Push は **ユーザーごとの通知 ON/OFF** に従い配信

## 決定

**`/admin` をメニュー（ハブ）化し、LINE 利用者を管理する仕組みを追加する。**

1. **メニュー画面**: `/admin` をランディング兼ハブにし、各画面へのリンクを置く
   （ダッシュボード / 記録管理(P8) / 設定 / ユーザー管理）。併せて管理系ページ共通の
   簡易ナビヘッダを設ける。
2. **`users` テーブルを新設**する（`sessions` は使わない）。
   ```sql
   CREATE TABLE IF NOT EXISTS users (
       line_user_id   TEXT PRIMARY KEY,
       nickname       TEXT,
       notify_enabled INTEGER NOT NULL DEFAULT 1,
       created_at     TEXT NOT NULL,
       updated_at     TEXT NOT NULL
   );
   ```
3. **自動登録**: webhook 受信時に `users` へ upsert する。**既存のニックネーム・通知設定は
   保持**し、新規 user_id のみ作成する。
4. **ユーザー管理 UI**: `/admin/users` で一覧・ニックネーム編集・通知 ON/OFF トグル・削除。
5. **Push の fan-out**: 定期 Push（朝7時・夜21時）は `notify_enabled=1` の全ユーザーへ
   送る。`users` が空の場合は移行期フォールバックとして `settings.line_user_id` を使う。
6. ニックネームは将来 Push 本文等で活用可能（本 ADR のスコープは登録・管理まで）。
7. **複数子との整合**（[ADR-0005](0005-multi-child-strategy.md)）: 当面は案B
   （子＝インスタンスごとに別 DB）のため `users` はインスタンス単位で独立してよい。
   ただし案A（1アプリ複数チャネル）移行に備え、`users` は将来 `channel_id`
   （or destination）列を足して (channel, user) 単位へ拡張できる前提で設計する。

## 理由

- メニュー化で画面間導線ができ、URL 直打ちが不要になる
- `users` テーブルは「利用者プロファイル」を会話状態と分離して持てる。`sessions` は
  dead なので流用より新設が明快
- 自動登録は摩擦が少なく、管理画面は「ニックネーム付与・通知制御・削除」に専念できる
- 通知 ON/OFF をユーザー単位に持つことで、両親で使っても通知先が安定する
- 新規テーブルは `CREATE TABLE IF NOT EXISTS` で本番 Turso にも起動時自動反映され、
  手動マイグレーション不要

## 却下した案の理由

**案: `sessions` テーブルを拡張して使う。** dead テーブルであり、本来は会話状態用。
利用者プロファイルは別概念なので新規 `users` の方が見通しが良い。

**案: 利用者を管理画面から手動登録する。** user_id の手入力は手間でミスも増える。
自動登録（webhook upsert）を採用。

**案: Push は代表1人固定 / 全員一律配信。** 家族で通知要否が分かれるため、
ユーザー単位の ON/OFF を採用。

## トレードオフ・リスク

- **PII**: LINE `userId` は Push に必須なので保存する。ニックネームは実名を避ける運用
  （NFR-4 踏襲）。管理画面はトークン保護。userId を計測ログ等に平文で残さない方針と整合
  （→ [[project-pii-check]]）。
- **二重管理（移行期）**: 既存 `settings.line_user_id` と `users` が併存する。
  scheduler は `users` を優先し、空なら `settings` にフォールバック。将来 `users` に一本化。
- **Push レート**: 宛先は家族規模（数人）なので逐次送信で十分。1人ずつ try で失敗を隔離。
- **dead な `sessions`**: 本 ADR では削除しない（既存スキーマに残置）。不要が確定したら
  別途撤去を検討。
- **削除は不可逆**: ユーザー削除は確認ステップを設ける。削除後はそのユーザーへ Push されない。

---

## 実装計画（別セッションで着手可能）

### crud に追加する関数（`crud.py`）

| 関数 | 役割 |
|---|---|
| `upsert_user(conn, line_user_id)` | 新規なら作成、既存はニックネーム・通知設定を保持 |
| `list_users(conn)` | 管理一覧用に全件取得（updated_at 降順など） |
| `set_user_nickname(conn, line_user_id, nickname)` | ニックネーム更新 |
| `set_user_notify(conn, line_user_id, enabled: bool)` | 通知 ON/OFF 更新 |
| `delete_user(conn, line_user_id)` | 削除 |
| `get_notify_user_ids(conn)` | `notify_enabled=1` の user_id 一覧（Push 宛先） |

### 変更点

- **`schema.sql`**: `users` テーブル追加（上記 DDL）
- **`webhook.py`**: `crud.set_setting(conn, "line_user_id", user_id)` を
  `crud.upsert_user(conn, user_id)` に置き換え（移行期は setting も残すか要判断）
- **`scheduler.py`**: `_run_morning_push` / `_run_daily_summary_push` の宛先取得を
  `get_notify_user_ids()`（空なら `settings.line_user_id` フォールバック）に変更し、
  宛先ごとに `send_push` をループ。送信は1人ずつ try で失敗隔離
- **`admin.py`**:
  - `GET /admin` をメニュー化（各画面リンク）
  - `GET /admin/users` 一覧
  - `POST /admin/users/{id}/nickname` ニックネーム更新（PRG）
  - `POST /admin/users/{id}/notify` 通知トグル（PRG）
  - `POST /admin/users/{id}/delete` 削除（確認 → PRG）
- **テンプレート**: `admin.html` をメニュー化、`admin_users.html` 追加、
  管理系ページ共通のナビ部分

### タスク分解（P9）

| ID | 内容 | 完了条件 |
|---|---|---|
| T9.1 | メニュー画面（`/admin` ハブ化＋各画面ナビ） | 各画面へリンク遷移できる |
| T9.2 | `users` テーブル＋crud（upsert/list/nickname/notify/delete/notify_ids） | 単体テストで CRUD 検証 |
| T9.3 | webhook 自動登録（upsert、既存ニックネーム保持） | 初回発言で1件作成、再発言で重複せずニックネーム維持 |
| T9.4 | ユーザー管理 UI（一覧・ニックネームCRUD・通知トグル・削除） | UI から各操作が反映される |
| T9.5 | 定期 Push を通知 ON ユーザーへ fan-out（フォールバック付き） | 複数宛先へ配信、OFF は除外、users 空時は setting へ |
| T9.6 | テスト（webhook 自動登録・fan-out・管理 UI・トークン保護） | 下記テスト全通過 |

### テスト方針

- `upsert_user`: 新規作成／再 upsert でニックネーム・通知設定が保持される
- webhook: テキスト受信で `users` に1件、同一 user_id 再送で重複しない
- `get_notify_user_ids`: OFF のユーザーが除外される
- scheduler: 宛先が複数あれば全員に `send_push`（FakePushClient で検証）、
  `users` 空なら `settings.line_user_id` にフォールバック
- 管理 UI: トークン無し/誤りで 403、ニックネーム更新・通知トグル・削除が反映
- メニュー: `/admin` に各画面へのリンクが含まれる

### 受け入れ条件（Definition of Done）

1. `/admin` から各 Web 画面（ダッシュボード・記録管理・設定・ユーザー管理）へ遷移できる
2. LINE で話しかけると `users` に自動登録される（重複なし・ニックネーム保持）
3. `/admin/users` でニックネームの登録・変更・削除、通知 ON/OFF ができる
4. 定期 Push が通知 ON のユーザー全員に届き、OFF には届かない
5. 本番 Turso に起動時 `users` が自動作成され、手動マイグレーション不要

## 参考

- 既存設定 UI（PRG・トークン認証）: `src/kotolog/line/admin.py`
- Push 実装: `src/kotolog/line/push.py` / `scheduler.py`
- 関連 ADR: [ADR-0003](0003-admin-record-crud.md)（記録 CRUD, P8）
