#!/usr/bin/env python3
"""
開発ブランチの差分をもとに docs/user-manual.html を AI で最新化する。

使い方:
    python scripts/sync_manual.py <diff_path> <manual_path>

GitHub Actions の docs-sync.yml から呼び出される。
直接実行する場合:
    git diff HEAD~1..HEAD -- src/kotolog/ > /tmp/code.diff
    python scripts/sync_manual.py /tmp/code.diff docs/user-manual.html
"""

from __future__ import annotations

import sys
from pathlib import Path

import anthropic

# この値を超えると Haiku のコンテキストウィンドウを圧迫するため先頭を切り詰める
MAX_DIFF_BYTES = 60_000

SYSTEM_PROMPT = """\
あなたは koto-log（LINE 育児記録エージェント）のユーザーマニュアルを保守する担当者です。
マニュアルは日本語の HTML ファイルで、セクションは <!-- section: id --> と <!-- /section: id --> コメントで区切られています。

ルール:
1. ユーザー向けの変更（新機能・操作方法の変更・UI変更・新コマンド・新しい入力例）のみを反映する。
2. 内部実装・テスト・設定・型定義・リファクタリング・コメントのみの変更は無視する。
3. 変更が必要なセクションのみ書き換え、他のセクションは一切変更しない。
4. <!-- section: changelog --> に今回の変更を新しいエントリとして先頭に追加する（バージョン名はコミット差分から推定）。
5. ユーザー向けの変更が皆無なら HTML を一切変更せずそのまま返す。
6. HTML 全体を返す。Markdown コードブロック（```html など）や説明文は含めない。
7. <!-- section: xxx --> と <!-- /section: xxx --> のコメントは必ず保持する（AI 更新の目印）。
"""


def main(diff_path: str, manual_path: str) -> None:
    diff = Path(diff_path).read_text(encoding="utf-8")

    if not diff.strip():
        print("差分なし。スキップします。")
        return

    diff_bytes = diff.encode()
    if len(diff_bytes) > MAX_DIFF_BYTES:
        print(
            f"警告: 差分が {len(diff_bytes):,} バイトのため "
            f"先頭 {MAX_DIFF_BYTES:,} バイトに切り詰めます。"
        )
        diff = diff_bytes[:MAX_DIFF_BYTES].decode(errors="ignore")

    manual = Path(manual_path).read_text(encoding="utf-8")

    client = anthropic.Anthropic()
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",  # Claude Haiku 4.5（2025-10-01 snapshot）
        max_tokens=8192,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": (
                    "<diff>\n" + diff + "\n</diff>\n\n"
                    "<manual>\n" + manual + "\n</manual>"
                ),
            }
        ],
    )

    updated = response.content[0].text.strip()

    # 前置き文・Markdown フェンス・末尾の説明文を除去して HTML 部分のみ切り出す
    # （大文字小文字非依存・</html> を末尾の基準とする）
    updated_lower = updated.lower()
    doctype_start = updated_lower.find("<!doctype html>")
    html_end = updated_lower.rfind("</html>")

    if doctype_start != -1 and html_end != -1 and doctype_start < html_end:
        updated = updated[doctype_start:html_end + 7]  # len("</html>") == 7
    elif doctype_start != -1:
        updated = updated[doctype_start:]
        if updated.endswith("```"):
            updated = updated[:-3].strip()
    else:
        print("ERROR: レスポンスに <!DOCTYPE html> が含まれていません。中止します。")
        print(updated[:500])
        sys.exit(1)

    Path(manual_path).write_text(updated, encoding="utf-8")
    print(
        f"ユーザーマニュアルを更新しました "
        f"（入力 {response.usage.input_tokens:,} / 出力 {response.usage.output_tokens:,} tokens）。"
    )


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(f"使い方: {sys.argv[0]} <diff_path> <manual_path>")
        sys.exit(1)
    main(sys.argv[1], sys.argv[2])
