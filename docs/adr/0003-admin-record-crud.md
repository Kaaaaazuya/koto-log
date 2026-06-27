# ADR-0003: 管理画面での記録 CRUD（AIなし手動編集）

- **日付:** 2026-06-26
- **状態:** 採用（実装済み・P8）

## コンテキスト

記録の追加・修正・取り消しは現状 LINE 経由の AI（`update_or_delete_record` ツール）
だけで、しかも対象は「直近1件（last）」に限られる。過去の任意の記録を直接
直したい・消したい・手で足したいケース（取りこぼし入力、時刻の打ち間違い、
AI が誤抽出したレコードの修正）に手段がない。ダッシュボード（`/dashboard`）は
閲覧専用。

一方で **DB 層（`crud.py`）には CRUD 関数が既に揃っている**
（`insert_record` / `get_record` / `update_record` / `delete_record` / `query_records`）。
不足しているのは「AI を介さない手動 UI」だけ。

既存の `/admin`（`line/admin.py`）が、トークン認証・Jinja2・POST→Redirect(PRG)
パターンで設定編集 UI を提供している。

## 決定

**`/admin` を拡張し、AI を介さない記録 CRUD 画面を追加する。**

1. **配置**: 新ルータを作らず `line/admin.py` に記録 CRUD のエンドポイントを足す。
   - `GET  /admin/records` 一覧（期間・種別フィルタ、各行に編集/削除）
   - `GET  /admin/records/new` ＋ `POST /admin/records` 追加
   - `GET  /admin/records/{id}/edit` ＋ `POST /admin/records/{id}` 更新
   - `POST /admin/records/{id}/delete` 削除（確認 → POST）
2. **時刻入力**: HTML `datetime-local` を使い、`YYYY-MM-DDTHH:MM` を
   `YYYY-MM-DDTHH:MM:00+09:00`（JST）へ変換して保存する。**AI・相対表現解析は使わない。**
3. **認証**: 既存の `KOTOLOG_DASHBOARD_TOKEN`（`_check_token`）を流用する。
4. **永続化/実行**: 既存 `crud.*` をそのまま呼ぶ。スキーマ変更なし。
5. **対象の子**: 単一児（`baby`）前提。ダッシュボードと同じ `child_id` を使う。
   ※ 複数子は「子＝LINEアカウント固定／当面は案B（子ごとに別インスタンス）」とする
   （[ADR-0005](0005-multi-child-strategy.md)）。本 ADR の単一児前提は案B で有効。
   案A 移行時に一覧・フォームへ子セレクタ（`?child_id=`）を追加し、子解決を S1 経由にする。

## 理由

- DB 層が完成しているため、UI 追加だけで最小実装できる
- `/admin` の既存パターン（認証・PRG・テンプレート）に乗れば一貫性が保てる
- `datetime-local` は分単位で確実に入力でき、管理用途に合う（曖昧さを持ち込まない）
- AI を通さないので、誤抽出レコードの「確実な」修正手段になる
  （AI 修正ループ `update_or_delete_record` と補完関係）

## 却下した案の理由

**案: 専用アプリ/ルータを別に立てる。**
認証・テンプレート・接続取得を二重化する。`/admin` 拡張で十分。

**案: 時刻入力に `utils.timeparse`（相対表現）を流用する。**
「3時」「さっき」も打てて便利だが、管理画面の目的は*正確な*修正であり、
解析の曖昧さを持ち込みたくない。`datetime-local` で確定値を入れる方針とする。

**案: 記録 CRUD 専用に別トークンを設ける。**
運用が煩雑になる。現状はダッシュボードと同じ保護レベルで足りる。

## トレードオフ・リスク

- **共有トークン**: ダッシュボード閲覧と記録編集が同一トークンで保護される。
  閲覧だけ渡して編集は渡さない、という分離はできない（現状要件では許容）。
- **CSRF 非対策**: 認証はクエリ文字列トークンのみで、CSRF トークンは持たない。
  個人運用・限定共有のため許容するが、公開範囲を広げる場合は要再検討。
