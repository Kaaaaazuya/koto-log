"""T1.4: LLM クライアント抽象化のテスト。

litellm.completion をモックして、model 文字列に応じた呼び出しの組み立て
（local は api_base 付与 / hosted は api_key 付与・tools の受け渡し）を検証する。
実際の Ollama 疎通は test_llm_live.py（統合テスト）で確認する。
"""

from kotolog.config import Config
from kotolog.llm.client import LLMClient


def _cfg(model: str, api_key: str | None = None) -> Config:
    return Config(
        model=model,
        api_key=api_key,
        db_url=":memory:",
        default_child="baby",
        ollama_base="http://localhost:11434",
        line_channel_secret=None,
        line_channel_access_token=None,
        turso_auth_token=None,
        dashboard_token=None,
    )


def test_local_call_passes_api_base(monkeypatch):
    captured = {}

    def fake_completion(**kwargs):
        captured.update(kwargs)
        return "resp"

    monkeypatch.setattr("kotolog.llm.client.litellm.completion", fake_completion)

    client = LLMClient(_cfg("ollama_chat/qwen2.5:7b"))
    out = client.complete([{"role": "user", "content": "hi"}])

    assert out == "resp"
    assert captured["model"] == "ollama_chat/qwen2.5:7b"
    assert captured["api_base"] == "http://localhost:11434"
    assert "api_key" not in captured  # local では不要


def test_hosted_call_passes_api_key_not_api_base(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "kotolog.llm.client.litellm.completion",
        lambda **kw: captured.update(kw),
    )

    client = LLMClient(_cfg("claude-3-5-haiku-latest", api_key="sk-test"))
    client.complete([{"role": "user", "content": "hi"}])

    assert captured["model"] == "claude-3-5-haiku-latest"
    assert captured["api_key"] == "sk-test"
    assert "api_base" not in captured  # hosted では付けない


def test_tools_are_forwarded(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "kotolog.llm.client.litellm.completion",
        lambda **kw: captured.update(kw),
    )

    tools = [{"type": "function", "function": {"name": "save_record"}}]
    client = LLMClient(_cfg("ollama_chat/qwen2.5:7b"))
    client.complete([{"role": "user", "content": "hi"}], tools=tools)

    assert captured["tools"] == tools


def test_no_tools_means_no_tools_kwarg(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "kotolog.llm.client.litellm.completion",
        lambda **kw: captured.update(kw),
    )
    LLMClient(_cfg("ollama_chat/qwen2.5:7b")).complete([{"role": "user", "content": "hi"}])
    assert "tools" not in captured


# --- 使用量計測（ADR-0002 / P7） ---------------------------------------------


def _usage_resp(prompt=120, completion=20):
    from types import SimpleNamespace

    usage = SimpleNamespace(
        prompt_tokens=prompt, completion_tokens=completion, total_tokens=prompt + completion
    )
    msg = SimpleNamespace(content="ok", tool_calls=None)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=msg)], model="claude-3-5-haiku-latest", usage=usage
    )


class _RecordingSink:
    def __init__(self):
        self.events = []

    def record(self, event):
        self.events.append(event)


def test_complete_records_usage_event_to_sink(monkeypatch):
    """complete() の戻り値から使用量を捕捉し Sink へ通知する。"""
    monkeypatch.setattr("kotolog.llm.client.litellm.completion", lambda **kw: _usage_resp())

    sink = _RecordingSink()
    client = LLMClient(_cfg("claude-3-5-haiku-latest", api_key="sk-test"), sink=sink)
    client.complete([{"role": "user", "content": "hi"}], operation="extract")

    assert len(sink.events) == 1
    ev = sink.events[0]
    assert ev.operation == "extract"
    assert ev.input_tokens == 120
    assert ev.output_tokens == 20
    assert ev.total_tokens == 140
    assert ev.model == "claude-3-5-haiku-latest"


def test_complete_default_sink_does_not_raise(monkeypatch):
    """Sink 未注入（NullSink）でも complete() は応答をそのまま返す。"""
    resp = _usage_resp()
    monkeypatch.setattr("kotolog.llm.client.litellm.completion", lambda **kw: resp)

    out = LLMClient(_cfg("ollama_chat/qwen2.5:7b")).complete([{"role": "user", "content": "hi"}])
    assert out is resp


def test_null_sink_skips_event_build(monkeypatch):
    """計測オフ（NullSink）では build_event を構築せず completion_cost も呼ばない。"""
    monkeypatch.setattr("kotolog.llm.client.litellm.completion", lambda **kw: _usage_resp())
    calls = {"cost": 0}
    monkeypatch.setattr(
        "kotolog.obs.usage.litellm.completion_cost",
        lambda **kw: calls.__setitem__("cost", calls["cost"] + 1),
    )

    LLMClient(_cfg("ollama_chat/qwen2.5:7b")).complete([{"role": "user", "content": "hi"}])
    assert calls["cost"] == 0  # NullSink ではコスト計算（＝イベント構築）が走らない


def test_complete_default_operation_is_loop(monkeypatch):
    monkeypatch.setattr("kotolog.llm.client.litellm.completion", lambda **kw: _usage_resp())

    sink = _RecordingSink()
    LLMClient(_cfg("ollama_chat/qwen2.5:7b"), sink=sink).complete([{"role": "user", "content": "hi"}])
    assert sink.events[0].operation == "loop"
