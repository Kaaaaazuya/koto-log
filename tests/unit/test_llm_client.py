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
