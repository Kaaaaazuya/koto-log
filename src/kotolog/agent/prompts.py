"""プロンプトのファイル分離・バージョン管理（Issue #66）。

`src/kotolog/prompts/<name>/<version>.txt` からシステムプロンプトを読み込む。
本番コード（extractor.py / loop.py）と evals ランナーが同じファイルを参照するため、
プロンプトを変更すれば両方に同時に反映される。
"""

from __future__ import annotations

from importlib import resources

DEFAULT_VERSION = "v1"


def load_prompt(name: str, version: str = DEFAULT_VERSION) -> str:
    """`prompts/<name>/<version>.txt` の内容を読み込んで返す（前後の空白は除去）。"""
    text = resources.files("kotolog").joinpath("prompts", name, f"{version}.txt").read_text(encoding="utf-8")
    return text.strip()
