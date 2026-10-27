"""Original audit decisions, inspection reports and financial-reporting cases."""

from __future__ import annotations

import asyncio
import calendar
import io
import json
import re
from datetime import datetime, timezone
from urllib.parse import urlencode, urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup
from pypdf import PdfReader

from .models import ContentItem, ItemCategory
from .rules import (
    ESMA_FINANCIAL_REPORTING_URL, FRC_CASE_OUTCOMES_URL, FRC_REPORTING_CASES_URL,
    MAX_ARTICLE_CHARS, MIN_ARTICLE_CHARS, PCAOB_ENFORCEMENT_PATH,
    PCAOB_INSPECTIONS_PATH, PCAOB_NEWS_SEARCH_API,
    PCAOB_NEWS_SEARCH_CLIENT_GUID, PCAOB_NEWS_SEARCH_INDEX,
)
from .sources import Source, SourceFetchError, _clean_text, _extract_pdf_text, _to_utc, _unique_by_url


_INVALID_DATE = datetime.min.replace(tzinfo=timezone.utc)


def _publication(value: str) -> datetime:
    if not re.search(r"\b\d{4}\b", value):
        raise SourceFetchError(f"Publication date is missing its year: {value!r}")
    published = _to_utc(value, _INVALID_DATE)
    if published == _INVALID_DATE:
        raise SourceFetchError(f"Missing or invalid original publication date: {value!r}")
    return published


def _official_url(url: str, domains: set[str]) -> str:
    split = urlsplit(url)
    if split.scheme != "https" or split.hostname not in domains:
        raise SourceFetchError(f"Unexpected original-document URL: {url}")
    return url


async def _read_pdf(client: httpx.AsyncClient, item: ContentItem, semaphore: asyncio.Semaphore) -> None:
    async with semaphore:
        response = await client.get(item.url)
        response.raise_for_status()
    _official_url(str(response.url), {urlsplit(item.url).hostname})
    if not response.content.startswith(b"%PDF"):
        raise SourceFetchError(f"Original document is not a PDF: {item.url}")
    text = _extract_pdf_text(response.content, MAX_ARTICLE_CHARS)
    if len(text) < MIN_ARTICLE_CHARS:
        raise SourceFetchError(f"Original PDF extraction failed ({len(text)} characters): {item.url}")
    item.article_text = text
    item.summary = text[:2_000]
    item.metadata["article_read_status"] = "provided_by_source"


