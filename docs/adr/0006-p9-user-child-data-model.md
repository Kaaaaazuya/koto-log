# ADR-0006: P9 データモデル設計（user–child・対象児解決・マイグレーション基盤）

- **日付:** 2026-06-28
- **状態:** 採用（実装未着手・P9 / T9.1）

## コンテキスト

[ロードマップ v2](../koto-log_roadmap_v2_refined.md) の P9 で「**1アプリ内に複数子を持ち、
会話で対象児を解決**」「夫婦など数名の LINE ユーザーが同じ子を記録・参照」「
`KOTOLOG_DEFAULT_CHILD` 撤廃・user–child データモデルへ移行・**マイグレーション基盤導入**」
を決定済み（[ADR-0005](0005-multi-child-strategy.md) の案A/案B は棄却）。本 ADR はその
具体設計（T9.1）を定める。

### 現状の制約

- `children`（id・name_alias・birthday）は複数行可だが、実行時は起動時 1 人に固定。
  `build_agent()` が `ensure_child(config.default_child)` で 1 人を決め、`ToolExecutor`
  に `child_id` を焼き付ける。`_agent` はプロセス singleton。
- ユーザーは `settings.line_user_id` 1 件のみ（webhook で最後に話した人が上書き）。
  `users` テーブルは未実装（ADR-0004 で計画のみ）。`sessions` は dead。
- マイグレーションの仕組みは無い（`init_db()` が `schema.sql` を
  `CREATE TABLE IF NOT EXISTS` で流すだけ）。本番は Turso、ローカル/テストは sqlite3。
- 接続層（`connection.py`）は sqlite3 と `_LibsqlConn` の両対応。両者とも
  `execute` / `executescript` / `commit` を持つ。

### スコープ（PO の線引き）

「**1 世帯・LINE ユーザー数名・子ども複数**」に限定。招待リンク・認証・権限ロール・
ユーザー×子のアクセス制御は**作らない**（全ユーザーが全子を記録・参照できる）。

## 決定

### 1. データモデル（1 世帯フラット）

ユーザー×子の関連テーブルは作らない（全員が全子にアクセス）。必要なのは
「子の複数管理」「ユーザーの登録」「各ユーザーの現在の対象児」「世帯の既定児」。

```sql
-- 既存 children は流用（必要なら表示順のため birthday を活用）
-- 例: id, name_alias, birthday

-- 新規 users（ADR-0004 の users を本 ADR で確定。current_child_id を追加）
CREATE TABLE IF NOT EXISTS users (
    line_user_id     TEXT PRIMARY KEY,
    nickname         TEXT,
    notify_enabled   INTEGER NOT NULL DEFAULT 1,
    current_child_id INTEGER REFERENCES children(id) ON DELETE SET NULL,  -- 会話の現在の対象児
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);

-- 世帯の既定児は settings に保持（単一世帯のため十分）
--   key='default_child_id', value=<children.id>
```

- **既定児**: `settings.default_child_id`。最初の子作成時に自動設定。
- **ユーザー別の現在の対象児**: `users.current_child_id`（「○○に切り替え」で更新）。

### 2. 対象児の解決（決定論・LLM に推測させない）

1 メッセージごとに以下の**優先順**で `child_id` を決める。

1. **メッセージ内の明示参照**（「上の子の」「<ニックネーム>の」）→ **コードで決定論的に**
   children へマッピング（LLM は参照語の抽出のみ。id 推測はさせない）。
2. 無ければ **そのユーザーの `current_child_id`**。
3. 無ければ **世帯の `default_child_id`**。
4. 子が 1 人だけなら常にその子。

- **「上の子／下の子」** は登録児を `birthday` 昇順で並べて解決。ただし誕生日が
  NULL や同一（双子等）でも順序が安定するよう、**`id` 昇順をタイブレークに必ず併用**する
  （`ORDER BY birthday ASC NULLS LAST, id ASC` 相当。NULL は末尾固定）。
- 明示参照が**どの登録児にも一致しない**場合のみ聞き返す（「どの子？」）。未指定は
  既定児へフォールバック（聞き返さない）= ロードマップ v2 の原則。
- **抽出スキーマ拡張**: `extractor` の各レコードに任意フィールド `child`（参照語）を
  追加。マッピングは executor 手前の解決層が担当（ルールベース）。

### 3. executor / agent のリクエスト単位 child 束縛

- `ToolExecutor` の `child_id` 起動時固定をやめ、**メッセージごとに解決した `child_id`
  で executor を構成**（conn は共有・executor は軽量）。`_agent` singleton が
  `executor.child_id` を書き換える方式は採らない（webhook の BackgroundTask は別スレッド
  で動くため共有状態の書き換えは競合する）。
- 解決層 `resolve_child_id(conn, user_id, message_ref) -> int` を 1 か所に置く。

### 4. マイグレーション基盤（sqlite3 / libsql 両対応・前進のみ）

```sql
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);
```

- `migrations/` に**連番の前進マイグレーション**（`0001_baseline.sql`,
  `0002_users_and_default_child.sql`, …）を置く。
