from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from email.message import EmailMessage

import pytest
import respx
from httpx import Response

from app.models import ContentItem, ItemCategory
from app.rules import SEC_AAER_RSS
from app.sources import (
    PublicArticleReader,
    RSSSource,
    build_sources,
    parse_asic_records,
    parse_cninfo_records,
    parse_csrc_records,
    parse_dated_listing,
    parse_mof_listing,
    parse_newsletter_message,
    parse_pcaob_listing,
    parse_scholar_message,
    parse_thomson_reuters_topic,
)


SINCE = datetime(2026, 7, 17, tzinfo=timezone.utc)


def test_parse_csrc_records_keeps_public_body_and_normalizes_url() -> None:
    payload = {
        "data": {
            "results": [
                {
                    "title": "中国证券监督管理委员会行政处罚决定书",
                    "url": "//www.csrc.gov.cn/csrc/example/content.shtml",
                    "publishedTimeStr": "2026-07-18 08:00:00",
                    "memo": "〔2026〕18号；当事人：某会计师事务所。",
                    "content": (
                        "某上市公司年度报告存在虚假记载并虚增收入。"
                        "签字注册会计师未勤勉尽责，审计程序存在重大缺陷。" * 20
                    ),
                    "channelName": "行政处罚",
                    "manuscriptId": "123",
                }
            ]
        }
    }

    items = parse_csrc_records(payload, "中国证监会行政处罚", SINCE)

    assert len(items) == 1
    assert items[0].url == "https://www.csrc.gov.cn/csrc/example/content.shtml"
    assert "〔2026〕18号" in items[0].title
    assert len(items[0].article_text) >= 300
    assert items[0].metadata["article_read_status"] == "provided_by_source"


def test_parse_asic_records_keeps_reporting_release_metadata() -> None:
    payload = [
        {
            "name": "ASIC finds listed company breached financial reporting obligations",
            "url": "/about-asic/news-centre/find-a-media-release/2026-releases/example/",
            "publishedDate": "2026-07-18T03:00:00Z",
            "metaDescription": "The annual financial report contained a material misstatement.",
            "summary": "<p>ASIC obtained a Federal Court finding.</p>",
            "metaSubject": ["companies", "financial reporting"],
            "metaFunction": ["enforcement"],
            "documentNumber": "26-999MR",
        }
    ]

    items = parse_asic_records(payload, SINCE)

    assert len(items) == 1
    assert items[0].source == "ASIC Financial Reporting & Audit"
    assert items[0].url.startswith("https://www.asic.gov.au/")
    assert "material misstatement" in items[0].summary
    assert items[0].metadata["document_number"] == "26-999MR"


def test_parse_asic_records_prefers_actual_create_timestamp() -> None:
    payload = [
        {
            "name": "ASIC sanctions listed company auditor",
            "url": "/about-asic/news-centre/find-a-media-release/2026-releases/example/",
            "publishedDate": "2026-07-19T16:00:00Z",
            "createDate": "2026-07-19T04:30:00Z",
        }
    ]

    items = parse_asic_records(payload, SINCE)

    assert items[0].published_at.isoformat() == "2026-07-19T04:30:00+00:00"


def test_parse_dated_listing_supports_frc_day_month_year() -> None:
    html = """
    <li class="search__results-item">
      <a href="/news-and-events/news/2026/07/example/">Audit firm sanctioned</a>
      <p>An audit engagement failed to obtain sufficient evidence.</p>
      <p>18 July 2026</p>
    </li>
    """

    items = parse_dated_listing(
        html,
        "https://www.frc.org.uk/news-and-events/news/?news_type=9",
        "UK FRC Audit Enforcement",
        "/news-and-events/news/",
        SINCE,
        ItemCategory.PUBLIC_COMPANY_AUDIT,
    )

    assert len(items) == 1
    assert items[0].published_at.date().isoformat() == "2026-07-18"
    assert items[0].category == ItemCategory.PUBLIC_COMPANY_AUDIT