class PCAOBDocumentsSource(Source):
    """Scan public metadata because the API sorts on report/effective dates.

    A report dated months ago can be published today. Early stopping based on
    the API's default order would miss it, so filter only after reading all pages.
    """

    _MAX_PAGES = 100

    def __init__(self, kind: str) -> None:
        if kind not in {"enforcement", "inspections"}:
            raise ValueError("Unknown PCAOB document kind")
        self.kind = kind
        self.name = "PCAOB Enforcement" if kind == "enforcement" else "PCAOB Inspections"
        self.path = PCAOB_ENFORCEMENT_PATH if kind == "enforcement" else PCAOB_INSPECTIONS_PATH

    async def fetch(self, since: datetime) -> list[ContentItem]:
        items = []
        visited = set()
        semaphore = asyncio.Semaphore(4)
        try:
            async with httpx.AsyncClient(timeout=40, follow_redirects=True) as client:
                for page in range(1, self._MAX_PAGES + 1):
                    response = await client.post(PCAOB_NEWS_SEARCH_API, json={
                        "ClientGuid": PCAOB_NEWS_SEARCH_CLIENT_GUID, "IndexName": PCAOB_NEWS_SEARCH_INDEX,
                        "CustomUrl": self.path, "Keyword": "", "SortBy": "newest",
                        "MaxPerPage": 96, "PageNo": page,
                    })
                    response.raise_for_status()
                    payload = response.json()
                    rows = payload.get("Results") if isinstance(payload, dict) else None
                    if payload.get("Success") is not True or not isinstance(rows, list) or not rows:
                        raise SourceFetchError(f"{self.name}: public index returned no usable records")
                    fingerprint = tuple(row.get("DocId", str(row.get("Document", {}))) for row in rows)
                    if fingerprint in visited:
                        raise SourceFetchError(f"{self.name}: public index repeated a page")
                    visited.add(fingerprint)
                    for row in rows:
                        doc = row.get("Document", {})

                        def value(field: str) -> str:
                            values = doc.get(field, [])
                            return str(values[0]) if isinstance(values, list) and values else ""

                        published = _publication(value("publicationdate"))
                        if not since <= published <= datetime.now(timezone.utc):
                            continue
                        if self.kind == "enforcement" and not any(
                            word in value("enforcementordertypes") for word in ("Settled Disciplinary", "Adjudicated Disciplinary")
                        ):
                            continue
                        attachment = json.loads(value("enforcementorderdocument") if self.kind == "enforcement" else value("name"))
                        url = _official_url(attachment.get("mediaUrl", ""), {"assets.pcaobus.org", "pcaobus.org"})
                        title = _clean_text(value("title"))
                        if not title:
                            raise SourceFetchError(f"{self.name}: record has no firm/respondent name")
                        document_kind = "enforcement_order" if self.kind == "enforcement" else "inspection_report"
                        items.append(ContentItem(
                            source=self.name, category=ItemCategory.PUBLIC_COMPANY_AUDIT,
                            title=f"{title} — PCAOB {'disciplinary order' if self.kind == 'enforcement' else 'inspection report'}",
                            url=url, published_at=published,
                            metadata={"api_url": PCAOB_NEWS_SEARCH_API, "listing_url": "https://pcaobus.org" + self.path,
                                      "date_field": "publicationdate", "document_kind": document_kind,
                                      "event_date": value("effectivedate"), "inspection_year": value("inspectionyear")},
                        ))
                    pages = int(payload.get("Pagination", {}).get("NofPages", 0))
                    if pages < 1:
                        raise SourceFetchError(f"{self.name}: missing pagination")
                    if page >= pages:
                        break
                else:
                    raise SourceFetchError(f"{self.name}: metadata scan exceeded {self._MAX_PAGES} pages")
                items = _unique_by_url(items)
                await asyncio.gather(*(_read_pdf(client, item, semaphore) for item in items))
            return items
        except (httpx.HTTPError, ValueError, TypeError, KeyError, AttributeError) as exc:
            raise SourceFetchError(f"{self.name} collection failed: {exc}") from exc


class FRCEnforcementSource(Source):
    name = "UK FRC Enforcement Outcomes"

    async def fetch(self, since: datetime) -> list[ContentItem]:
        items = []
        try:
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                response = await client.get(FRC_CASE_OUTCOMES_URL)
                response.raise_for_status()
                soup = BeautifulSoup(response.text, "html.parser")
                rows = [row for row in soup.select("table tr") if row.select_one('input[name="ids"]')]
                if not rows:
                    raise SourceFetchError("FRC outcomes listing has no cases")
                for row in rows:
                    cells = row.find_all("td")
                    if len(cells) < 6:
                        raise SourceFetchError("FRC outcomes listing has an invalid case row")
                    day = _publication(cells[1].get_text(" "))
                    if not since.date() <= day.date() <= datetime.now(timezone.utc).date():
                        continue
                    firm, entity = (_clean_text(cells[index].get_text(" ")) for index in (2, 3))
                    if cells[5].get_text(strip=True) != "Audit" or entity.casefold() in {"n/a", "uk private company"}:
                        continue
                    case_id = row.select_one('input[name="ids"]')["value"]
                    url = FRC_CASE_OUTCOMES_URL + "print/?" + urlencode({"ids": case_id})
                    detail = await client.get(url)
                    detail.raise_for_status()
                    text = _clean_text(detail.text)
                    # FRC explicitly defines the date on this page as the
                    # publication date; never infer it from a hearing/effect date.
                    if not re.search(r"date at the top.{0,100}publication date", text, re.I):
                        raise SourceFetchError("FRC case detail does not confirm its publication date")
                    body = BeautifulSoup(detail.text, "html.parser").select_one("main")
                    article = _clean_text(str(body or detail.text))[:MAX_ARTICLE_CHARS]
                    if len(article) < MIN_ARTICLE_CHARS:
                        raise SourceFetchError("FRC case summary has insufficient text")
                    items.append(ContentItem(
                        source=self.name, category=ItemCategory.PUBLIC_COMPANY_AUDIT,
                        title=f"{firm} — {entity}: FRC enforcement outcome", url=url, published_at=day,
                        summary=article[:2_000], article_text=article,
                        metadata={"listing_url": FRC_CASE_OUTCOMES_URL, "date_precision": "day",
                                  "date_field": "FRC confirmed publication date", "document_kind": "enforcement_order",
                                  "article_read_status": "provided_by_source"},
                    ))
            return items
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            raise SourceFetchError(f"FRC outcomes collection failed: {exc}") from exc


