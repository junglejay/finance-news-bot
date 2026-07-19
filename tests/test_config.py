from __future__ import annotations

from app.config import Settings


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
