# ADR-0001: スケジューラーはアプリ内蔵の APScheduler を使う

- **日付:** 2026-06-21
- **状態:** 採用

## コンテキスト

毎朝7時のカウントダウン Push・毎晩21時の日次サマリー Push を定期実行する必要がある。
実行基盤として以下の選択肢を検討した。

| 案 | 概要 |
|---|---|
| A | APScheduler を FastAPI の lifespan 内で起動（アプリ内蔵） |
| B | Render Cron Job サービス（別コンテナ）から `/trigger/*` を HTTP 呼び出し |
| C | 外部 keep-alive（UptimeRobot）+ APScheduler |

## 決定

**案 A（APScheduler アプリ内蔵）を採用する。**

## 理由

- 追加サービス・外部依存ゼロで実装できる
- コード・設定の変更箇所が `scheduler.py` と `webhook.py` の lifespan のみに閉じる
- Render の有料プラン（Always-On）前提のため、Web Service がスリープする問題は発生しない

## 却下した案の理由

**案 B（Render Cron Job → HTTP）:** Cron Job が HTTP リクエストを送る先の Web Service がスリープ中の場合、起動に約30秒かかりタイムアウトするリスクがある。また render.yaml に別サービス定義が必要で構成が複雑になる。

**案 C（UptimeRobot + APScheduler）:** 外部サービスへの依存が生まれる。無料プランで運用する場合の次善策として有効だが、現時点では不要。

## トレードオフ・リスク

- Web Service の再起動（デプロイ・クラッシュ）でスケジューラーも再起動される。再起動直後に発火予定だったジョブは skip される可能性がある（許容範囲）
- 無料プランへ移行する場合はスリープ問題が発生するため、案 C への切り替えを検討すること
