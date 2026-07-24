"""Permitted metadata and excerpt collectors for feeds, public notices, and Scholar Alerts."""

from __future__ import annotations

import asyncio
import email
import imaplib
import io
import logging
import re
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime
from typing import Iterable
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

import feedparser
import httpx
from bs4 import BeautifulSoup
from dateutil import parser as date_parser, tz

from .config import Settings
from .models import ContentItem, ItemCategory
from .rules import (
    AFRC_PRESS_RELEASES_URL,
    ARTICLE_READER_CONCURRENCY,
    ASIC_MEDIA_RELEASES_API,
    CNINFO_ANNOUNCEMENTS_API,
    CNINFO_PDF_BASE_URL,
    CSRC_NEWS_API,
    CSRC_PENALTIES_API,
    FRC_AUDIT_ENFORCEMENT_URL,
    FULL_TEXT_BLOCKED_SOURCES,
    IAASB_NEWS_URL,
    MAX_ARTICLE_CHARS,
    MIN_ARTICLE_CHARS,
    MOF_SANCTIONS_URL,
    PCAOB_NEWS_URL,
    PUBLIC_ARTICLE_DOMAINS,
    SEC_AAER_RSS,
    SEC_ADMIN_PROCEEDINGS_RSS,
    SEC_LITIGATION_RSS,
    SEC_PRESS_RSS,
    THOMSON_REUTERS_PCAOB_URL,
)


logger = logging.getLogger(__name__)


class SourceFetchError(RuntimeError):
    pass


class Source(ABC):
    name: str

    @abstractmethod
    async def fetch(self, since: datetime) -> list[ContentItem]:
        raise NotImplementedError


def _to_utc(value: str | None, fallback: datetime) -> datetime:
    if not value:
        return fallback
    try:
        parsed = date_parser.parse(
            value,
            tzinfos={
                "EST": tz.gettz("America/New_York"),
                "EDT": tz.gettz("America/New_York"),
            },
        )
    except (TypeError, ValueError, OverflowError):
        return fallback
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _clean_text(value: str) -> str:
    return " ".join(BeautifulSoup(value or "", "html.parser").get_text(" ").split())


class RSSSource(Source):
    def __init__(self, name: str, feed_url: str, category: ItemCategory = ItemCategory.OTHER) -> None:
        self.name = name
        self.feed_url = feed_url
        self.category = category

    async def fetch(self, since: datetime) -> list[ContentItem]:
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
                response = await client.get(
                    self.feed_url,
                    headers={"User-Agent": "audit-regulatory-intel-bot/2.0"},
                )
                response.raise_for_status()
            parsed = feedparser.parse(response.content)
        except (httpx.HTTPError, ValueError) as exc:
            raise SourceFetchError(f"RSS fetch failed: {exc}") from exc

        now = datetime.now(timezone.utc)
        items: list[ContentItem] = []
        for entry in parsed.entries:
            title = _clean_text(entry.get("title", ""))
            url = entry.get("link", "").strip()
            if not title or not url:
                continue
            published = _to_utc(entry.get("published") or entry.get("updated"), now)
            if published < since:
                continue
            items.append(
                ContentItem(
                    source=self.name,
                    category=self.category,
                    title=title,
                    url=url,
                    summary=_clean_text(entry.get("summary") or entry.get("description", "")),
                    published_at=published,
                    metadata={"feed_url": self.feed_url},
                )
            )
        return items


