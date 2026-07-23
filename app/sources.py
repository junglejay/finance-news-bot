"""Permitted metadata and excerpt collectors for feeds, public notices, and Scholar Alerts."""

from __future__ import annotations

import asyncio
import email
import imaplib
import logging
import re
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime
from typing import Iterable
from urllib.parse import urljoin, urlsplit

import feedparser
import httpx
from bs4 import BeautifulSoup
from dateutil import parser as date_parser, tz

from .config import Settings
from .models import ContentItem, ItemCategory
from .rules import (
    ARTICLE_READER_CONCURRENCY,
    BOJ_WHATS_NEW_RSS,
    BOK_PRESS_RELEASES_RSS,
    BIS_PRESS_RELEASES_RSS,
    BIS_STATISTICAL_RELEASES_RSS,
    CFTC_PRESS_URL,
    EBA_NEWS_RSS,
    ECB_NEWS_RSS,
    ECB_STATISTICAL_RELEASES_RSS,
    EIA_PRESS_RELEASES_RSS,
    EIA_TODAY_IN_ENERGY_RSS,
    FEDERAL_RESERVE_PRESS_RSS,
    FORBES_BUSINESS_RSS,
    FULL_TEXT_BLOCKED_SOURCES,
    HTTPS_UPGRADE_HOSTS,
    MAX_ARTICLE_CHARS,
    MIN_ARTICLE_CHARS,
    PCAOB_NEWS_URL,
    PUBLIC_ARTICLE_DOMAINS,
    SEC_AAER_URL,
    SEC_PRESS_URL,
    GUARDIAN_BUSINESS_RSS,
    WSJ_US_BUSINESS_RSS,
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


def _upgrade_known_https_url(value: str) -> str:
    """Use HTTPS for official feeds whose legacy items still contain HTTP links."""
    split = urlsplit(value)
    if split.scheme == "http" and (split.hostname or "").lower() in HTTPS_UPGRADE_HOSTS:
        return split._replace(scheme="https").geturl()
    return value


class RSSSource(Source):
    def __init__(self, name: str, feed_url: str, category: ItemCategory = ItemCategory.OTHER) -> None:
        self.name = name
        self.feed_url = feed_url
        self.category = category

    async def fetch(self, since: datetime) -> list[ContentItem]:
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
                response = await client.get(self.feed_url, headers={"User-Agent": "commodity-risk-intel-bot/1.0"})
                response.raise_for_status()
            parsed = feedparser.parse(response.content)
        except (httpx.HTTPError, ValueError) as exc:
            raise SourceFetchError(f"RSS fetch failed: {exc}") from exc

        now = datetime.now(timezone.utc)
        items: list[ContentItem] = []
        for entry in parsed.entries:
            title = _clean_text(entry.get("title", ""))
            url = _upgrade_known_https_url(entry.get("link", "").strip())
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
        if not self._is_allowed(item):
            logger.debug(f"Skipping {item.source} '{item.title[:50]}' - source/domain not allowed")
            return
        try:
            async with semaphore:
                response = await client.get(
                    item.url,
                    headers={"User-Agent": "commodity-risk-intel-bot/1.0", "Accept": "text/html,application/xhtml+xml"},
                )
                response.raise_for_status()
            content_type = response.headers.get("content-type", "").lower()
            if "html" not in content_type:
                item.metadata["article_read_status"] = "skipped_non_html"
                logger.debug(f"Skipping {item.source} - non-HTML content type: {content_type}")
                return
            article_text = _extract_public_article_text(response.text, self.max_characters)
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
    root = soup.find("article") or soup.find("main") or soup.select_one('[role="main"]') or soup.body
    if root is None:
        return ""
    blocks: list[str] = []
    for element in root.select("p, h2, h3, li"):
        text = _clean_text(element.get_text(" "))
        if len(text) < 30 or (blocks and text == blocks[-1]):
            continue
        blocks.append(text)
    return "\n".join(blocks)[:max_characters]


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
                response = await client.get(self.url, headers={"User-Agent": "commodity-risk-intel-bot/1.0"})
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise SourceFetchError(f"PCAOB fetch failed: {exc}") from exc
        return parse_pcaob_listing(response.text, self.url, since)


def parse_pcaob_listing(html: str, page_url: str, since: datetime) -> list[ContentItem]:
    soup = BeautifulSoup(html, "html.parser")
    now = datetime.now(timezone.utc)
    items: list[ContentItem] = []
    seen: set[str] = set()
    for anchor in soup.select('a[href*="news-release-detail"]'):
        title = _clean_text(anchor.get_text(" "))
        url = urljoin(page_url, anchor["href"])
        if not title or url in seen:
            continue
        seen.add(url)
        container = anchor.find_parent(["article", "li", "div"])
        context = _clean_text(container.get_text(" ") if container else title)
        date_match = re.search(
            r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},\s+\d{4}",
            context,
            flags=re.IGNORECASE,
        )
        published = _to_utc(date_match.group(0) if date_match else None, now)
        if published < since:
            continue
        items.append(
            ContentItem(
                source="PCAOB",
                category=ItemCategory.RISK,
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
        RSSSource("Forbes", FORBES_BUSINESS_RSS),
        RSSSource("U.S. EIA Today in Energy", EIA_TODAY_IN_ENERGY_RSS),
        RSSSource("U.S. EIA Press Releases", EIA_PRESS_RELEASES_RSS),
        RSSSource("Federal Reserve Press Releases", FEDERAL_RESERVE_PRESS_RSS),
        RSSSource("Bank of Japan", BOJ_WHATS_NEW_RSS),
        RSSSource("Bank of Korea Press Releases", BOK_PRESS_RELEASES_RSS),
        RSSSource("BIS Press Releases", BIS_PRESS_RELEASES_RSS),
        RSSSource("BIS Statistical Releases", BIS_STATISTICAL_RELEASES_RSS),
        RSSSource("ECB News", ECB_NEWS_RSS),
        RSSSource("ECB Statistical Releases", ECB_STATISTICAL_RELEASES_RSS),
        RSSSource("European Banking Authority", EBA_NEWS_RSS),
        SECListingSource("CFTC Press Releases", CFTC_PRESS_URL, settings.sec_user_agent),
        SECListingSource("SEC Accounting & Auditing Enforcement", SEC_AAER_URL, settings.sec_user_agent),
        SECListingSource("SEC Press Releases", SEC_PRESS_URL, settings.sec_user_agent),
        PCAOBNewsSource(),
        RSSSource("Guardian Business", GUARDIAN_BUSINESS_RSS),
        RSSSource("WSJ US Business", WSJ_US_BUSINESS_RSS),
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
