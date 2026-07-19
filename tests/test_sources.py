from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

from app.sources import parse_newsletter_message, parse_pcaob_listing, parse_scholar_message, parse_sec_listing


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