class PublicArticleReader:
    """Read public article bodies for the selected sources without persisting them.

    The reader deliberately uses a small allow-list of first-party domains. It is
    not a generic scraper and it does not attempt logins, cookie walls, archive
    copies, or subscription workarounds.
    """

    def __init__(
        self,
        extra_allowed_domains: Iterable[str] = (),
        max_characters: int = MAX_ARTICLE_CHARS,
        concurrency: int = ARTICLE_READER_CONCURRENCY,
    ) -> None:
        self.allowed_domains = PUBLIC_ARTICLE_DOMAINS | {domain.lower() for domain in extra_allowed_domains}
        self.max_characters = max_characters
        self.concurrency = concurrency

    async def enrich(self, items: list[ContentItem]) -> list[ContentItem]:
        semaphore = asyncio.Semaphore(self.concurrency)
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            await asyncio.gather(*(self._enrich_one(client, semaphore, item) for item in items))
        return items

    async def _enrich_one(
        self, client: httpx.AsyncClient, semaphore: asyncio.Semaphore, item: ContentItem
    ) -> None:
        if item.article_text:
            item.metadata.setdefault("article_read_status", "provided_by_source")
            return
        if not self._is_allowed(item):
            item.metadata.setdefault("article_read_status", "skipped_source_or_domain")
            logger.debug(f"Skipping {item.source} '{item.title[:50]}' - source/domain not allowed")
            return
        try:
            async with semaphore:
                # Public pages (notably the Guardian) occasionally drop the TLS
                # tunnel mid-handshake, so retry transient connection failures
                # before giving up on a readable article.
                last_http_error: httpx.HTTPError | None = None
                for attempt in range(1, 4):
                    try:
                        response = await client.get(
                            item.url,
                            headers={
                                "User-Agent": "audit-regulatory-intel-bot/2.0",
                                "Accept": "text/html,application/xhtml+xml,application/pdf",
                            },
                        )
                        response.raise_for_status()
                        break
                    except httpx.HTTPError as exc:
                        last_http_error = exc
                        if attempt < 3:
                            await asyncio.sleep(0.5 * attempt)
                else:
                    assert last_http_error is not None
                    raise last_http_error
            content_type = response.headers.get("content-type", "").lower()
            if "pdf" in content_type or item.url.lower().endswith(".pdf"):
                article_text = _extract_pdf_text(response.content, self.max_characters)
            elif "html" in content_type:
                article_text = _extract_public_article_text(response.text, self.max_characters)
            else:
                item.metadata["article_read_status"] = "skipped_non_html"
                logger.debug(f"Skipping {item.source} - non-HTML content type: {content_type}")
                return
            if len(article_text) < MIN_ARTICLE_CHARS:
                item.metadata["article_read_status"] = f"insufficient_text_{len(article_text)}_chars"
                logger.debug(f"Skipping {item.source} '{item.title[:50]}' - insufficient text: {len(article_text)} chars")
                return
            item.article_text = article_text
            item.metadata["article_read_status"] = "read"
            logger.debug(f"Successfully read {len(article_text)} chars from {item.source}")
        except httpx.HTTPError as exc:
            item.metadata["article_read_status"] = f"unavailable:{type(exc).__name__}"
            logger.debug(f"HTTP error reading {item.source} '{item.title[:50]}': {exc}")

    def _is_allowed(self, item: ContentItem) -> bool:
        if item.source in FULL_TEXT_BLOCKED_SOURCES:
            return False
        split = urlsplit(item.url)
        host = (split.hostname or "").lower()
        return split.scheme == "https" and any(
            host == domain or host.endswith(f".{domain}") for domain in self.allowed_domains
        )


def _extract_public_article_text(html: str, max_characters: int) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for element in soup.select("script, style, noscript, nav, header, footer, aside, form, svg"):
        element.decompose()
    # Prefer known editorial-body containers over the whole <main>. Regulatory
    # sites frequently append "More news" cards whose audit-related words would
    # otherwise contaminate classification of an unrelated announcement.
    root = (
        soup.select_one(".detail-page .ck-content")
        or soup.select_one(".ck-content")
        or soup.find("article")
        or soup.find("main")
        or soup.select_one('[role="main"]')
        or soup.body
    )
    if root is None:
        return ""
    blocks: list[str] = []
    for element in root.select("p, h2, h3, li"):
        text = _clean_text(element.get_text(" "))
        if len(text) < 30 or (blocks and text == blocks[-1]):
            continue
        blocks.append(text)
    joined = "\n".join(blocks)
    if len(joined) < MIN_ARTICLE_CHARS:
        # Some government pages use div/span plus <br> instead of paragraphs.
        # Falling back to the main container keeps those public decisions usable.
        fallback = _clean_text(root.get_text(" "))
        if len(fallback) > len(joined):
            joined = fallback
    return joined[:max_characters]


def _extract_pdf_text(content: bytes, max_characters: int) -> str:
    """Extract plain text from a public PDF such as an SEC litigation order.

    pypdf is imported lazily so the HTML path keeps working even if the package
    is absent. Scanned/image-only PDFs yield little or no text and are naturally
    filtered out by the caller's minimum-length check.
    """
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(content))
        blocks = [(page.extract_text() or "") for page in reader.pages]
    except Exception as exc:  # encrypted/malformed PDFs must not break the run
        logger.debug(f"PDF text extraction failed: {exc}")
        return ""
    return " ".join("\n".join(blocks).split())[:max_characters]


def parse_csrc_records(
    payload: dict, source: str, since: datetime
) -> list[ContentItem]:
    """Convert CSRC's public search JSON into typed, full-text records."""
    results = payload.get("data", {}).get("results", [])
    if not isinstance(results, list):
        return []

    now = datetime.now(timezone.utc)
    items: list[ContentItem] = []
    for record in results:
        if not isinstance(record, dict):
            continue
        title = _clean_text(str(record.get("title", "")))
        raw_url = str(record.get("url", "")).strip()
        published = _to_utc(str(record.get("publishedTimeStr", "")), now)
        if not title or not raw_url or published < since:
            continue
        url = f"https:{raw_url}" if raw_url.startswith("//") else urljoin(CSRC_NEWS_API, raw_url)
        summary = _clean_text(str(record.get("memo") or record.get("content") or ""))
        article_text = _clean_text(str(record.get("content") or ""))
        if title in {"中国证券监督管理委员会行政处罚决定书", "行政处罚决定书"} and summary:
            title = f"{title}｜{summary[:160]}"
        items.append(
            ContentItem(
                source=source,
                title=title[:500],
                url=url,
                summary=(article_text or summary)[:8_000],
                article_text=article_text[:MAX_ARTICLE_CHARS],
                published_at=published,
                metadata={
                    "channel": str(record.get("channelName", "")),
                    "manuscript_id": str(record.get("manuscriptId", "")),
                    "article_read_status": (
                        "provided_by_source" if len(article_text) >= MIN_ARTICLE_CHARS else "insufficient_source_text"
                    ),
                },
            )
        )
    return _unique_by_url(items)