def test_date_only_listing_is_not_dropped_at_midday_boundary() -> None:
    html = """
    <article>
      <a href="/news-and-events/news/2026/07/example/">Annual enforcement review</a>
      <p>18 July 2026</p>
    </article>
    """

    items = parse_dated_listing(
        html,
        "https://www.frc.org.uk/news-and-events/news/",
        "UK FRC Audit & Reporting",
        "/news-and-events/news/",
        datetime(2026, 7, 18, 12, tzinfo=timezone.utc),
        date_timezone="Europe/London",
    )

    assert len(items) == 1
    assert items[0].metadata["date_precision"] == "day"


def test_parse_thomson_reuters_pcaob_topic_keeps_public_article_card() -> None:
    html = """
    <article class="card-post">
      <h3 class="card-post__title">
        <a href="/news/pcaob-sanctions-audit-firm/">PCAOB sanctions audit firm</a>
      </h3>
      <p>The order identifies failures across six issuer audits.</p>
      <span>July 18, 2026 · 5 minute read</span>
    </article>
    """

    items = parse_thomson_reuters_topic(
        html,
        "https://tax.thomsonreuters.com/news/topic/pcaob/",
        datetime(2026, 7, 18, 12, tzinfo=timezone.utc),
    )

    assert len(items) == 1
    assert items[0].source == "Thomson Reuters PCAOB"
    assert items[0].url.endswith("/news/pcaob-sanctions-audit-firm/")


def test_parse_cninfo_records_prioritizes_auditor_year_report_reply() -> None:
    published = datetime(2026, 7, 18, 13, tzinfo=timezone.utc)
    payload = {
        "announcements": [
            {
                "secCode": "600001",
                "secName": "示例公司",
                "announcementId": "a1",
                "announcementTitle": (
                    "会计师事务所关于2025年年度报告监管<em>问询</em>函的专项说明"
                ),
                "announcementTime": int(published.timestamp() * 1000),
                "adjunctUrl": "finalpage/2026-07-18/a1.PDF",
            },
            {
                "secCode": "600001",
                "secName": "示例公司",
                "announcementId": "a2",
                "announcementTitle": "关于2025年年度报告监管问询函的回复公告",
                "announcementTime": int(published.timestamp() * 1000),
                "adjunctUrl": "finalpage/2026-07-18/a2.PDF",
            },
        ]
    }

    items = parse_cninfo_records([payload], SINCE)

    assert len(items) == 1
    assert "监管问询函" in items[0].title
    assert items[0].category == ItemCategory.PUBLIC_COMPANY_AUDIT
    assert items[0].url == "https://static.cninfo.com.cn/finalpage/2026-07-18/a1.PDF"


def test_parse_dated_listing_uses_date_inside_afrc_anchor() -> None:
    html = """
    <div>
      <a class="press-releases-result__item"
         href="/en-hk/news-centre/press-releases/current">
        <p class="press-releases-result__item-title">Current audit inspection report</p>
        <p class="press-releases-result__item-date">18 July 2026</p>
      </a>
      <a class="press-releases-result__item"
         href="/en-hk/news-centre/press-releases/old">
        <p class="press-releases-result__item-title">Old appointment announcement</p>
        <p class="press-releases-result__item-date">10 June 2026</p>
      </a>
    </div>
    """

    items = parse_dated_listing(
        html,
        "https://www.afrc.org.hk/en-hk/news-centre/press-releases/",
        "AFRC Hong Kong",
        "/en-hk/news-centre/press-releases/",
        SINCE,
    )

    assert len(items) == 1
    assert items[0].title == "Current audit inspection report"
    assert items[0].published_at.date().isoformat() == "2026-07-18"


