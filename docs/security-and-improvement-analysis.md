# koto-log セキュリティ・機能改善分析

2026-07 時点のコードベース（P11 完了、main = `cba9d55`）を対象に、
(1) セキュリティを含む改善、(2) アプリケーション・機能の改善、の2観点で分析した結果。

## サマリー

全体として設計品質は高い。パラメタライズド SQL、`update_record` のカラム許可リスト、
webhook の HMAC 署名検証（`compare_digest` 使用）、Jinja2 の自動エスケープ、
pip-audit / bandit / Dependabot の CI、SHA ピン留めされた GitHub Actions など、
基本的な守りは既にできている。

一方で、**認証・認可まわりに構造的な弱点が集中**している。特に
「トークン未設定時に管理画面が完全無認証」「LINE 友だち追加だけで誰でも家族データに
フルアクセス」「認証トークンが URL クエリ」の3点は優先度が高い。

---

## 1. セキュリティ改善

### 優先度: 高

#### S-1. 管理画面・ダッシュボードがトークン未設定時に無認証（フェイルオープン）

`admin.py` / `dashboard.py` の `_check_token`:

```python
expected = os.environ.get("KOTOLOG_DASHBOARD_TOKEN", "")
if expected and token != expected:
    raise HTTPException(status_code=403)
```

`KOTOLOG_DASHBOARD_TOKEN` が未設定だと `/admin` `/admin/users` `/admin/records` が
**認証なしで公開**される。README にも「未設定で認証なし」と明記されているが、
`/admin/users` には LINE ユーザー ID（個人識別子）、`/admin/records` には
体温・薬・病院受診などの健康情報が含まれ、削除・改変も可能。
本番 URL（`koto-log.onrender.com`）は推測可能なため、設定漏れ = 即インシデント。

**対策**: フェイルクローズに変更する。トークン未設定なら管理系ルートは 403
（またはアプリ起動時にエラー）。「認証なしモード」が必要ならローカル開発用の
明示フラグ（例 `KOTOLOG_AUTH_DISABLED=1`）に限定する。

#### S-2. 認証トークンが URL クエリパラメータ

`?token=xxx` 方式は以下の経路で漏洩する:

- アクセスログ（Render / プロキシ）・ブラウザ履歴・共有時の URL コピペ
- 外部 CDN（Google Fonts / jsDelivr）読み込み時の Referer
  （最新ブラウザの既定 Referrer-Policy ではクエリは落ちるが、保証はない）

また全テンプレートがリンク・フォーム action にトークンを埋め込んでおり、
漏洩面が広い。

**対策**: Cookie ベースのセッション（`/login` で一度トークンを入力 →
`HttpOnly; Secure; SameSite=Lax` Cookie 発行）へ移行。
最低限でも `Referrer-Policy: no-referrer` ヘッダを付与する。
あわせて比較を `hmac.compare_digest(token or "", expected)` にしてタイミング攻撃を防ぐ。

#### S-3. LINE 友だち追加だけで誰でも家族データにフルアクセス

`webhook.py` の `upsert_user` は届いた `userId` を無条件に登録し、
そのままエージェントが記録の閲覧・追加・修正・削除（「さっきのなし」）を実行できる。
Bot の QR コード / Bot ID を知った第三者が友だち追加すると、
子どもの健康情報の閲覧も記録の破壊もできてしまう。
さらに `notify_enabled=1` が既定なので、毎朝・毎晩の Push で家庭のサマリーが届く。

**対策**: 承認制にする。
- 新規ユーザーは `approved=0` で登録し、返信は「管理者の承認待ちです」に固定
- `/admin/users` に承認トグルを追加（既にユーザー管理画面はあるので拡張は小さい）
- もしくは招待コード方式（「招待 XXXX」と送ると有効化）
- `notify_enabled` の既定も承認後に ON へ

#### S-4. `set_config` ツールの key 検証が実行側にない

`definitions.py` では `enum: ["due_date"]` だが、JSON Schema の enum は LLM への
ヒントに過ぎず強制力がない。`executor.py::_set_config` は受け取った key を
そのまま `settings` に書くため、プロンプトインジェクション（あるいは LLM の誤動作）で
`default_child_id` や `line_user_id` など**任意の設定キーを上書き**できる。
S-3 と組み合わさると、任意の LINE ユーザーが世帯全体の設定を壊せる。

**対策**: executor 側で `CONFIG_KEYS` に対する許可リスト検証を行い、
許可外は `{"ok": False, "reason": "unsupported key"}` を返す。

#### S-5. `LINE_CHANNEL_SECRET` 未設定時に空鍵 HMAC で署名検証が通る

`webhook.py` は secret 未設定時に `channel_secret=""` で HMAC を計算する。
空文字鍵は誰でも計算できるため、**secret 未設定のデプロイでは任意の偽 webhook が
「署名検証済み」として通る**。

