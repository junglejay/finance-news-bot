"""Shared geography and lookback rules for collection, selection, and previews."""

from __future__ import annotations

from urllib.parse import urlsplit

from .models import ContentItem
from .rules import (
    DEFAULT_WINDOW_HOURS,
    FOREIGN_ARTICLE_DOMAINS,
    FOREIGN_SOURCE_PREFIXES,
    FOREIGN_WINDOW_HOURS,
    MAINLAND_CHINA_SOURCES,
    WEEKEND_WINDOW_HOURS,
)


def source_region(source: str) -> str:
    name = source.strip().casefold()
    if source in MAINLAND_CHINA_SOURCES:
        return "mainland"
    if any(name == prefix or name.startswith(prefix + " ") for prefix in ("hkex", "afrc")):
        return "hong_kong"
    if any(
        name == prefix.casefold() or name.startswith(prefix.casefold() + " ")
        for prefix in FOREIGN_SOURCE_PREFIXES
    ):
        return "foreign"
    return "other"


def article_region(item: ContentItem) -> str:
    region = source_region(item.source)
    if region != "other":
        return region
    host = (urlsplit(item.url).hostname or "").casefold()
    if any(host == domain or host.endswith("." + domain) for domain in FOREIGN_ARTICLE_DOMAINS):
        return "foreign"
    return "other"


def is_foreign_article(item: ContentItem) -> bool:
    return article_region(item) == "foreign"


def source_window_hours(source: str, weekday: int) -> int:
    if source_region(source) == "foreign":
        return FOREIGN_WINDOW_HOURS
    return WEEKEND_WINDOW_HOURS if weekday == 0 else DEFAULT_WINDOW_HOURS