def test_parse_mof_listing_uses_web_publication_date_from_url() -> None:
    html = """
    <table>
      <tr>
        <td><a href="./202607/t20260718_123.htm"><script>
          var str = "财政部行政处罚决定书（财监法〔2026〕99号）";
        </script></a></td>
        <td>财监法〔2026〕99号</td>
        <td>2026年06月30日</td>
      </tr>
    </table>
    """

    items = parse_mof_listing(
        html,
        "https://www.mof.gov.cn/gp/xxgkml/jdjcj/index.htm",
        SINCE,
    )

    assert len(items) == 1
    assert items[0].published_at.date().isoformat() == "2026-07-18"
    assert "99号" in items[0].title
    assert items[0].metadata["document_date"] == "2026年06月30日"


def test_parse_pcaob_listing_extracts_recent_release() -> None:
    html = """
    <article><span>Jul. 18, 2026</span>
    <a href="/news-events/news-releases/news-release-detail/example">PCAOB Posts Audit Inspection Reports</a>
    </article>
    """
    items = parse_pcaob_listing(
        html,
        "https://pcaobus.org/news-events/news-releases",
        SINCE,
    )

    assert len(items) == 1
    assert items[0].source == "PCAOB"
    assert items[0].category == ItemCategory.OTHER


def test_parse_pcaob_recent_updates_card_uses_descriptive_title() -> None:
    html = """
    <div class="recent-updates__item">
      <div class="recent-updates__date"><span>Jul. 18, 2026</span></div>
      <h3 class="recent-updates__title">PCAOB Posts New Inspection Reports</h3>
      <a href="/news-events/news-releases/news-release-detail/example">Read more</a>
    </div>
    """

    items = parse_pcaob_listing(html, "https://pcaobus.org/news-events", SINCE)

    assert len(items) == 1
    assert items[0].title == "PCAOB Posts New Inspection Reports"


def test_parse_scholar_alert_email_keeps_paper_links_only() -> None:
    message = EmailMessage()
    message["Date"] = "Fri, 18 Jul 2026 08:00:00 +0000"
    message["Subject"] = "Google Scholar Alert - audit quality"
    message.set_content("HTML only")
    message.add_alternative(
        """
        <html><body>
          <a href="https://papers.example.test/audit">Audit quality and restatements</a>
          <a href="https://scholar.google.com/unsubscribe">Unsubscribe</a>
        </body></html>
        """,
        subtype="html",
    )

    items = parse_scholar_message(message, SINCE)

    assert len(items) == 1
    assert items[0].category == ItemCategory.RESEARCH


def test_parse_authorised_ft_newsletter_keeps_metadata_and_links() -> None:
    message = EmailMessage()
    message["Date"] = "Fri, 18 Jul 2026 08:00:00 +0000"
    message["Subject"] = "FT Audit and Accounting"
    message.set_content("HTML only")
    message.add_alternative(
        """
        <html><body>
          <a href="https://www.ft.com/content/example">Audit regulator opens investigation</a>
          <a href="https://www.ft.com/unsubscribe">Unsubscribe</a>
        </body></html>
        """,
        subtype="html",
    )

    items = parse_newsletter_message(message, SINCE, "Financial Times")

    assert len(items) == 1
    assert items[0].source == "Financial Times"


@pytest.mark.asyncio
@respx.mock
async def test_public_article_reader_extracts_allowed_regulator_article() -> None:
    item = ContentItem(
        source="UK FRC Audit Enforcement",
        title="Audit sanctions",
        url="https://www.frc.org.uk/news-and-events/news/example",
        published_at=datetime(2026, 7, 18, tzinfo=timezone.utc),
    )
    route = respx.get(item.url).mock(
        return_value=Response(
            200,
            headers={"content-type": "text/html"},
            text="<html><main><p>" + ("Public audit enforcement material. " * 20) + "</p></main></html>",
        )
    )

    await PublicArticleReader().enrich([item])

    assert route.called
    assert len(item.article_text) >= 300
    assert item.metadata["article_read_status"] == "read"