def parse_asic_records(payload: object, since: datetime) -> list[ContentItem]:
    """Convert ASIC's public newsroom JSON to media-release records."""
    if not isinstance(payload, list):
        return []

    now = datetime.now(timezone.utc)
    items: list[ContentItem] = []
    for record in payload:
        if not isinstance(record, dict):
            continue
        title = _clean_text(str(record.get("name", "")))
        raw_url = str(record.get("url", "")).strip()
        published = _to_utc(
            # ASIC's publishedDate is a local newsroom date that is sometimes
            # emitted with a misleading trailing Z. createDate is the actual
            # machine timestamp and avoids future-dated candidates.
            str(record.get("createDate") or record.get("publishedDate") or ""),
            now,
        )
        if not title or not raw_url or published < since:
            continue
        summary = _clean_text(
            " ".join(
                (
                    str(record.get("metaDescription", "")),
                    str(record.get("summary", "")),
                    " ".join(str(value) for value in record.get("metaSubject", [])),
                    " ".join(str(value) for value in record.get("metaFunction", [])),
                )
            )
        )
        items.append(
            ContentItem(
                source="ASIC Financial Reporting & Audit",
                title=title,
                url=urljoin("https://www.asic.gov.au/newsroom/media-releases/", raw_url),
                summary=summary[:4_000],
                published_at=published,
                metadata={
                    "document_number": str(record.get("documentNumber", "")),
                    "api_url": ASIC_MEDIA_RELEASES_API,
                },
            )
        )
    return _unique_by_url(items)


class ASICJSONSource(Source):
    name = "ASIC Financial Reporting & Audit"

    async def fetch(self, since: datetime) -> list[ContentItem]:
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
                response = await client.get(
                    ASIC_MEDIA_RELEASES_API,
                    headers={"User-Agent": "audit-regulatory-intel-bot/2.0"},
                )
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise SourceFetchError(f"ASIC JSON fetch failed: {exc}") from exc
        return parse_asic_records(payload, since)


class CSRCJSONSource(Source):
    def __init__(self, name: str, api_url: str) -> None:
        self.name = name
        self.api_url = api_url

    async def fetch(self, since: datetime) -> list[ContentItem]:
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
                response = await client.get(
                    self.api_url,
                    headers={"User-Agent": "audit-regulatory-intel-bot/2.0"},
                )
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise SourceFetchError(f"CSRC JSON fetch failed: {exc}") from exc
        return parse_csrc_records(payload, self.name, since)


_MONTH_PATTERN = (
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|"
    r"Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Sept(?:ember)?|"
    r"Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
)


def _source_day_timestamp(
    year: int,
    month: int,
    day: int,
    timezone_name: str,
    now: datetime,
) -> datetime:
    """Represent a source-local publication date without shifting its day.

    Noon UTC keeps the stated source date stable in report validation. For the
    current source-local day, cap the anchor at crawl time so scoring never sees
    a future item. Date-only values remain an approximation and are labelled as
    such by the listing parsers.
    """
    anchor = datetime(year, month, day, 12, tzinfo=timezone.utc)
    source_today = now.astimezone(ZoneInfo(timezone_name)).date()
    if source_today == anchor.date():
        return min(anchor, now)
    return anchor


def _date_from_context(
    context: str,
    now: datetime,
    timezone_name: str = "UTC",
) -> datetime | None:
    patterns = (
        rf"{_MONTH_PATTERN}\.?\s+\d{{1,2}},\s+\d{{4}}",
        rf"\d{{1,2}}\s+{_MONTH_PATTERN}\s+\d{{4}}",
    )
    for pattern in patterns:
        match = re.search(pattern, context, flags=re.IGNORECASE)
        if match:
            try:
                parsed = date_parser.parse(match.group(0))
            except (TypeError, ValueError, OverflowError):
                return None
            return _source_day_timestamp(
                parsed.year,
                parsed.month,
                parsed.day,
                timezone_name,
                now,
            )
    return None


def parse_dated_listing(
    html: str,
    page_url: str,
    source: str,
    link_pattern: str,
    since: datetime,
    category: ItemCategory = ItemCategory.OTHER,
    date_timezone: str = "UTC",
) -> list[ContentItem]:
    """Parse cards/list items whose headline and publication date share a container."""
    soup = BeautifulSoup(html, "html.parser")
    now = datetime.now(timezone.utc)
    items: list[ContentItem] = []
    seen: set[str] = set()
    for anchor in soup.select("a[href]"):
        href = anchor.get("href", "")
        if link_pattern not in href:
            continue
        anchor_context = _clean_text(anchor.get_text(" "))
        title_element = anchor.select_one(
            "h2, h3, h4, h5, [class*='item-title'], [class*='card-title']"
        )
        title = _clean_text(
            title_element.get_text(" ") if title_element else anchor_context
        )
        url = urljoin(page_url, href)
        if not title or url in seen:
            continue

        context = anchor_context
        published = _date_from_context(anchor_context, now, date_timezone)
        if published is None:
            for container in anchor.parents:
                if getattr(container, "name", None) not in {"div", "li", "article"}:
                    continue
                candidate_context = _clean_text(container.get_text(" "))
                candidate_date = _date_from_context(
                    candidate_context,
                    now,
                    date_timezone,
                )
                if candidate_date is not None:
                    context = candidate_context
                    published = candidate_date
                    break
        if published is None or published < since:
            continue
        seen.add(url)
        items.append(
            ContentItem(
                source=source,
                category=category,
                title=title,
                url=url,
                summary=context[:2_000],
                published_at=published,
                metadata={"listing_url": page_url, "date_precision": "day"},
            )
        )
    return items


