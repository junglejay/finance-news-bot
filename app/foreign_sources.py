"""Public professional news and enforcement sources with explicit publication dates."""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

import httpx
from bs4 import BeautifulSoup

from .models import ContentItem, ItemCategory
from .editorial import focused_news_title, primary_references
from .rules import (
    CPAB_ENFORCEMENT_REPORTS_URL,
    CPAB_EXPRESS_ARCHIVE_URL,
    DOJ_PRESS_RELEASES_API,
    JOA_NEWS_URL,
    MAX_ARTICLE_CHARS,
    MIN_ARTICLE_CHARS,
)
from .sources import (
    Source,
    SourceFetchError,
    _clean_text,
    _extract_pdf_text,
    _extract_public_article_text,
    _to_utc,
    _unique_by_url,
)

logger = logging.getLogger(__name__)
_INVALID_DATE = datetime.min.replace(tzinfo=timezone.utc)


def _date(value: str) -> datetime:
    published = _to_utc(value, _INVALID_DATE)
    if published == _INVALID_DATE:
        raise SourceFetchError(f"Missing or invalid publication date: {value!r}")
    return published


def _day_in_window(published: datetime, since: datetime, zone: str) -> bool:
    """Date-only publishers cannot support an exact intraday cutoff."""
    today = datetime.now(timezone.utc).astimezone(ZoneInfo(zone)).date()
    return since.astimezone(ZoneInfo(zone)).date() <= published.date() <= today


class JournalOfAccountancySource(Source):
    name = "Journal of Accountancy"
    _MAX_PAGES = 20

    async def fetch(self, since: datetime) -> list[ContentItem]:
        self.collection_stats = {"metadata_count": 0, "detail_requests": 0}
        items: list[ContentItem] = []
        visited: set[str] = set()
        page_url = JOA_NEWS_URL
        semaphore = asyncio.Semaphore(4)
        try:
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                for _ in range(self._MAX_PAGES):
                    if page_url in visited:
                        raise SourceFetchError("JOA listing repeated a page")
                    visited.add(page_url)
                    response = await client.get(page_url)
                    response.raise_for_status()
                    soup = BeautifulSoup(response.text, "html.parser")
                    cards = soup.select("article .entry-title a")
                    if not cards:
                        raise SourceFetchError("JOA listing contains no news cards")
                    self.collection_stats["metadata_count"] += len(cards)
                    dates: list[datetime] = []
                    pending: list[tuple[str, str]] = []
                    for anchor in cards:
                        card = anchor.find_parent("article")
                        time = card.select_one("time[datetime]")
                        published = _date(time.get("datetime", "") if time else "")
                        dates.append(published)
                        url = urljoin(page_url, anchor["href"])
                        if urlsplit(url).hostname != "www.journalofaccountancy.com":
                            raise SourceFetchError("JOA listing contains an unexpected article host")
                        if published >= since and focused_news_title(_clean_text(anchor.get_text(" "))):
                            pending.append((_clean_text(anchor.get_text(" ")), url))

                    async def read(title: str, url: str) -> ContentItem | None:
                        async with semaphore:
                            self.collection_stats["detail_requests"] += 1
                            detail = await client.get(url)
                            detail.raise_for_status()
                        article = BeautifulSoup(detail.text, "html.parser")
                        first = article.select_one('meta[property="article:published_time"]')
                        published = _date(first.get("content", "") if first else "")
                        # Listings may resurface an old story on its modified date.
                        if not since <= published <= datetime.now(timezone.utc):
                            return None
                        text = _extract_public_article_text(detail.text, MAX_ARTICLE_CHARS)
                        return ContentItem(
                            source=self.name, title=title, url=url,
                            published_at=published, summary=text[:2_000],
                            article_text=text if len(text) >= MIN_ARTICLE_CHARS else "",
                            metadata={
                                "listing_url": JOA_NEWS_URL,
                                "date_field": "article:published_time",
                                "primary_references": primary_references(detail.text, url),
                            },
                        )

                    items.extend(item for item in await asyncio.gather(*(read(*row) for row in pending)) if item)
                    next_link = soup.select_one("a[rel=next]") or next((
                        anchor for anchor in soup.select(".nav-previous a, .nav-next a")
                        if re.fullmatch(r"Next(?:\s+\d+)?", anchor.get_text(" ", strip=True), re.I)
                    ), None)
                    if min(dates) < since or next_link is None:
                        return _unique_by_url(items)
                    page_url = urljoin(page_url, next_link["href"])
                    if urlsplit(page_url).hostname != "www.journalofaccountancy.com":
                        raise SourceFetchError("JOA pagination left the official site")
            raise SourceFetchError("JOA pagination limit reached before covering the window")
        except httpx.HTTPError as exc:
            raise SourceFetchError(f"JOA fetch failed: {exc}") from exc


