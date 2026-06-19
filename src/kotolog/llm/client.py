"""LLM クライアント抽象化（T1.4）。

LiteLLM 経由で local(Ollama) ⇄ Claude を model 文字列のみで切替える（NFR-3）。
ツール定義と実行コードはモデル非依存。このラッパだけがプロバイダ差を吸収する。
"""

from __future__ import annotations

import litellm

from kotolog.config import Config


class LLMClient:
    def __init__(self, config: Config) -> None:
        self.model = config.model
        self.api_key = config.api_key
        self.ollama_base = config.ollama_base

    def complete(self, messages: list[dict], tools: list[dict] | None = None):
        """1 回の補完を実行する。tools を渡せば tool-use を有効化する。"""
        kwargs: dict = {"model": self.model, "messages": messages}
        if tools:
            kwargs["tools"] = tools
        if self._is_local():
            # ローカル(Ollama)は api_base が必要。api_key は不要。
            kwargs["api_base"] = self.ollama_base
        elif self.api_key:
            kwargs["api_key"] = self.api_key
        return litellm.completion(**kwargs)

    def _is_local(self) -> bool:
        return self.model.startswith("ollama")
