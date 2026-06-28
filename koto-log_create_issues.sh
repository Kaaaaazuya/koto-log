#!/usr/bin/env bash
#
# koto-log ロードマップ v2 を GitHub Issue として一括作成するスクリプト
#
# 前提:
#   - gh CLI がインストール済み (https://cli.github.com/)
#   - `gh auth login` 認証済み（koto-log への issue 作成権限があること）
#
# 使い方:
#   REPO=Kaaaaazuya/koto-log bash koto-log_create_issues.sh
#   （REPO 未指定時はカレントの git リポジトリを使用）
#
set -euo pipefail

REPO="${REPO:-}"
REPO_ARG=()
if [[ -n "${REPO}" ]]; then
  REPO_ARG=(--repo "${REPO}")
fi

echo "==> ラベルを作成（既存ならスキップ）"
create_label() {
  gh label create "$1" "${REPO_ARG[@]}" --color "$2" --description "$3" 2>/dev/null \
    || echo "    label '$1' は既存のためスキップ"
}
create_label "epic"          "5319e7" "全体トラッキング"
create_label "type:feature"  "0e8a16" "基本機能の追従 (PdM観点)"
create_label "type:tech-debt" "d93f0b" "技術的負債の解消 (テックリード観点)"
create_label "priority:now"  "b60205" "最優先"
create_label "priority:next" "fbca04" "次に着手"
create_label "priority:later" "c2e0c6" "後回し/任意"
create_label "phase:P9"  "1d76db" "フェーズP9"
create_label "phase:P10" "1d76db" "フェーズP10"
create_label "phase:P11" "1d76db" "フェーズP11"
create_label "phase:P12" "1d76db" "フェーズP12"
create_label "phase:P13" "1d76db" "フェーズP13"
create_label "phase:P14" "1d76db" "フェーズP14"

echo "==> Issue を作成"

gh issue create "${REPO_ARG[@]}" \
  --title "[EPIC] ロードマップ v2 トラッキング" \
  --label "epic" \
  --body "$(cat <<'BODY'
個人・家族用ツールとしての koto-log ロードマップ(v2)の親 Issue。各フェーズの進捗をここで俯瞰する。

方針(3者合意):
- PdM: 不足する基本機能の追従は MUST。
- テックリード: 機能の前に構造的負債を返す。ただし個人ツールゆえ過剰設計は避ける。
- PO: スコープ外=マルチテナント/自治体連携/課金/ML付加価値/製本。各フェーズに「負債1×機能1」を同居。

- [ ] P9 基盤の最小刷新 + 子ども複数・夫婦共有(軽量)
- [ ] P10 記録項目の拡充(自家で使う範囲)
- [ ] P11 成長記録・成長曲線
- [ ] P12 予防接種・健診リマインド
- [ ] P13 リマインダー・振り返り高度化 + コスト可観測
- [ ] P14 保守・品質の継続改善
BODY
)"

gh issue create "${REPO_ARG[@]}" \
  --title "P9. 基盤の最小刷新 + 子ども複数・夫婦共有(軽量)" \
  --label "phase:P9,type:feature,type:tech-debt,priority:now" \
  --body "$(cat <<'BODY'
## 背景
現状は KOTOLOG_DEFAULT_CHILD=baby の単一子ども前提・単一 LINE ユーザー前提。家族で使うには共有と複数子ども対応が必要で、これはデータモデルの根(user–child)に関わる構造的負債。機能を増やす前の今が最も安く移行できる。

## 基本機能 (PdM)
- [ ] 子どもを複数登録できる
- [ ] 夫婦など数名の LINE ユーザーが同じ子を記録・参照できる
- [ ] 会話で対象児を切替(未指定は既定児にフォールバック、LLM に推測させない)

## 返す負債 (テックリード)
- [ ] KOTOLOG_DEFAULT_CHILD 前提を撤廃
- [ ] user–child データモデルへ移行
- [ ] スキーママイグレーションの仕組みを導入
- [ ] ADR を作成(データモデル/マイグレーション方針)

## スコープ外 (PO)
- 招待リンク基盤・認証・権限ロールは作らない。admin での LINE ユーザー登録(数名)で十分。

## 完了条件
- 子ども2人+ユーザー2人で記録・集計・修正が破綻なく動く。既存データが無損失に移行できる。
BODY
)"

gh issue create "${REPO_ARG[@]}" \
  --title "P10. 記録項目の拡充(自家で使う範囲)" \
  --label "phase:P10,type:feature,type:tech-debt,priority:now" \
  --body "$(cat <<'BODY'