class DatedListingSource(Source):
    def __init__(
        self,
        name: str,
        url: str,
        link_pattern: str,
        category: ItemCategory = ItemCategory.OTHER,
        date_timezone: str = "UTC",
    ) -> None:
        self.name = name
        self.url = url
        self.link_pattern = link_pattern
        self.category = category
        self.date_timezone = date_timezone

    async def fetch(self, since: datetime) -> list[ContentItem]:
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
                response = await client.get(
                    self.url,
                    headers={"User-Agent": "audit-regulatory-intel-bot/2.0"},
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise SourceFetchError(f"dated listing fetch failed: {exc}") from exc
        return parse_dated_listing(
            response.text,
            self.url,
            self.name,
            self.link_pattern,
            since,
            self.category,
            self.date_timezone,
        )


def parse_thomson_reuters_topic(
    html: str,
    page_url: str,
    since: datetime,
) -> list[ContentItem]:
    """Parse the public PCAOB topic cards without picking up site navigation."""
    soup = BeautifulSoup(html, "html.parser")
    now = datetime.now(timezone.utc)
    items: list[ContentItem] = []
    for card in soup.select(".card-post"):
        anchor = card.select_one('.card-post__title a[href*="/news/"]')
        if anchor is None:
            continue
        title = _clean_text(anchor.get_text(" "))
        context = _clean_text(card.get_text(" "))
        published = _date_from_context(
            context,
            now,
            "America/New_York",
        )
        if not title or published is None or published < since:
            continue
        items.append(
            ContentItem(
                source="Thomson Reuters PCAOB",
                title=title,
                url=urljoin(page_url, anchor["href"]),
                summary=context[:2_000],
                published_at=published,
                metadata={"listing_url": page_url, "date_precision": "day"},
            )
        )
    return _unique_by_url(items)


class ThomsonReutersPCAOBSource(Source):
    name = "Thomson Reuters PCAOB"

    def __init__(self, url: str = THOMSON_REUTERS_PCAOB_URL) -> None:
        self.url = url

    async def fetch(self, since: datetime) -> list[ContentItem]:
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
                response = await client.get(
                    self.url,
                    headers={"User-Agent": "audit-regulatory-intel-bot/2.0"},
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise SourceFetchError(f"Thomson Reuters PCAOB fetch failed: {exc}") from exc
        return parse_thomson_reuters_topic(response.text, self.url, since)


_CNINFO_QUERY_TERMS = (
    "年报问询函",
    "会计差错",
    "非标准审计意见",
    "警示函",
    "立案",
    "行政处罚",
    "更正",
)
_CNINFO_NEGATIVE_TITLE_TERMS = ("不存在", "最近五年")
_CNINFO_AUDITOR_TITLE_TERMS = ("会计师事务所", "注册会计师", "审计机构")


def _is_focused_cninfo_title(title: str) -> bool:
    if any(term in title for term in _CNINFO_NEGATIVE_TITLE_TERMS):
        return False
    annual_report = "年度报告" in title or "年报" in title
    inquiry_reply = "问询函" in title and ("回复" in title or "专项说明" in title)
    reporting_correction = annual_report and (
        "更正" in title or "会计差错" in title
    )
    audit_exception = any(
        term in title
        for term in (
            "非标准审计意见",
            "非标审计意见",
            "保留意见",
            "否定意见",
            "无法表示意见",
        )
    )
    enforcement = any(
        term in title
        for term in (
            "立案调查",
            "行政处罚决定",
            "警示函",
            "纪律处分",
            "公开谴责",
        )
    )
    return (annual_report and inquiry_reply) or reporting_correction or audit_exception or enforcement


def parse_cninfo_records(payloads: Iterable[dict], since: datetime) -> list[ContentItem]:
    """Convert targeted public-company filings from CNInfo into candidates."""
    items: list[ContentItem] = []
    for payload in payloads:
        records = payload.get("announcements") or []
        if not isinstance(records, list):
            continue
        for record in records:
            if not isinstance(record, dict):
                continue
            raw_title = str(record.get("announcementTitle", ""))
            # Search highlights wrap matched words in <em>. Joining with an
            # empty separator preserves phrases such as "监管问询函".
            title = " ".join(
                BeautifulSoup(raw_title, "html.parser")
                .get_text("", strip=True)
                .split()
            )
            raw_url = str(record.get("adjunctUrl", "")).strip()
            try:
                timestamp_ms = int(record.get("announcementTime"))
                source_timestamp = datetime.fromtimestamp(
                    timestamp_ms / 1000,
                    tz=timezone.utc,
                )
            except (TypeError, ValueError, OSError, OverflowError):
                continue
            if (
                not title
                or not raw_url
                or source_timestamp < since
                or not _is_focused_cninfo_title(title)
            ):
                continue
            source_day = source_timestamp.astimezone(
                ZoneInfo("Asia/Shanghai")
            ).date()
            published = _source_day_timestamp(
                source_day.year,
                source_day.month,
                source_day.day,
                "Asia/Shanghai",
                datetime.now(timezone.utc),
            )
            security_name = _clean_text(str(record.get("secName", "")))
            security_code = _clean_text(str(record.get("secCode", "")))
            category = (
                ItemCategory.PUBLIC_COMPANY_AUDIT
                if any(term in title for term in _CNINFO_AUDITOR_TITLE_TERMS)
                else ItemCategory.REPORTING_CONTROLS
            )
            items.append(
                ContentItem(
                    source="巨潮资讯年报问询与审计回复",
                    category=category,
                    title=title,
                    url=urljoin(CNINFO_PDF_BASE_URL, raw_url),
                    summary=(
                        f"上市公司公开披露材料：{security_name}"
                        f"（{security_code}）—{title}"
                    ),
                    published_at=published,
                    metadata={
                        "api_url": CNINFO_ANNOUNCEMENTS_API,
                        "security_name": security_name,
                        "security_code": security_code,
                        "announcement_id": str(record.get("announcementId", "")),
                        "source_timestamp": source_timestamp.isoformat(),
                        "date_precision": "day",
                    },
                )
            )

    # Prefer auditor replies because they contain the audit procedures and
    # evidence the user is looking for. Keep one document per listed company
    # and bound PDF downloads before the article-reading stage.
    ranked = sorted(
        _unique_by_url(items),
        key=lambda item: (
            any(term in item.title for term in _CNINFO_AUDITOR_TITLE_TERMS),
            item.published_at,
        ),
        reverse=True,
    )
    selected: list[ContentItem] = []
    seen_securities: set[str] = set()
    for item in ranked:
        security_key = item.metadata.get("security_code") or item.url
        if security_key in seen_securities:
            continue
        seen_securities.add(security_key)
        selected.append(item)
        if len(selected) >= 8:
            break
    return selected


class CNInfoAnnouncementSource(Source):
    name = "巨潮资讯年报问询与审计回复"

    async def fetch(self, since: datetime) -> list[ContentItem]:
        local_since = since.astimezone(ZoneInfo("Asia/Shanghai")).date()
        local_today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
        date_range = f"{local_since.isoformat()}~{local_today.isoformat()}"
        headers = {
            "User-Agent": "Mozilla/5.0 audit-regulatory-intel-bot/2.0",
            "Referer": "https://www.cninfo.com.cn/new/commonUrl?url=disclosure/list/notice",
            "Origin": "https://www.cninfo.com.cn",
        }

        async def fetch_term(client: httpx.AsyncClient, term: str) -> dict:
            response = await client.post(
                CNINFO_ANNOUNCEMENTS_API,
                data={
                    "pageNum": "1",
                    "pageSize": "30",
                    "column": "szse",
                    "tabName": "fulltext",
                    "plate": "",
                    "stock": "",
                    "searchkey": term,
                    "secid": "",
                    "category": "",
                    "trade": "",
                    "seDate": date_range,
                    "sortName": "",
                    "sortType": "",
                    "isHLtitle": "true",
                },
                headers=headers,
            )
            response.raise_for_status()
            return response.json()

        try:
            async with httpx.AsyncClient(timeout=25, follow_redirects=True) as client:
                payloads = await asyncio.gather(
                    *(fetch_term(client, term) for term in _CNINFO_QUERY_TERMS)
                )
        except (httpx.HTTPError, ValueError) as exc:
            raise SourceFetchError(f"CNInfo announcement fetch failed: {exc}") from exc
        return parse_cninfo_records(payloads, since)


def parse_mof_listing(html: str, page_url: str, since: datetime) -> list[ContentItem]:
    """Parse MOF sanction rows, using page publication dates embedded in URLs."""
    soup = BeautifulSoup(html, "html.parser")
    items: list[ContentItem] = []
    for row in soup.select("table tr"):
        cells = row.find_all("td")
        anchor = row.find("a", href=True)
        if len(cells) < 3 or anchor is None:
            continue
        href = anchor["href"].strip()
        date_match = re.search(r"/t(\d{8})_\d+\.htm", urljoin(page_url, href))
        if not date_match:
            continue
        try:
            parsed_day = datetime.strptime(date_match.group(1), "%Y%m%d")
        except ValueError:
            continue
        published = _source_day_timestamp(
            parsed_day.year,
            parsed_day.month,
            parsed_day.day,
            "Asia/Shanghai",
            datetime.now(timezone.utc),
        )
        if published < since:
            continue

        title = _clean_text(anchor.get_text(" "))
        script = anchor.find("script")
        if script is not None:
            script_match = re.search(
                r"""var\s+str\s*=\s*["'](?P<title>.*?)["']\s*;""",
                script.get_text(" ", strip=False),
                flags=re.DOTALL,
            )
            if script_match:
                title = _clean_text(script_match.group("title"))
        if not title:
            title = "财政部行政处罚决定书"
        items.append(
            ContentItem(
                source="财政部行政处罚",
                title=title[:500],
                url=urljoin(page_url, href),
                summary=_clean_text(cells[1].get_text(" ")),
                published_at=published,
                metadata={
                    "listing_url": page_url,
                    "document_date": _clean_text(cells[2].get_text(" ")),
                },
            )
        )
    return _unique_by_url(items)


class MinistryOfFinanceSanctionsSource(Source):
    name = "财政部行政处罚"

    def __init__(self, url: str = MOF_SANCTIONS_URL) -> None:
        self.url = url

    async def fetch(self, since: datetime) -> list[ContentItem]:
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
                response = await client.get(
                    self.url,
                    headers={"User-Agent": "audit-regulatory-intel-bot/2.0"},
                )
                response.raise_for_status()
                items = parse_mof_listing(response.text, self.url, since)

                semaphore = asyncio.Semaphore(ARTICLE_READER_CONCURRENCY)

                async def add_detail(item: ContentItem) -> None:
                    try:
                        async with semaphore:
                            detail = await client.get(
                                item.url,
                                headers={"User-Agent": "audit-regulatory-intel-bot/2.0"},
                            )
                            detail.raise_for_status()
                        text = _extract_public_article_text(detail.text, MAX_ARTICLE_CHARS)
                        if len(text) >= MIN_ARTICLE_CHARS:
                            item.summary = text[:8_000]
                            item.article_text = text
                            item.metadata["article_read_status"] = "provided_by_source"
                    except httpx.HTTPError as exc:
                        item.metadata["article_read_status"] = f"unavailable:{type(exc).__name__}"

                await asyncio.gather(*(add_detail(item) for item in items))
                return items
        except httpx.HTTPError as exc:
            raise SourceFetchError(f"MOF sanctions fetch failed: {exc}") from exc


class SECListingSource(Source):
    def __init__(self, name: str, url: str, user_agent: str) -> None:
        self.name = name
        self.url = url
        self.user_agent = user_agent

    async def fetch(self, since: datetime) -> list[ContentItem]:
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
                response = await client.get(self.url, headers={"User-Agent": self.user_agent, "Accept-Encoding": "gzip, deflate"})
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise SourceFetchError(f"public listing fetch failed: {exc}") from exc
        return parse_sec_listing(response.text, self.url, self.name, since)


def parse_sec_listing(html: str, page_url: str, source: str, since: datetime) -> list[ContentItem]:
    """Extract public dated listing rows without downloading a release or complaint."""
    soup = BeautifulSoup(html, "html.parser")
    now = datetime.now(timezone.utc)
    items: list[ContentItem] = []
    for row in soup.select("table tr"):
        cells = row.find_all("td")
        if len(cells) < 2:
            continue
        date_text = _clean_text(cells[0].get_text(" "))
        published = _to_utc(date_text, now)
        if published < since:
            continue
        anchor = row.find("a", href=True)
        title = _clean_text(cells[1].get_text(" "))
        if not anchor or not title:
            continue
        url = urljoin(page_url, anchor["href"])
        items.append(
            ContentItem(
                source=source,
                category=ItemCategory.RISK,
                title=title,
                url=url,
                summary=_clean_text(row.get_text(" ")),
                published_at=published,
                metadata={"listing_url": page_url},
            )
        )
    return items


class PCAOBNewsSource(Source):
    def __init__(self, url: str = PCAOB_NEWS_URL) -> None:
        self.name = "PCAOB"
        self.url = url

    async def fetch(self, since: datetime) -> list[ContentItem]:
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
                response = await client.get(
                    self.url,
                    headers={"User-Agent": "audit-regulatory-intel-bot/2.0"},
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise SourceFetchError(f"PCAOB fetch failed: {exc}") from exc
        return parse_pcaob_listing(response.text, self.url, since)


def parse_pcaob_listing(html: str, page_url: str, since: datetime) -> list[ContentItem]:
    soup = BeautifulSoup(html, "html.parser")
    now = datetime.now(timezone.utc)
    items: list[ContentItem] = []
    seen: set[str] = set()

    # The server-rendered News & Events landing page exposes recent updates
    # even when the dedicated news-release search widget is unavailable.
    for card in soup.select(".recent-updates__item"):
        title_element = card.select_one(".recent-updates__title")
        anchor = card.select_one('a[href*="news-release-detail"]')
        date_element = card.select_one(".recent-updates__date")
        title = _clean_text(title_element.get_text(" ") if title_element else "")
        if not title or anchor is None:
            continue
        url = urljoin(page_url, anchor["href"])
        published = _date_from_context(
            _clean_text(date_element.get_text(" ") if date_element else ""),
            now,
            "America/New_York",
        )
        if published is None or published < since or url in seen:
            continue
        seen.add(url)
        items.append(
            ContentItem(
                source="PCAOB",
                category=ItemCategory.OTHER,
                title=title,
                url=url,
                summary=_clean_text(card.get_text(" "))[:2_000],
                published_at=published,
                metadata={"listing_url": page_url},
            )
        )

    # Retain support for the older server-rendered release listing.
    for anchor in soup.select('a[href*="news-release-detail"]'):
        url = urljoin(page_url, anchor["href"])
        if url in seen:
            continue

        title = _clean_text(anchor.get_text(" "))
        context = title
        date_match = None
        # PCAOB has served several variants of this page. Walk upward until a
        # container supplies both the descriptive heading and the date instead
        # of treating the CTA label ("Read more") as the headline.
        for container in anchor.parents:
            if getattr(container, "name", None) not in {"article", "li", "div"}:
                continue
            candidate_context = _clean_text(container.get_text(" "))
            candidate_date = re.search(
                r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
                r"[a-z]*\.?\s+\d{1,2},\s+\d{4}",
                candidate_context,
                flags=re.IGNORECASE,
            )
            heading = container.select_one("h2, h3, h4")
            candidate_title = _clean_text(heading.get_text(" ") if heading else "")
            if not candidate_title and title.casefold() not in {
                "read more",
                "learn more",
                "view details",
            }:
                candidate_title = title
            if candidate_date and candidate_title:
                title = candidate_title
                context = candidate_context
                date_match = candidate_date
                break

        # A release without a trustworthy listing date must not be labelled as
        # current merely because the crawler ran today.
        if not title or date_match is None:
            continue
        published = _date_from_context(
            date_match.group(0),
            now,
            "America/New_York",
        )
        if published is None or published < since:
            continue
        seen.add(url)
        items.append(
            ContentItem(
                source="PCAOB",
                category=ItemCategory.OTHER,
                title=title,
                url=url,
                summary=context[:2_000],
                published_at=published,
                metadata={"listing_url": page_url},
            )
        )
    return items


class ScholarAlertSource(Source):
    """Read Google Scholar's own alert emails from a dedicated IMAP mailbox.

    The mailbox is opened read-only, so a failed run never loses alerts. In-memory de-duplication
    makes repeated links within a run harmless.
    """

    name = "Google Scholar Alert"

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        folder: str,
        sender: str,
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.folder = folder
        self.sender = sender

    async def fetch(self, since: datetime) -> list[ContentItem]:
        return await asyncio.to_thread(self._fetch_sync, since)

    def _fetch_sync(self, since: datetime) -> list[ContentItem]:
        messages = _fetch_imap_messages(
            self.host,
            self.port,
            self.username,
            self.password,
            self.folder,
            self.sender,
            since,
            "Scholar Alert",
        )

        items: list[ContentItem] = []
        for message in messages:
            items.extend(parse_scholar_message(message, since))
        return items


class FTEmailSource(Source):
    """Parse an account-authorised Financial Times newsletter received by IMAP."""

    name = "Financial Times"

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        folder: str,
        sender: str,
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.folder = folder
        self.sender = sender

    async def fetch(self, since: datetime) -> list[ContentItem]:
        return await asyncio.to_thread(self._fetch_sync, since)

    def _fetch_sync(self, since: datetime) -> list[ContentItem]:
        messages = _fetch_imap_messages(
            self.host,
            self.port,
            self.username,
            self.password,
            self.folder,
            self.sender,
            since,
            "Financial Times",
        )
        items: list[ContentItem] = []
        for message in messages:
            items.extend(parse_newsletter_message(message, since, self.name))
        return _unique_by_url(items)


def _fetch_imap_messages(
    host: str,
    port: int,
    username: str,
    password: str,
    folder: str,
    sender: str,
    since: datetime,
    source_name: str,
) -> list[email.message.Message]:
    search_date = since.astimezone(timezone.utc).strftime("%d-%b-%Y")
    try:
        with imaplib.IMAP4_SSL(host, port) as mailbox:
            mailbox.login(username, password)
            status, _ = mailbox.select(folder, readonly=True)
            if status != "OK":
                raise SourceFetchError(f"cannot open IMAP folder {folder}")
            status, data = mailbox.search(None, "FROM", f'"{sender}"', "SINCE", search_date)
            if status != "OK":
                raise SourceFetchError("cannot search source mailbox")
            message_ids = data[0].split()
            messages: list[email.message.Message] = []
            for message_id in message_ids:
                status, payload = mailbox.fetch(message_id, "(RFC822)")
                if status == "OK" and payload and isinstance(payload[0], tuple):
                    messages.append(email.message_from_bytes(payload[0][1]))
            return messages
    except (imaplib.IMAP4.error, OSError, SourceFetchError) as exc:
        raise SourceFetchError(f"{source_name} IMAP fetch failed: {exc}") from exc


def _decode_header_value(value: str | None) -> str:
    if not value:
        return ""
    return str(make_header(decode_header(value)))


def _message_html(message: email.message.Message) -> str:
    if message.is_multipart():
        for part in message.walk():
            if part.get_content_type() == "text/html" and not part.get_filename():
                return part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", errors="replace")
    elif message.get_content_type() == "text/html":
        return message.get_payload(decode=True).decode(message.get_content_charset() or "utf-8", errors="replace")
    return ""


def parse_scholar_message(message: email.message.Message, since: datetime) -> list[ContentItem]:
    """Parse just paper links/titles and the alert email timestamp, never paper full text."""
    date_header = message.get("Date")
    try:
        published = parsedate_to_datetime(date_header) if date_header else datetime.now(timezone.utc)
    except (TypeError, ValueError, IndexError):
        published = datetime.now(timezone.utc)
    if published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)
    published = published.astimezone(timezone.utc)
    if published < since:
        return []

    subject = _decode_header_value(message.get("Subject"))
    soup = BeautifulSoup(_message_html(message), "html.parser")
    items: list[ContentItem] = []
    ignored = {"unsubscribe", "manage alerts", "google scholar", "view all"}
    for anchor in soup.find_all("a", href=True):
        title = _clean_text(anchor.get_text(" "))
        url = anchor["href"].strip()
        if not title or not url.startswith(("http://", "https://")):
            continue
        if title.lower() in ignored or "unsubscribe" in url.lower():
            continue
        items.append(
            ContentItem(
                source="Google Scholar Alert",
                category=ItemCategory.RESEARCH,
                title=title,
                url=url,
                summary="Scholar Alert：" + subject,
                published_at=published,
                metadata={"alert_subject": subject},
            )
        )
    return _unique_by_url(items)


def parse_newsletter_message(
    message: email.message.Message, since: datetime, source: str
) -> list[ContentItem]:
    """Extract newsletter headline links only; never retrieve subscription-protected pages."""
    date_header = message.get("Date")
    try:
        published = parsedate_to_datetime(date_header) if date_header else datetime.now(timezone.utc)
    except (TypeError, ValueError, IndexError):
        published = datetime.now(timezone.utc)
    if published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)
    published = published.astimezone(timezone.utc)
    if published < since:
        return []

    subject = _decode_header_value(message.get("Subject"))
    ignored = {"unsubscribe", "view online", "manage preferences", "financial times"}
    items: list[ContentItem] = []
    for anchor in BeautifulSoup(_message_html(message), "html.parser").find_all("a", href=True):
        title = _clean_text(anchor.get_text(" "))
        url = anchor["href"].strip()
        if not title or not url.startswith(("http://", "https://")):
            continue
        if title.lower() in ignored or "unsubscribe" in url.lower():
            continue
        items.append(
            ContentItem(
                source=source,
                category=ItemCategory.OTHER,
                title=title,
                url=url,
                summary=f"{source} 官方通讯：{subject}",
                published_at=published,
                metadata={"newsletter_subject": subject},
            )
        )
    return _unique_by_url(items)


def _unique_by_url(items: Iterable[ContentItem]) -> list[ContentItem]:
    seen: set[str] = set()
    unique: list[ContentItem] = []
    for item in items:
        if item.url not in seen:
            seen.add(item.url)
            unique.append(item)
    return unique


def build_sources(settings: Settings) -> list[Source]:
    sources: list[Source] = [
        RSSSource(
            "SEC Accounting & Auditing Enforcement",
            SEC_AAER_RSS,
            ItemCategory.FRAUD_ENFORCEMENT,
        ),
        RSSSource("SEC Press Releases", SEC_PRESS_RSS),
        RSSSource("SEC Litigation Releases", SEC_LITIGATION_RSS),
        RSSSource("SEC Administrative Proceedings", SEC_ADMIN_PROCEEDINGS_RSS),
        PCAOBNewsSource(),
        DatedListingSource(
            "UK FRC Audit & Reporting",
            FRC_AUDIT_ENFORCEMENT_URL,
            "/news-and-events/news/",
            date_timezone="Europe/London",
        ),
        ThomsonReutersPCAOBSource(),
        DatedListingSource(
            "IAASB",
            IAASB_NEWS_URL,
            "/news-events/",
            ItemCategory.PUBLIC_COMPANY_AUDIT,
            "America/New_York",
        ),
        CSRCJSONSource("中国证监会行政处罚", CSRC_PENALTIES_API),
        CSRCJSONSource("中国证监会要闻", CSRC_NEWS_API),
        CNInfoAnnouncementSource(),
        MinistryOfFinanceSanctionsSource(),
        ASICJSONSource(),
        DatedListingSource(
            "AFRC Hong Kong",
            AFRC_PRESS_RELEASES_URL,
            "/en-hk/news-centre/press-releases/",
            date_timezone="Asia/Hong_Kong",
        ),
    ]
    for name, feed_url in settings.extra_rss_feeds:
        sources.append(RSSSource(name, feed_url))
    if settings.ft_enabled:
        if settings.ft_feed_url:
            sources.append(RSSSource("Financial Times", settings.ft_feed_url))
        if settings.ft_email_sender and settings.scholar_enabled:
            sources.append(
                FTEmailSource(
                    settings.scholar_imap_host,
                    settings.scholar_imap_port,
                    settings.scholar_imap_username,
                    settings.scholar_imap_password,
                    settings.scholar_imap_folder,
                    settings.ft_email_sender,
                )
            )
    if settings.scholar_enabled:
        sources.append(
            ScholarAlertSource(
                settings.scholar_imap_host,
                settings.scholar_imap_port,
                settings.scholar_imap_username,
                settings.scholar_imap_password,
                settings.scholar_imap_folder,
                settings.scholar_sender,
            )
        )
    return sources