@pytest.mark.asyncio
@respx.mock
async def test_public_article_reader_does_not_refetch_source_provided_text() -> None:
    item = ContentItem(
        source="中国证监会行政处罚",
        title="处罚决定",
        url="https://www.csrc.gov.cn/csrc/example",
        article_text="公开处罚决定正文。" * 100,
        published_at=datetime(2026, 7, 18, tzinfo=timezone.utc),
    )

    await PublicArticleReader().enrich([item])

    assert item.metadata["article_read_status"] == "provided_by_source"


@pytest.mark.asyncio
@respx.mock
async def test_public_article_reader_never_fetches_financial_times() -> None:
    item = ContentItem(
        source="Financial Times",
        title="Audit market outlook",
        url="https://www.ft.com/content/example",
        published_at=datetime(2026, 7, 18, tzinfo=timezone.utc),
    )

    await PublicArticleReader().enrich([item])

    assert item.article_text == ""
    assert item.metadata["article_read_status"] == "skipped_source_or_domain"


@pytest.mark.asyncio
@respx.mock
async def test_sec_aaer_rss_assigns_dedicated_category() -> None:
    route = respx.get(SEC_AAER_RSS).mock(
        return_value=Response(
            200,
            headers={"content-type": "application/rss+xml"},
            text=(
                "<rss><channel><item><title>Example Corp and Jane Doe, CPA</title>"
                "<link>https://www.sec.gov/files/litigation/admin/example.pdf</link>"
                "<description>AAER-9999</description>"
                "<pubDate>Fri, 18 Jul 2026 08:00:00 +0000</pubDate>"
                "</item></channel></rss>"
            ),
        )
    )
    source = RSSSource(
        "SEC Accounting & Auditing Enforcement",
        SEC_AAER_RSS,
        ItemCategory.FRAUD_ENFORCEMENT,
    )

    items = await source.fetch(SINCE)

    assert route.called
    assert items[0].category == ItemCategory.FRAUD_ENFORCEMENT


def test_build_sources_is_focused_on_audit_and_regulatory_material(settings) -> None:
    settings = replace(settings, extra_rss_feeds=(("Custom authority", "https://example.test/rss"),))

    source_names = {source.name for source in build_sources(settings)}

    assert {
        "SEC Accounting & Auditing Enforcement",
        "SEC Press Releases",
        "PCAOB",
        "UK FRC Audit & Reporting",
        "IAASB",
        "ASIC Financial Reporting & Audit",
        "AFRC Hong Kong",
        "SEC Litigation Releases",
        "SEC Administrative Proceedings",
        "中国证监会行政处罚",
        "中国证监会要闻",
        "巨潮资讯年报问询与审计回复",
        "财政部行政处罚",
        "Thomson Reuters PCAOB",
        "Custom authority",
    } <= source_names
    assert "Guardian Business" not in source_names
    assert "WSJ US Business" not in source_names
    assert "Yahoo Finance" not in source_names
    assert "U.S. EIA Today in Energy" not in source_names
    assert "Federal Reserve Press Releases" not in source_names
    assert "CFTC Press Releases" not in source_names


def test_core_regulator_articles_are_allowed_by_default() -> None:
    reader = PublicArticleReader()
    published_at = datetime(2026, 7, 18, tzinfo=timezone.utc)

    for source, url in (
        ("中国证监会行政处罚", "https://www.csrc.gov.cn/csrc/example"),
        ("财政部行政处罚", "https://www.mof.gov.cn/example"),
        ("UK FRC Audit Enforcement", "https://www.frc.org.uk/example"),
        ("IAASB", "https://www.iaasb.org/example"),
        ("Thomson Reuters PCAOB", "https://tax.thomsonreuters.com/news/example"),
        ("巨潮资讯年报问询与审计回复", "https://static.cninfo.com.cn/example.pdf"),
    ):
        assert reader._is_allowed(
            ContentItem(
                source=source,
                title="Official release",
                url=url,
                published_at=published_at,
            )
        )
