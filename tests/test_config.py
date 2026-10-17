from __future__ import annotations

from app.config import Settings


def test_anthropic_configuration_takes_precedence(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "anthropic-test-token")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://open.bigmodel.cn/api/anthropic/")
    monkeypatch.setenv("ANTHROPIC_MODEL", "glm-5.3")
    monkeypatch.setenv("AI_API_KEY", "old-test-key")
    monkeypatch.setenv("AI_BASE_URL", "https://old.example/v1")
    monkeypatch.setenv("AI_MODEL", "old-model")

    settings = Settings.from_env()

    assert settings.ai_protocol == "anthropic"
    assert settings.ai_api_key == "anthropic-test-token"
    assert settings.ai_base_url == "https://open.bigmodel.cn/api/anthropic"
    assert settings.ai_model == "glm-5.3"


def test_anthropic_token_uses_bigmodel_defaults(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "anthropic-test-token")
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)

    settings = Settings.from_env()

    assert settings.ai_protocol == "anthropic"
    assert settings.ai_base_url == "https://open.bigmodel.cn/api/anthropic"
    assert settings.ai_model == "glm-5.3"


def test_legacy_openai_configuration_remains_supported(monkeypatch) -> None:
    for name in ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "ANTHROPIC_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AI_API_KEY", "old-test-key")
    monkeypatch.setenv("AI_BASE_URL", "https://old.example/v1/")
    monkeypatch.setenv("AI_MODEL", "old-model")

    settings = Settings.from_env()

    assert settings.ai_protocol == "openai"
    assert settings.ai_api_key == "old-test-key"
    assert settings.ai_base_url == "https://old.example/v1"
    assert settings.ai_model == "old-model"


def test_extra_feed_and_article_domain_configuration(monkeypatch) -> None:
    monkeypatch.setenv(
        "EXTRA_RSS_FEEDS",
        "Authority one|https://one.example/rss\nIgnored|http://not-secure.example/rss\nAuthority two|https://two.example/feed",
    )
    monkeypatch.setenv("EXTRA_ARTICLE_DOMAINS", "one.example, https://two.example/ invalid_domain")

    settings = Settings.from_env()

    assert settings.extra_rss_feeds == (
        ("Authority one", "https://one.example/rss"),
        ("Authority two", "https://two.example/feed"),
    )
    assert settings.extra_article_domains == ("one.example", "two.example")