def split_esma_cases(content: bytes, title: str, url: str, published: datetime) -> list[ContentItem]:
    """Use real PDF page anchors for cases; skip the table of contents."""
    try:
        pages = [(page.extract_text() or "") for page in PdfReader(io.BytesIO(content)).pages]
    except Exception as exc:
        raise SourceFetchError(f"ESMA PDF extraction failed: {exc}") from exc
    sections = []
    current = None
    pattern = re.compile(r"Decision\s+ref\s+((?:EECS|FRWG)/[\d\s]+-\d+)\s*[–—-]\s*([^\n]+)", re.I)
    for page_number, page in enumerate(pages, 1):
        if "Table of Contents" in page or len(pattern.findall(page)) > 1:
            continue
        match = pattern.search(page)
        if match:
            if current:
                sections.append(current)
            reference = re.sub(r"\s+", "", match[1])
            current = [page_number, reference, match[2].strip(), page[match.start():]]
        elif current:
            current[3] += "\n" + page
    if current:
        sections.append(current)
    if not sections:
        sections = [[1, "", title, "\n".join(pages)]]
    items = []
    for page, reference, heading, text in sections:
        text = " ".join(text.split())[:MAX_ARTICLE_CHARS]
        if len(text) < MIN_ARTICLE_CHARS:
            raise SourceFetchError("ESMA case/report has insufficient text")
        items.append(ContentItem(
            source="ESMA Financial Reporting", category=ItemCategory.REPORTING_CONTROLS,
            title=f"{reference}: {heading}" if reference else title,
            url=f"{url}#page={page}" if reference else url, published_at=published,
            summary=text[:2_000], article_text=text,
            metadata={"listing_url": ESMA_FINANCIAL_REPORTING_URL, "date_field": "ESMA publication time",
                      "document_kind": "reporting_case", "case_reference": reference,
                      "pdf_page": page, "article_read_status": "provided_by_source"},
        ))
    return items


class ESMAReportingSource(Source):
    name = "ESMA Financial Reporting"
    _MAX_PAGES = 20

    async def fetch(self, since: datetime) -> list[ContentItem]:
        items = []
        visited = set()
        page_url = ESMA_FINANCIAL_REPORTING_URL
        try:
            async with httpx.AsyncClient(timeout=40, follow_redirects=True) as client:
                for _ in range(self._MAX_PAGES):
                    if page_url in visited:
                        raise SourceFetchError("ESMA financial-reporting listing repeated a page")
                    visited.add(page_url)
                    response = await client.get(page_url)
                    response.raise_for_status()
                    soup = BeautifulSoup(response.text, "html.parser")
                    rows = [row for row in soup.select("table tr") if row.select_one('time[datetime]')]
                    if not rows:
                        raise SourceFetchError("ESMA financial-reporting listing has no dated document rows")
                    dates = []
                    for row in rows:
                        published = _publication(row.select_one('time[datetime]')["datetime"])
                        dates.append(published)
                        if not since <= published <= datetime.now(timezone.utc):
                            continue
                        anchor = row.select_one('.views-field-title a[href*=".pdf"]')
                        if anchor is None:
                            continue
                        title = _clean_text(anchor.get_text(" "))
                        if not re.search(r"enforcement|financial report|IFRS|accounting|APM|performance measures|ECEP", title, re.I):
                            continue
                        url = _official_url(urljoin(page_url, anchor["href"]), {"www.esma.europa.eu", "esma.europa.eu"})
                        document = await client.get(url)
                        document.raise_for_status()
                        if not document.content.startswith(b"%PDF"):
                            raise SourceFetchError("ESMA document is not a PDF")
                        items.extend(split_esma_cases(document.content, title, url, published))
                    next_page = soup.select_one('a[rel="next"]')
                    if min(dates) < since:
                        return _unique_by_url(items)
                    if next_page is None:
                        raise SourceFetchError("ESMA document list does not cover the full requested window")
                    page_url = _official_url(urljoin(page_url, next_page["href"]), {"www.esma.europa.eu"})
                raise SourceFetchError("ESMA listing exceeded pagination limit")
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            raise SourceFetchError(f"ESMA financial reporting collection failed: {exc}") from exc


