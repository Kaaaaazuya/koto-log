# ADR-0005: 複数子対応の方針（案B 当面採用・案A 移行可能性を必須とする）

- **日付:** 2026-06-26
- **状態:** 採用（案B を当面採用 / 案A への移行を前提に設計を縛る）

## コンテキスト

複数の子を記録したい。子の判定方式として「**投稿を受け付ける LINE アカウント
（チャネル）で子を固定**する」方針を採用済み（メッセージ内で子を判定する複雑さを
回避できる）。

実装・運用モデルには2案ある。

| 案 | 概要 |
|---|---|
| **案A** | 1アプリで複数チャネルを捌く。webhook の `destination`（bot自身のID＝チャネル識別子）で振り分け、チャネル→(子・secret・token) をマッピング。DB・画面は1つで全子横断 |
| **案B** | 子1人 = 1デプロイ（別 Render サービス＋別 Turso DB＋別 env）。各インスタンスは単一子。コード変更ほぼゼロ |

現状のアーキは「子は起動時 `KOTOLOG_DEFAULT_CHILD` の単一値」で、
`build_agent()` が `ensure_child(default_child)` で1人を決め、`ToolExecutor` に
`child_id` を焼き付ける。LINE 認証情報（`LINE_CHANNEL_SECRET` /
`LINE_CHANNEL_ACCESS_TOKEN`）も env に1組のみ。**この構造は案B にそのまま使える。**

DB スキーマは既に複数子対応（`children` 複数行・`records.child_id` FK・crud 全関数が
`child_id` 引数）。

## 決定

**当面は案B（子ごとに別インスタンス）を採用する。ただし案A へ「書き換えではなく
局所変更」で移行できる設計を必須要件とする。**

すなわち、今後 P8（記録CRUD）・P9（管理画面/ユーザー管理）等を実装する際は、
案A の移行点になる箇所を**単一の継ぎ目（seam）に閉じ込める**こと。案B の段階では
seam は「現状の単一値を返すだけ」の薄い実装でよい。

### 移行可能性のための設計契約（必須）

以下を散在させず、各1か所の関数/抽象に閉じ込める。

| Seam | 案B での実装（今） | 案A での実装（将来） |
|---|---|---|
| **S1 子の解決** `resolve_child_id(conn, ctx)` | `ensure_child(config.default_child)` を返す | `ctx.destination` → `children` マッピングで解決 |
| **S2 チャネル認証情報** `resolve_channel(destination)` → `(secret, token)` | env の1組を返す（destination 無視） | `destination` 別のマッピングを返す |
| **S3 executor の子束縛** | 起動時に1回 child_id を束ねてよい | **リクエスト単位**で child_id を解決して executor を構成 |
| **S4 destination の可視化** | webhook で `destination` を取得しログ/記録 | マッピングのキーとして使用 |

設計上の含意:

- **webhook**: 署名検証・返信・child 解決を、上記 seam 経由で行う。
  `destination` は今のうちから読んでおく（S4）。署名検証は「`destination` で
  secret を選んで検証」という形にしておけば、案A で複数 secret 化しても webhook
  本体は変わらない（案B では候補が1つなだけ）。
- **executor / agent**: `_agent` シングルトン＋固定 child_id は案B では許容。ただし
  child_id の決定は `build_agent` 内に**直書きせず S1 を呼ぶ**形にしておき、案A で
  「リクエストごとに child_id 差し替え」へ広げられるようにする（S3）。
- **設定スキーマ**: 案B は `KOTOLOG_DEFAULT_CHILD` ＋ 単一認証情報のまま。案A は
  `LINE_CHANNELS`（JSON 等で `destination → {child, secret, token}`）へ拡張。
  config 読み込みを1か所にまとめ、両形態を吸収できる余地を残す。

## 理由

- 案B は**コード変更ほぼゼロ**で今すぐ複数子を運用開始でき、最小コスト
- seam を先に決めておけば、案A は「seam の中身差し替え＋webhook ルーティング＋
  画面の子セレクタ」に限定され、全面書き換えにならない
- DB は既に複数子対応のため、案A でもスキーマ移行は最小（`children` 行追加と
  destination マッピングのみ）

## 却下した案の理由

**案A を最初から実装する。** 認証情報のマッピング化・webhook ルーティング・
画面の子軸対応・users のチャネル別化が一度に必要で、現時点ではオーバー。
横断ダッシュボードが要るようになってから移行する。

**移行可能性を考えず案B を素朴に実装する。** 子・認証情報の参照がコード各所に
散ると、案A 移行が事実上の書き換えになる。seam 化を必須にしてこれを防ぐ。

## トレードオフ・リスク

- **案B のデータ分断**: 子ごとに DB・ダッシュボード・管理画面・登録ユーザーが
  分かれ、横断で見られない。これは案A 移行までの許容事項。
- **案B のインフラ増加**: 子の数だけ Render サービス＋Turso DB＋LINE 公式アカウント。
  少人数家族規模なら許容。
- **seam の形式的コスト**: 単一値を返すだけの関数を今書く手間。案A 移行コストとの
  トレードで採用。
- **案A 署名検証の順序**: `destination` は未検証 body から読む必要がある。
  「destination でキー選択 → そのキーで署名検証 → 検証後にのみ内容を処理」とし、
  未検証データで副作用を起こさない原則を守る（案B でも同じ手順で書いておく）。

## 既存 ADR との整合

- **ADR-0003（記録CRUD, 実装済み）**: 各インスタンス単一子の前提は案B で有効なまま。
  案A 移行時に一覧・フォームへ子セレクタ（`?child_id=`）を追加する。子の解決は S1 経由。
- **ADR-0004（ユーザー管理）**: 案B では `users` はインスタンス（=子）ごとに独立。
  案A では `users` が**チャネル別**になり、nickname/notify が (channel, user) 単位に
  なる。`users` 設計時に将来 `channel_id`（or destination）列を足せる前提にしておく。
- **ADR-0002（計測）**: 影響なし。ただし計測ログに `destination` を入れる場合は
  PII 方針（[[project-pii-check]]）に従い識別子の扱いに注意。

## 実装メモ（案B で「今」やること）

案B 運用自体は env 分離だけで動くが、移行可能性のために最小限の足場を入れる。

1. `resolve_child_id` / `resolve_channel` の seam 関数を用意し、`build_agent` と
   webhook をこれ経由にする（中身は現状の単一値）。
2. webhook で `destination` を取得し、ログ（または settings）に残す（S4）。
3. config 読み込みを1か所に集約し、将来 `LINE_CHANNELS` を足せる構造にする。
4. ドキュメント（README / .env.example）に「案B: 子ごとに別インスタンス／別 DB／
   別 LINE アカウント」「将来 案A（destination ルーティング）へ移行予定」を明記。

> 注: 上記 seam は P8/P9 を触るタイミングで併せて入れるのが効率的。新規の重い
> フェーズは設けず、各実装時の設計制約として適用する。

## 参考

- LINE webhook の `destination`（チャネル識別子）
- 関連: [ADR-0003](0003-admin-record-crud.md) / [ADR-0004](0004-admin-menu-and-user-management.md)