- **単一児前提**: 複数児対応時はレコード一覧・フォームに子選択が必要になる。
- **削除は不可逆**: 確認ステップ（confirm 画面 or 二段 POST）を必須とする。

---

## 実装計画（別セッションで着手可能）

### エンドポイント設計

| メソッド・パス | 役割 | 使う crud |
|---|---|---|
| `GET /admin/records` | 一覧（`?days=` 期間・`?type=` 種別フィルタ） | `query_records` |
| `GET /admin/records/new` | 追加フォーム表示 | — |
| `POST /admin/records` | 追加実行 → PRG | `insert_record` |
| `GET /admin/records/{id}/edit` | 編集フォーム（既存値プリフィル） | `get_record` |
| `POST /admin/records/{id}` | 更新実行 → PRG | `update_record` |
| `POST /admin/records/{id}/delete` | 削除実行 → PRG | `delete_record` |

全エンドポイントで先頭に `_check_token(token)`。更新後は
`RedirectResponse(".../admin/records?token=...&saved=1", 303)`（既存 PRG に倣う）。

### 時刻変換（AIなし）

```python
JST = timezone(timedelta(hours=9))

def _to_iso_jst(dt_local: str) -> str:
    # "2026-06-26T21:30" -> "2026-06-26T21:30:00+09:00"
    return datetime.fromisoformat(dt_local).replace(tzinfo=JST).isoformat()

def _to_input_value(iso: str) -> str:
    # 既存 ISO -> datetime-local の value（編集フォームのプリフィル用）
    return datetime.fromisoformat(iso).strftime("%Y-%m-%dT%H:%M")
```

`ended_at` は任意（空なら None）。`amount` は空なら None、数値はそのまま。

### 入力バリデーション

- `type` は `RecordType` enum のいずれか（不正は 400 か再表示）
- `started_at` 必須
- `sub_type` は任意（必要なら `utils.subtype` のルールベース正規化を流用可。AIではない）
- 許可カラムは `crud._UPDATABLE` に準拠（更新は既存ガードがあるため安全）

### フォーム項目

種別（select）/ サブ種別（text）/ 量（number）/ 単位（text, 既定 ml）/
開始（datetime-local, 必須）/ 終了（datetime-local, 任意）/ メモ（text）

### タスク分解

| ID | 内容 | 完了条件 |
|---|---|---|
| T8.1 | `GET /admin/records` 一覧＋フィルタ・編集/削除リンク | 期間/種別で絞り込み表示できる |
| T8.2 | 追加フォーム＋ `POST /admin/records` | 1件追加され DB に入る（時刻が JST ISO） |
| T8.3 | 編集フォーム（プリフィル）＋ `POST /admin/records/{id}` | 既存値が入り、更新が反映される |
| T8.4 | 削除（確認 → `POST .../delete`） | 確認後に1件削除される |
| T8.5 | テンプレート（`admin_records.html` 等）・テスト・トークン保護 | 下記テスト全通過 |

### テスト方針（`tests/unit/test_dashboard.py` / 既存 admin テストのパターン）

- トークン無し/誤りで 403
- 一覧が記録を表示（`query_records` をモック or インメモリDB）
- 追加 POST で `insert_record` が呼ばれ、`datetime-local` → `+09:00` 変換が正しい
- 編集 POST で `update_record` が許可カラムのみ更新
- 削除 POST で `delete_record` が呼ばれる
- 編集フォームのプリフィル値（ISO → `YYYY-MM-DDTHH:MM`）が正しい

### 受け入れ条件（Definition of Done）

1. `/admin/records?token=...` で記録の一覧・追加・編集・削除が UI から行える
2. AI・LiteLLM を一切経由しない（`crud.*` 直呼び）
3. 時刻は JST ISO8601 で保存され、ダッシュボード表示と整合する
4. トークン未設定時はアクセス可、設定時は誤トークンで 403
5. 削除は確認ステップを経る

## 参考

- 既存設定 UI（PRG・トークン認証の手本）: `src/kotolog/line/admin.py`
- DB CRUD（実装済み）: `src/kotolog/db/crud.py`
- 関連 ADR: [ADR-0002](0002-token-usage-measurement.md)（計測, P7）