class FRCReportingCasesSource(Source):
    """Quarterly research materials, explicitly excluded from the daily AI pool."""
    name = "UK FRC Reporting Cases (Topics)"

    async def fetch(self, since: datetime) -> list[ContentItem]:
        items = []
        page_url = FRC_REPORTING_CASES_URL
        visited = set()
        try:
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                for _ in range(100):
                    if page_url in visited:
                        raise SourceFetchError("FRC CRR listing repeated a page")
                    visited.add(page_url)
                    response = await client.get(page_url)
                    response.raise_for_status()
                    soup = BeautifulSoup(response.text, "html.parser")
                    rows = soup.select("table tbody tr")
                    if not rows:
                        raise SourceFetchError("FRC CRR has no case rows")
                    fields = {}
                    periods = []
                    for row in rows:
                        key, cell = row.find("th"), row.find("td")
                        if key is None or cell is None:
                            continue
                        label = _clean_text(key.get_text(" "))
                        if label == "Entity":
                            fields = {}
                        fields[label] = _clean_text(cell.get_text(" "))
                        if label != "Case Summary / Press Notice":
                            continue
                        period = _publication(fields.get("Quarter Published", ""))
                        end = period.replace(day=calendar.monthrange(period.year, period.month)[1])
                        periods.append(end)
                        content = cell.select_one(".rich-text")
                        if end.date() < since.date() or content is None:
                            continue
                        text = _clean_text(str(content))[:MAX_ARTICLE_CHARS]
                        entity = fields.get("Entity", "")
                        if len(text) < MIN_ARTICLE_CHARS or not entity:
                            continue
                        quarter = f"{period.year}-Q{(period.month - 1)//3 + 1}"
                        url = FRC_REPORTING_CASES_URL + "?" + urlencode({"quarter": period.strftime("%Y-%m-01"), "query": entity})
                        items.append(ContentItem(
                            source=self.name, category=ItemCategory.REPORTING_CONTROLS,
                            title=f"{entity} — FRC corporate reporting review ({quarter})", url=url,
                            published_at=end, summary=text[:2_000], article_text=text,
                            metadata={"topic_only": True, "date_precision": "quarter", "publication_label": quarter,
                                      "period_anchor_only": True, "listing_url": FRC_REPORTING_CASES_URL,
                                      "document_kind": "reporting_case", "article_read_status": "provided_by_source"},
                        ))
                    next_page = soup.select_one('a[rel="next"], a[aria-label="Next page"]')
                    if not periods:
                        raise SourceFetchError("FRC CRR contains no publication quarters")
                    if min(periods).date() < since.date() or next_page is None:
                        return _unique_by_url(items)
                    page_url = _official_url(urljoin(page_url, next_page["href"]), {"www.frc.org.uk"})
                raise SourceFetchError("FRC CRR exceeded pagination limit")
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            raise SourceFetchError(f"FRC quarterly case collection failed: {exc}") from exc