## 背景
記録対象が授乳・睡眠・おむつ・体温の4種のみ。自然言語入力なので UI を増やさず項目を足せるのが強み。無計画に増やすと正規化と eval が膨らむため自家で使う範囲に絞る。

## 基本機能 (PdM)
- [ ] 離乳食・薬・病院・お風呂・搾乳・授乳の左右など、家族が使う項目を選定して追加

## 返す負債 (テックリード)
- [ ] RecordType enum・ツール定義・subtype.py 正規化辞書を整理
- [ ] 追加項目ごとに eval ケースを並行追加(回帰防止)

## スコープ外 (PO)
- 全網羅はしない。使われない項目は足さない。

## 完了条件
- 追加した各項目が自由文から正しく構造化保存され、対応する eval が green。
BODY
)"

gh issue create "${REPO_ARG[@]}" \
  --title "P11. 成長記録・成長曲線" \
  --label "phase:P11,type:feature,priority:now" \
  --body "$(cat <<'BODY'
## 背景
身長・体重と成長曲線は親の関心が高い定番機能。ダッシュボード(P4)の Chart.js 基盤を再利用できる。

## 基本機能 (PdM)
- [ ] 身長・体重を記録できる
- [ ] 厚労省/WHO 標準値と比較する成長曲線グラフ
- [ ] /dashboard に成長タブを追加

## 返す負債 (テックリード)
- [ ] グラフ生成ロジックを共通化(既存サマリーグラフと重複を排除)

## スコープ外 (PO)
- 標準値は静的データ同梱。外部連携しない。

## 完了条件
- 記録した身長体重が成長曲線上にプロットされ、標準値帯と比較表示される。
BODY
)"

gh issue create "${REPO_ARG[@]}" \
  --title "P12. 予防接種・健診リマインド" \
  --label "phase:P12,type:feature,type:tech-debt,priority:next" \
  --body "$(cat <<'BODY'
## 背景
プロアクティブ通知(P5)の強みが最も活きる領域。生年月日からスケジュールを生成し LINE Push で先回りする。

## 基本機能 (PdM)
- [ ] 生年月日から予防接種スケジュールを生成
- [ ] 接種前・健診前に LINE Push でリマインド
- [ ] 接種済みを記録

## 返す負債 (テックリード)
- [ ] APScheduler のジョブを拡張
- [ ] スケジュール定義を設定データとして外部化(改定追従しやすく)

## スコープ外 (PO)
- デジタル予診票・自治体連携には踏み込まない。

## 完了条件
- 子の生年月日設定後、次回接種が正しい日付で Push される。
BODY
)"

gh issue create "${REPO_ARG[@]}" \
  --title "P13. リマインダー・振り返り高度化 + コスト可観測" \
  --label "phase:P13,type:feature,type:tech-debt,priority:next" \
  --body "$(cat <<'BODY'
## 背景
「記録するアプリ」から「先回りして助けるアプリ」へ。あわせて個人の API 課金を見える化する。

## 基本機能 (PdM)
- [ ] 授乳・おむつの間隔リマインダー(前回から N 時間)
- [ ] 月次サマリーの自動生成(LLM 文章+グラフ)
- [ ] 写真付き日記(任意・ストレージコスト見て判断)

## 返す負債 (テックリード)
- [ ] トークンコストの軽量可観測(operation 別 JSON ログ=P7 の延長)
- [ ] プロンプト/モデル選択のコスト最適化

## スコープ外 (PO)
- Langfuse 等の本格可観測基盤は入れない。

## 完了条件
- 間隔リマインダーが動作し、月次サマリーが生成される。operation 別トークンコストがログで確認できる。
BODY
)"

gh issue create "${REPO_ARG[@]}" \
  --title "P14. 保守・品質の継続改善(Later・任意)" \
  --label "phase:P14,type:tech-debt,priority:later" \
  --body "$(cat <<'BODY'
## 背景
1人保守の個人ツールとして「壊れにくく・直しやすい」状態を維持するための薄く長い活動。

## 内容
- [ ] eval の継続拡充とツール選択の回帰テスト自動化
- [ ] 依存ライブラリの定期更新
- [ ] agent ループのフォールバック解析の整理/削減(精度が安定したら簡素化)

## 完了条件
- 主要シナリオの eval が CI で自動実行され、回帰を検知できる。
BODY
)"

echo "==> 完了。GitHub の Issues タブを確認してください。"
