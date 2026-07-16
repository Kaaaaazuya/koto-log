"""T0.2: .env から設定を読み込む config 層のテスト。"""

from kotolog.config import Config, load_config


def test_load_config_reads_env(monkeypatch):
    monkeypatch.setenv("KOTOLOG_MODEL", "ollama/qwen2.5")
    monkeypatch.setenv("KOTOLOG_API_KEY", "sk-test")
    monkeypatch.setenv("KOTOLOG_DB_URL", "test.db")
    monkeypatch.setenv("KOTOLOG_DEFAULT_CHILD", "baby")

    cfg = load_config()

    assert isinstance(cfg, Config)
    assert cfg.model == "ollama/qwen2.5"
    assert cfg.api_key == "sk-test"
    assert cfg.db_url == "test.db"
    assert cfg.default_child == "baby"


def test_load_config_defaults(monkeypatch):
    for key in ("KOTOLOG_MODEL", "KOTOLOG_API_KEY", "KOTOLOG_DB_URL", "KOTOLOG_DEFAULT_CHILD"):
        monkeypatch.delenv(key, raising=False)

    cfg = load_config()

    # model / db_url / default_child は妥当なデフォルトを持つ。api_key は任意（None可）。
    assert cfg.model
    assert cfg.db_url
    assert cfg.default_child
    assert cfg.api_key is None


def test_usage_log_defaults_off(monkeypatch):
    monkeypatch.delenv("KOTOLOG_USAGE_LOG", raising=False)
    assert load_config().usage_log is False


def test_usage_log_enabled_by_env(monkeypatch):
    monkeypatch.setenv("KOTOLOG_USAGE_LOG", "1")
    assert load_config().usage_log is True


def test_usage_db_defaults_off(monkeypatch):
    monkeypatch.delenv("KOTOLOG_USAGE_DB", raising=False)
    assert load_config().usage_db is False


def test_usage_db_enabled_by_env(monkeypatch):
    monkeypatch.setenv("KOTOLOG_USAGE_DB", "1")
    assert load_config().usage_db is True