class DOJPressSource(Source):
    name = "US DOJ Accounting Enforcement"
    _PAGE_SIZE = 50
    _MAX_PAGES = 100
    _REQUEST_INTERVAL = 0.3  # Official API permits at most four requests/second.

    async def fetch(self, since: datetime) -> list[ContentItem]:
        self.collection_stats = {"metadata_count": 0, "detail_requests": 0}
        items: list[ContentItem] = []
        visited: set[tuple[str, ...]] = set()
        try:
            async with httpx.AsyncClient(timeout=40, follow_redirects=True) as client:
                for page in range(self._MAX_PAGES):
                    if page:
                        await asyncio.sleep(self._REQUEST_INTERVAL)
                    response = await client.get(DOJ_PRESS_RELEASES_API, params={
                        "sort": "date", "direction": "DESC", "pagesize": self._PAGE_SIZE,
                        "page": page, "fields": "title,url,date,uuid",
                    })
                    response.raise_for_status()
                    payload = response.json()
                    rows = payload.get("results") if isinstance(payload, dict) else None
                    if not isinstance(rows, list) or not isinstance(payload.get("metadata"), dict):
                        raise SourceFetchError("DOJ API returned an invalid result set")
                    if any(not isinstance(row, dict) for row in rows):
                        raise SourceFetchError("DOJ API returned an invalid release")
                    if not rows:
                        return _unique_by_url(items)
                    self.collection_stats["metadata_count"] += len(rows)
                    fingerprint = tuple(str(row.get("uuid") or row.get("url")) for row in rows)
                    if fingerprint in visited:
                        raise SourceFetchError("DOJ API repeated a page")
                    visited.add(fingerprint)
                    dates: list[datetime] = []
                    for row in rows:
                        try:
                            published = datetime.fromtimestamp(int(row["date"]), timezone.utc)
                        except (KeyError, TypeError, ValueError, OverflowError, OSError) as exc:
                            raise SourceFetchError("DOJ API returned an invalid publication date") from exc
                        dates.append(published)
                        url = row.get("url", "")
                        if urlsplit(url).hostname not in {"www.justice.gov", "justice.gov"}:
                            raise SourceFetchError("DOJ API returned an unexpected article host")
                        if not _day_in_window(published, since, "America/New_York"):
                            continue
                        title = _clean_text(row.get("title", ""))
                        if not focused_news_title(title):
                            continue
                        if row.get("body"):
                            text = _clean_text(row["body"])[:MAX_ARTICLE_CHARS]
                        else:
                            self.collection_stats["detail_requests"] += 1
                            detail = await client.get(url)
                            detail.raise_for_status()
                            text = _extract_public_article_text(detail.text, MAX_ARTICLE_CHARS)
                        items.append(ContentItem(
                            source=self.name, title=title, url=url,
                            published_at=published, summary=text[:2_000],
                            article_text=text if len(text) >= MIN_ARTICLE_CHARS else "",
                            metadata={"api_url": str(response.url), "date_precision": "day", "date_field": "date"},
                        ))
                    if min(dates).date() < since.astimezone(ZoneInfo("America/New_York")).date():
                        return _unique_by_url(items)
                    if len(rows) < self._PAGE_SIZE:
                        return _unique_by_url(items)
            raise SourceFetchError("DOJ pagination limit reached before covering the window")
        except (httpx.HTTPError, ValueError) as exc:
            raise SourceFetchError(f"DOJ API fetch failed: {exc}") from exc