- 起動時 `migrate(conn)` が `schema_migrations` の最大適用版より新しいものを順に適用し、
  各版を 1 トランザクションで実行して版を記録。`PRAGMA user_version` は使わず
  テーブル方式（libsql 互換・明示的）。
- **既存 DB の扱い**: `schema_migrations` が空でも、新規空 DB と既存 DB は区別する。
  `sqlite_master` で既存テーブル（例: `records` / `children`）の有無を確認し、
  **既にテーブルがある場合のみ** baseline (`0001`) を「適用済み」としてスタンプする。
  テーブルが無い新規 DB では baseline をスキップせず通常適用する（スタンプ誤りを防ぐ）。
- `schema.sql` は baseline（`0001`）として位置づけ、以降の変更は必ずマイグレーションで行う。

## 理由

- 単一世帯のため user×child の関連や ACL は不要。フラット構成が最小で要件を満たす。
- 対象児解決を決定論にすることで「LLM が勝手に別の子へ記録する」事故を防ぐ
  （育児データの取り違えは実害が大きい）。
- リクエスト単位の child 束縛は、複数子・複数ユーザー・別スレッド実行と整合する。
- マイグレーション基盤は今後の項目追加（P10）でも効く。テーブル方式は Turso/libsql でも
  確実に動く。

## 却下した案の理由

**user×child 関連テーブル＋権限。** 1 世帯では過剰（PO 決定）。将来本当に必要なら
そのとき追加する。

**対象児を LLM に id まで推測させる。** 取り違えリスク。参照語の抽出までに留め、
id 解決はコードで行う。

**`PRAGMA user_version` でのバージョン管理。** 暗黙的で、libsql 挙動差の懸念。
明示的な `schema_migrations` テーブルにする。

**executor.child_id を singleton で都度書き換え。** 別スレッド実行で競合。
リクエスト単位 executor 構成にする。

## トレードオフ・リスク

- **既存本番 DB のスタンプ**: 初回マイグレーション導入時、baseline を適用済みと
  みなす処理を誤ると二重適用の恐れ。`IF NOT EXISTS`／冪等 SQL を徹底し、テストで担保。
- **対象児フォールバック**: 未指定→既定児は便利だが、切替忘れで別の子に記録される
  可能性。確認文に**対象児名を必ず含める**（「太郎: ミルク120…」）ことで気づけるようにする。
- **`settings.default_child_id` と `users.current_child_id` の二系統**: 単一世帯では
  許容。複雑化したら見直す。
- **PII**: `line_user_id`・`nickname` を保持。実名回避（NFR-4）・トークン保護・
  計測ログに平文を残さない（→ [[project-pii-check]]）。

## 実装計画（P9 / T9.1〜）

> 本 ADR は T9.1（設計）。以降の実装タスクは開発計画 P9 に対応。

1. **マイグレーション基盤**（T9.2）
   - `db/migrations/` ＋ `migrate(conn)`、`schema_migrations` テーブル、起動時呼び出し
     （`build_agent`／`init_db` 経路）。baseline スタンプ処理。
2. **スキーマ変更**（T9.2/T9.3）
   - `0002`: `users` 作成、`settings.default_child_id` 運用開始。
3. **children 複数対応＋既定児**（T9.3）
   - 子の登録・一覧・既定児設定 crud。`KOTOLOG_DEFAULT_CHILD` 撤廃
     （`build_agent` は既定児を DB から解決）。
4. **対象児解決層＋executor リクエスト単位化**（T9.4）
   - `resolve_child_id(conn, user_id, child_ref)`、`extractor` に `child` フィールド、
     `Agent.handle` で解決→per-request `ToolExecutor`。確認文に対象児名を付与。
5. **ユーザー管理 UI・自動登録・Push fan-out**（T9.5/T9.6、ADR-0004 を内包）
   - webhook で `upsert_user`、`/admin` メニュー＋ユーザー管理、通知 ON/OFF、
     通知 ON ユーザーへ fan-out、「○○に切り替え」で `current_child_id` 更新。
6. **テスト**（T9.7）
   - マイグレーション（新規 DB / 既存 DB スタンプ / 二重適用しない）
   - 対象児解決（明示参照・current・default・単一児・不一致は聞き返し）
   - per-request child（別スレッドで取り違えない）
   - 共有（複数ユーザーが同一子を参照）・管理 UI・トークン保護

### 受け入れ条件（DoD）

1. 子を複数登録でき、会話で対象児が決定論的に解決される（未指定は既定児）
2. 確認文に対象児名が含まれる
3. 数名の LINE ユーザーが同じ子を記録・参照できる（自動登録・ニックネーム・通知 ON/OFF）
4. マイグレーションが新規 DB・既存本番 DB の双方で安全に適用される（冪等・前進のみ）
5. `KOTOLOG_DEFAULT_CHILD` への依存が無くなる

## 参考

- 棄却 ADR: [ADR-0005](0005-multi-child-strategy.md)（子＝LINEアカウント固定）
- 内包 ADR: [ADR-0004](0004-admin-menu-and-user-management.md)（管理画面・ユーザー管理）
- 接続層: `src/kotolog/db/connection.py`（sqlite3 / libsql 両対応）