**対策**: secret が空なら即 401/500 を返す（フェイルクローズ）。1行の修正。

### 優先度: 中

#### S-6. 管理系 POST に CSRF 対策がない

削除・更新エンドポイント（`/admin/records/{id}/delete` 等）は CSRF トークンなし。
現状はクエリの `token` が実質の防御だが、S-1 の無認証運用時はゼロ、
S-2 でトークンが漏れた場合も突破される。Cookie セッション化（S-2）とセットで
CSRF トークン（または `SameSite=Strict`）を導入する。

#### S-7. SQLite 単一コネクションをロックなしでスレッド間共有

`connection.py` は `check_same_thread=False` で単一コネクションを作り、
webhook の BackgroundTask（別スレッド）・admin・dashboard が同じ接続を共有する。
書き込みが並行すると `sqlite3.ProgrammingError` や中途半端な commit の混線が起こり得る。

**対策**: 最小修正なら `threading.Lock` を接続ラッパに導入。
より良いのはリクエストごとの接続（SQLite はファイル DB なので接続コストは小さい）。
Turso 側（`_LibsqlConn`）も同様。

#### S-8. CDN 依存に SRI がなく、バージョンがフローティング

`chart.js@4`（メジャーバージョンのみ固定）を jsDelivr から読み込んでいる。
CDN 側の改竄・侵害時にダッシュボードで任意 JS が実行される（トークン窃取に直結）。

**対策**: 完全なバージョン固定 + `integrity`/`crossorigin` 属性、
またはセルフホスト（静的ファイル同梱）。

#### S-9. セキュリティヘッダが皆無

CSP / `X-Frame-Options` / `X-Content-Type-Options` / `Referrer-Policy` がない。
FastAPI のミドルウェアで一括付与できる（10行程度）。ダッシュボードは
inline script を使うため CSP は段階導入でよいが、`Referrer-Policy` と
`X-Frame-Options: DENY` は即入れられる。

#### S-10. コンテナが root で実行される

Dockerfile に `USER` 指定がない。`RUN useradd -m app` + `USER app` を追加する。

#### S-11. レート制限・コスト制御がない

- webhook 経由の LLM 呼び出しにユーザー毎・時間毎の制限がない。
  S-3 の未承認ユーザーでも API コスト（Claude 課金）を消費させられる
- `/admin/test-push` は LLM 呼び出し + 全ユーザー Push を発火する

**対策**: ユーザー毎の簡易レートリミット（例: 20 msg/分で「少し待ってね」返信）、
1日あたりの LLM 呼び出し上限。

### 優先度: 低

- **S-12** `processed_events` が無限成長する。スケジューラに「7日より古い行を削除」
  ジョブを追加
- **S-13** `is_processed` → `mark_processed` が非原子的（TOCTOU）。
  `INSERT OR IGNORE` の `rowcount` で「初回かどうか」を1文で判定できる。
  また処理**前**に mark するため、処理が例外で落ちるとその LINE メッセージは
  返信なしで永久に失われる（LINE の再送も冪等化で弾かれる）。
  「成功後に mark」または「失敗時に mark を取り消す」へ
- **S-14** `reply.py` / `push.py` が HTTP ステータスを確認しない
  （`raise_for_status()` なし）。送信失敗が無音で握りつぶされる
- **S-15** スケジューラジョブ（`_run_morning_push` 等）が毎回 `connect` して
  close しない。接続リーク
- **S-16** 健康情報（体温・薬・受診歴）と LINE ユーザー ID を扱う旨の
  プライバシー方針・データ削除ポリシーがドキュメントにない。
  `delete_user` はユーザー行のみ削除で記録は残る（世帯モデル上は妥当だが、明文化を）

### 既にできている点（維持すべき）

- SQL はすべてパラメタライズド。文字列連結によるインジェクションなし
- `update_record` の `_UPDATABLE` 許可リストで任意カラム更新を防止
- webhook 署名検証に `hmac.compare_digest` を使用
- Jinja2 自動エスケープ有効。`| safe` は `json.dumps` 済みの数値データのみ
  （なお `{{ x | safe }}` より `{{ x | tojson }}` の方が `</script>` 分割にも安全）
- CI: pip-audit（CVE）+ bandit（SAST）+ Dependabot + Actions の SHA ピン留め
- `uv.lock` の `--frozen` インストールで依存を固定

---

## 2. アプリケーション・機能の改善

### 優先度: 高（既存機能の欠け・バグに近いもの）

#### A-1. 会話コンテキストが使われていない

`Agent.handle()` は `history` 引数を持ち、`sessions` テーブルもスキーマに存在するが、
webhook はどちらも使っていない。そのため LLM が「量はどれくらい？」と聞き返しても、
次のメッセージ「120」は文脈なしで処理され会話が成立しない。