class CPABSource(Source):
    name = "Canada CPAB"

    async def fetch(self, since: datetime) -> list[ContentItem]:
        """Use dated Express announcements and the PDF's Published date for orders.

        Inspection year and enforcement effective dates are not publication dates.
        Individual inspection PDFs with only a month are covered by Express's
        dated publication announcements instead of inventing a publication day.
        """
        semaphore = asyncio.Semaphore(4)
        try:
            async with httpx.AsyncClient(timeout=40, follow_redirects=True) as client:
                listing = await client.get(CPAB_ENFORCEMENT_REPORTS_URL)
                listing.raise_for_status()
                soup = BeautifulSoup(listing.text, "html.parser")
                links = soup.select('table tbody a[href*="DownloadDisclosureDocument="]')
                if not links:
                    raise SourceFetchError("CPAB enforcement listing has no report links")
                urls = list(dict.fromkeys(urljoin(str(listing.url), a["href"]) for a in links))
                if len(urls) > 200:
                    raise SourceFetchError("CPAB report limit exceeded")
                dated_reports = 0

                async def report(url: str) -> ContentItem | None:
                    nonlocal dated_reports
                    if urlsplit(url).hostname != "cpab-ccrc.ca":
                        raise SourceFetchError("CPAB report left the official site")
                    async with semaphore:
                        response = await client.get(url)
                        response.raise_for_status()
                    if not response.content.startswith(b"%PDF"):
                        raise SourceFetchError("CPAB report is not a PDF")
                    text = _extract_pdf_text(response.content, MAX_ARTICLE_CHARS)
                    if len(text) < MIN_ARTICLE_CHARS:
                        raise SourceFetchError(
                            f"CPAB report text extraction failed ({len(text)} characters): {url}"
                        )
                    match = re.search(r"\bPublished\s*:?\s+(\d{4}[-\u2010-\u2015]\d{2}[-\u2010-\u2015]\d{2})", text, re.I)
                    if not match:
                        logger.info(
                            "CPAB report skipped: readable PDF has no precise Published date; "
                            "effective dates are not publication dates: %s",
                            url,
                        )
                        return None
                    published = _date(re.sub(r"[\u2010-\u2015]", "-", match[1]))
                    dated_reports += 1
                    if not _day_in_window(published, since, "America/Toronto"):
                        return None
                    title = re.split(r"\bPublished\b", text, maxsplit=1, flags=re.I)[0].strip()[:300]
                    return ContentItem(
                        source=self.name, category=ItemCategory.PUBLIC_COMPANY_AUDIT,
                        title=title, url=url, published_at=published, summary=text[:2_000], article_text=text,
                        metadata={"listing_url": CPAB_ENFORCEMENT_REPORTS_URL, "date_precision": "day", "date_field": "PDF Published"},
                    )

                reports = await asyncio.gather(*(report(url) for url in urls))
                logger.info(
                    "CPAB enforcement reports: %d readable, %d with publication dates, "
                    "%d skipped without publication dates, %d in window",
                    len(urls),
                    dated_reports,
                    len(urls) - dated_reports,
                    sum(item is not None for item in reports),
                )
                # Valid orders can omit publication dates entirely. Their
                # exclusion must not prevent independent dated Express news
                # from being collected, even when every order is undated.
                response = await client.get(CPAB_EXPRESS_ARCHIVE_URL)
                response.raise_for_status()
                archive = BeautifulSoup(response.text, "html.parser")
                cards = archive.select(".cpab-card-express")
                if not cards:
                    raise SourceFetchError("CPAB Express archive has no dated cards")
                items = [item for item in reports if item]
                for card in cards:
                    anchor, footer = card.select_one("a.card-title"), card.select_one(".card-footer")
                    if anchor is None or footer is None:
                        raise SourceFetchError("CPAB Express card has no link/date")
                    published = _date(footer.get_text(" "))
                    if not _day_in_window(published, since, "America/Toronto"):
                        continue
                    url = anchor["href"]
                    if urlsplit(url).hostname != "secure.campaigner.com" or not urlsplit(url).path.startswith("/CSB/Public/"):
                        raise SourceFetchError("CPAB Express link is not a public newsletter")
                    detail = await client.get(url)
                    detail.raise_for_status()
                    text = _clean_text(detail.text)[:MAX_ARTICLE_CHARS]
                    items.append(ContentItem(
                        source=self.name, category=ItemCategory.PUBLIC_COMPANY_AUDIT,
                        title=_clean_text(anchor.get_text(" ")), url=url, published_at=published,
                        summary=text[:2_000], article_text=text if len(text) >= MIN_ARTICLE_CHARS else "",
                        metadata={"listing_url": CPAB_EXPRESS_ARCHIVE_URL, "date_precision": "day"},
                    ))
                return _unique_by_url(items)
        except httpx.HTTPError as exc:
            raise SourceFetchError(f"CPAB fetch failed: {exc}") from exc
