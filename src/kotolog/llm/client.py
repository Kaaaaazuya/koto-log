"""LLM クライアント抽象化（T1.4）。

LiteLLM 経由で local(Ollama) ⇄ Claude を model 文字列のみで切替える（NFR-3）。
ツール定義と実行コードはモデル非依存。このラッパだけがプロバイダ差を吸収する。
"""

from __future__ import annotations

import litellm

from kotolog.config import Config
from kotolog.obs.usage import NullSink, UsageSink, build_event, current_trace_id


class LLMClient:
    def __init__(self, config: Config, sink: UsageSink | None = None) -> None:
        self.model = config.model
        self.api_key = config.api_key
        self.ollama_base = config.ollama_base
        # 全 LLM 呼び出しの唯一の通り道なので、ここで使用量を計測する（ADR-0002）。
        self._sink: UsageSink = sink or NullSink()
        # 計測オフ（NullSink）時はイベント構築自体を省く。completion_cost 等の
        # 無駄な処理を全呼び出しのホットパスから外す。
        self._measuring: bool = not isinstance(self._sink, NullSink)

    def complete(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        tool_choice: dict | None = None,
        *,
        operation: str = "loop",
    ):
        """1 回の補完を実行する。tools を渡せば tool-use を有効化する。"""
        kwargs: dict = {"model": self.model, "messages": messages}
        if tools:
            kwargs["tools"] = tools
        if tool_choice is not None:
            kwargs["tool_choice"] = tool_choice
        if self._is_local():
            # ローカル(Ollama)は api_base が必要。api_key は不要。
            kwargs["api_base"] = self.ollama_base
        elif self.api_key:
            kwargs["api_key"] = self.api_key
        resp = litellm.completion(**kwargs)
        self._record_usage(resp, operation)
        return resp

    def _record_usage(self, resp, operation: str) -> None:
        if not self._measuring:
            return
        # 計測の失敗で本処理を止めない。
        try:
            self._sink.record(build_event(resp, operation=operation, trace_id=current_trace_id()))
        except Exception:  # noqa: BLE001 - 計測は best-effort
            pass

    def _is_local(self) -> bool:
        return self.model.startswith("ollama")
