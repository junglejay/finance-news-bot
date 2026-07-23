from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

import pytest
import respx
from httpx import Response

from app.models import ContentItem
from app.sources import (
    BOJ_WHATS_NEW_RSS,
    PublicArticleReader,
    RSSSource,
    build_sources,
    parse_newsletter_message,
    parse_pcaob_listing,
    parse_scholar_message,
    parse_sec_listing,
)


def test_parse_sec_listing_extracts_recent_public_notices() -> None:
    html = """
    <table><tr><th>Date</th><th>Headline</th></tr>
    <tr><td>Jul. 18, 2026</td><td><a href="/litigation/example">SEC Charges Example Corp</a></td></tr>
    </table>
    """
    since = datetime(2026, 7, 17, tzinfo=timezone.utc)

    items = parse_sec_listing(html, "https://www.sec.gov/news", "SEC Press Releases", since)

    assert len(items) == 1
    assert items[0].url == "https://www.sec.gov/litigation/example"
    assert items[0].title == "SEC Charges Example Corp"


def test_parse_pcaob_listing_extracts_recent_release() -> None:
    html = """
    <article><span>Jul. 18, 2026</span>
    <a href="/news-events/news-releases/news-release-detail/example">PCAOB Announces Inspection Results</a>
    </article>
    """
    items = parse_pcaob_listing(html, "https://pcaobus.org/news-events/news-releases", datetime(2026, 7, 17, tzinfo=timezone.utc))

    assert len(items) == 1
    assert items[0].source == "PCAOB"


def test_parse_scholar_alert_email_keeps_paper_links_only() -> None:
    message = EmailMessage()
    message["Date"] = "Fri, 18 Jul 2026 08:00:00 +0000"
    message["Subject"] = "Google Scholar Alert - commodity futures"
    message.set_content("HTML only")
    message.add_alternative(
        """
        <html><body>
          <a href="https://papers.example.test/oil">Oil futures and inventories</a>
          <a href="https://scholar.google.com/unsubscribe">Unsubscribe</a>
        </body></html>
        """,
        subtype="html",
    )

    items = parse_scholar_message(message, datetime(2026, 7, 17, tzinfo=timezone.utc))

    assert len(items) == 1
    assert items[0].title == "Oil futures and inventories"
    assert items[0].category.value == "research"


def test_parse_authorised_ft_newsletter_keeps_metadata_and_links() -> None:
    message = EmailMessage()
    message["Date"] = "Fri, 18 Jul 2026 08:00:00 +0000"
    message["Subject"] = "FT Energy Source"
    message.set_content("HTML only")
    message.add_alternative(
        """
        <html><body>
          <a href="https://www.ft.com/content/example">Oil market outlook</a>
          <a href="https://www.ft.com/unsubscribe">Unsubscribe</a>
        </body></html>
        """,
        subtype="html",
    )

    items = parse_newsletter_message(message, datetime(2026, 7, 17, tzinfo=timezone.utc), "Financial Times")

    assert len(items) == 1
    assert items[0].source == "Financial Times"
    assert items[0].title == "Oil market outlook"


@pytest.mark.asyncio
@respx.mock
async def test_public_article_reader_extracts_allowed_public_article() -> None:
    item = ContentItem(
        source="U.S. EIA Today in Energy",
        title="Oil inventory update",
        url="https://www.eia.gov/todayinenergy/detail.php?id=123",
        published_at=datetime(2026, 7, 18, tzinfo=timezone.utc),
    )
    route = respx.get(item.url).mock(
        return_value=Response(
            200,
            headers={"content-type": "text/html"},
            text="<html><main><p>" + ("Public EIA article material. " * 20) + "</p></main></html>",
        )
    )

    await PublicArticleReader().enrich([item])

    assert route.called
    assert len(item.article_text) >= 300
    assert item.metadata["article_read_status"] == "read"


@pytest.mark.asyncio
@respx.mock
async def test_public_article_reader_never_fetches_financial_times() -> None:
    item = ContentItem(
        source="Financial Times",
        title="Oil market outlook",
        url="https://www.ft.com/content/example",
        published_at=datetime(2026, 7, 18, tzinfo=timezone.utc),
    )

    await PublicArticleReader().enrich([item])

    assert item.article_text == ""


def test_build_sources_includes_authoritative_default_and_extra_feeds(settings) -> None:
    settings = replace(settings, extra_rss_feeds=(("Custom authority", "https://example.test/rss"),))

    source_names = {source.name for source in build_sources(settings)}

    assert {
        "U.S. EIA Today in Energy",
        "U.S. EIA Press Releases",
        "Federal Reserve Press Releases",
        "Bank of Japan",
        "Bank of Korea Press Releases",
        "BIS Press Releases",
        "BIS Statistical Releases",
        "ECB News",
        "ECB Statistical Releases",
        "CFTC Press Releases",
        "European Banking Authority",
        "Guardian Business",
        "WSJ US Business",
    } <= source_names
    assert "Custom authority" in source_names


def test_bis_and_ecb_public_articles_are_allowed_by_default() -> None:
    reader = PublicArticleReader()
    published_at = datetime(2026, 7, 18, tzinfo=timezone.utc)

    bis_item = ContentItem(
        source="BIS Press Releases",
        title="BIS release",
        url="https://www.bis.org/press/example.htm",
        published_at=published_at,
    )
    ecb_item = ContentItem(
        source="ECB News",
        title="ECB release",
        url="https://www.ecb.europa.eu/press/example/html/index.en.html",
        published_at=published_at,
    )

    assert reader._is_allowed(bis_item)
    assert reader._is_allowed(ecb_item)


@pytest.mark.asyncio
@respx.mock
async def test_bank_of_japan_rss_items_upgrade_to_https() -> None:
    source = RSSSource("Bank of Japan", BOJ_WHATS_NEW_RSS)
    route = respx.get(BOJ_WHATS_NEW_RSS).mock(
        return_value=Response(
            200,
            headers={"content-type": "application/rss+xml"},
            text=(
                "<rss><channel><item><title>Policy update</title>"
                "<link>http://www.boj.or.jp/en/mopo/example.htm</link>"
                "<pubDate>Fri, 18 Jul 2026 08:00:00 +0000</pubDate>"
                "</item></channel></rss>"
            ),
        )
    )

    items = await source.fetch(datetime(2026, 7, 17, tzinfo=timezone.utc))

    assert route.called
    assert items[0].url == "https://www.boj.or.jp/en/mopo/example.htm"