**対策**: `sessions.recent_context` に直近 N 往復（2〜3往復で十分）を JSON 保存し、
`agent.handle(text, user_id, history=...)` に渡す。テーブルは既にある。

#### A-2. ダッシュボードの今日タブに P10 の新種別が出ない

`dashboard.py` のタイムラインは feeding / sleep / diaper のみ集めており、
`_ICONS` / `_TYPE_LABELS` も4種のみ。P10 で追加した離乳食・お風呂・薬・病院・外出と
P11 の身長・体重が「今日のまとめ」タイムラインに表示されない。
（21時サマリー Push の `build_daily_summary_text` も同様に4種のみ）

**対策**: `types.py` の `RECORD_TYPE_LABELS` を単一情報源にして、
全種別を1回のクエリ（type フィルタなし）で取得しタイムラインへ。

#### A-3. エラー時にユーザーへ何も返らない

`_handle_text_event` の `except` は `traceback.print_exc()` のみ。
LLM 障害や DB 障害時、ユーザーには完全な無反応に見える（S-13 と重なると記録も消える）。

**対策**: except 節で「ごめん、処理に失敗した。もう一度送って」を Reply する
（reply_token が生きている間）。あわせて `print` を `logging` の構造化ログへ。

#### A-4. 朝の Push が出産後に止まる

`_run_morning_push` は `remaining < 0` で return するため、
**赤ちゃんが生まれた瞬間から毎朝の便りが途絶える**。プロダクトの主用途は
出産後の育児記録なのに、朝の接点が妊娠期にしかない。

**対策**: `children.birthday` があれば「生後◯日（◯ヶ月◯日）」+ LLM 一言に切替。
due_date 超過・birthday 未設定なら送らない、のフォールバック順に。

### 優先度: 中（体験の底上げ）

#### A-5. チャット修正が「直近1件」限定

`update_or_delete_record` の target は `last` のみ。「朝のミルクを150に直して」や
「昨日の睡眠を消して」ができない。ツールに `type` / `time_hint`（timeparse で解決）を
追加し、該当候補が複数なら確認を返す設計にすると自然言語修正の実用度が大きく上がる。

#### A-6. Web UI の複数児対応が未完

P9 で複数児をチャット側は解決できるが、`/dashboard` `/dashboard/growth`
`/admin/records` は `get_default_child_id` 固定。子セレクタ（クエリ `?child=`）を追加する。

#### A-7. LINE UI の活用（クイックリプライ / Flex Message)

- 保存確認に「取り消す」クイックリプライを付ければ誤抽出の修正が1タップに
- 聞き返し（「母乳？ミルク？」）を選択肢ボタンに
- 21時サマリーを Flex Message でカード化

現在 `reply.py` は text 固定なので、messages 配列を渡せる形に拡張する。

#### A-8. 集計期間の拡張

`PERIODS` は today / yesterday / last_24h / last_7days / latest のみ。
「今週」「今月」「先月」や任意日付（「6月20日の記録」）は timeparse 資産を
流用して period 解決に追加できる。

#### A-9. データエクスポート

健診・小児科受診時に記録を持参する用途は定番。`/admin/records` に
CSV エクスポート（期間・種別フィルタ付き）を追加。実装コストは小さい。

### 優先度: 低（将来の伸びしろ）

- **A-10** 授乳間隔ベースのプロアクティブ通知（「前回から4時間経過」）。
  APScheduler 基盤は既にあるので interval ジョブ + 閾値設定で実現可能
- **A-11** ロードマップ済みの P12 予防接種リマインドは `children.birthday` +
  静的な接種スケジュール JSON（growth_standards.json と同じパターン）で実装できる
- **A-12** 可観測性: ADR-0002 の想定どおり Langfuse 等への sink 差し替え、
  応答レイテンシの計測（1秒 ACK 後のバックグラウンド処理時間）
- **A-13** タイムゾーン・言語が JST / 日本語ハードコード（個人利用なら現状維持で妥当）

---

## 推奨着手順

| 順 | 項目 | 理由 | 規模 |
|---|---|---|---|
| 1 | S-1 フェイルクローズ + S-5 空 secret 拒否 | 設定漏れ=即漏洩を1行〜数行で塞げる | 極小 |
| 2 | S-4 set_config 許可リスト | プロンプトインジェクション経路の遮断 | 極小 |
| 3 | S-3 ユーザー承認制 | 最大の認可ホール。admin/users 拡張で済む | 小 |
| 4 | A-2 新種別のダッシュボード表示 + A-3 エラー返信 | ユーザー体感の欠陥 | 小 |
| 5 | S-2/S-6 Cookie セッション + CSRF + S-9 ヘッダ | 認証基盤の作り直し | 中 |
| 6 | A-1 会話コンテキスト | 対話エージェントとしての核 | 中 |
| 7 | A-4 出産後の朝 Push / A-5 修正対象の拡張 | 継続利用の要 | 中 |
