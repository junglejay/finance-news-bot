"""Conservative event matching and article-level editorial gates."""

from __future__ import annotations

import re
from datetime import timedelta
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from .models import ContentItem
from .rules import AUTHORITATIVE_DOMAINS


MEDIA_SOURCES = {"Journal of Accountancy", "Thomson Reuters PCAOB", "Financial Times"}
_LOW_VALUE_TITLES = (
    r"\b(?:passing of|in memoriam|obituary|mourns|tribute to)\b",
    r"\b(?:meets? with|meeting with|regulatory dialogue|supervisory mou)\b",
    r"\b(?:advisory group|advisory council|committee)\s+to meet\b",
    r"\b(?:register now|call for papers|receive.{0,25}honou?rs?|appoints? new)\b",
    r"(?:悼念|讣告|逝世|礼节性会见|拜会)",
)
_CHANGE_TITLE = re.compile(
    r"change of auditor|change in.{0,30}accountant|item 4\.01|会計監査人の異動|更换.{0,8}审计|變更核數師",
    re.I,
)
_RED_FLAGS = re.compile(
    r"accounting fraud|financial irregularit|\brestatements?\b|restated(?:\s+\w+){0,3}\s+financial statements|non.reliance|material weakness|"
    r"scope limitation|unable to obtain|unpaid (?:audit )?fees|disagreements?|"
    r"reportable events?|\bqualified opinion|disclaimer of opinion|adverse opinion|"
    r"不正会計|不適切な会計|会計不正|会計処理|财务造假|財務造假|会计分歧|會計分歧|"
    r"无法表示意见|無法表示意見|保留意见|保留意見|内控缺陷|內控缺陷|"
    r"未付.{0,8}费用|未付.{0,8}費用|拖欠|審計範圍受限|审计范围受限",
    re.I,
)
_STAGES = {
    "investigation": r"\b(?:investigation|investigates?|inquiry)\b|立案|调查|調查",
    "charges": r"\b(?:charges?|charged|indictment)\b|指控|起诉|起訴",
    "settlement": r"\b(?:settles?|settlement|sanctions?)\b|和解|处罚|處罰",
    "sentence": r"\b(?:sentenced?|convicted|verdict)\b|判决|判決|判刑",
    "appeal": r"\b(?:appeals?|overturns?)\b|上诉|上訴|复核|覆核",
}


def low_value_reason(item: ContentItem) -> str | None:
    if item.metadata.get("topic_only"):
        return "季度或月份精度材料仅进入专题入口"
    if any(re.search(pattern, item.title, re.I) for pattern in _LOW_VALUE_TITLES):
        return "悼念、会见或一般活动通知"
    if _CHANGE_TITLE.search(item.title) and "4.02" not in str(item.metadata.get("item_codes", item.title)):
        text = f"{item.title}\n{item.summary}\n{item.article_text}"
        # Standard auditor-change disclosures explicitly deny disagreements and
        # reportable events. Those denials are not adverse accounting signals.
        text = re.sub(
            r"\b(?:no|not\s+(?:(?:have|had|been|reported|identified|aware of)\s+)?any)\b"
            r"[^.;。\n]{0,180}?\b(?:disagreements?|reportable events?|material weaknesses?)\b"
            r"(?:\s*(?:,|or|and)\s*(?:any\s+)?(?:disagreements?|reportable events?|material weaknesses?))*",
            "", text, flags=re.I,
        )
        text = re.sub(r"(?:不存在|并无|並無|没有|沒有|無)[^。;\n]{0,35}(?:分歧|争议|爭議)", "", text)
        text = re.sub(
            r"\b(?:did|does|do)\s+not\s+contain\s+(?:an?\s+)?(?:adverse opinion|disclaimer of opinion)"
            r"(?:\s+(?:or|and)\s+(?:an?\s+)?(?:adverse opinion|disclaimer of opinion))*",
            "", text, flags=re.I,
        )
        text = re.sub(
            r"\bany matter that was (?:the )?subject of (?:a )?disagreement or (?:a )?reportable event\b",
            "", text, flags=re.I,
        )
        if not _RED_FLAGS.search(text):
            return "例行审计师更换，未披露会计争议或其他实质风险"
    return None


def focused_news_title(title: str) -> bool:
    """Cheap discovery gate for broad media/DOJ listings, not terse orders."""
    return bool(re.search(
        r"accounting|financial (?:statements?|reporting)|audit|pcaob|fasb|restat|"
        r"internal control|earnings|fictitious revenue|overstat.{0,15}revenue|chief financial|chief accounting|\bCFO\b|controller|"
        r"会计|审计|财报|财务报|内控|重述", title, re.I,
    ))


def canonical_url(url: str) -> str:
    split = urlsplit(url)
    query = [(key, value) for key, value in parse_qsl(split.query, keep_blank_values=True)
             if not key.lower().startswith("utm_") and key.lower() not in {"fbclid", "gclid"}]
    # PDF page fragments identify separately extracted cases in one report.
    fragment = split.fragment if re.fullmatch(r"page=\d+", split.fragment) else ""
    return urlunsplit((split.scheme.lower(), split.netloc.lower(), split.path.rstrip("/"), urlencode(query), fragment))


def primary_references(html: str, page_url: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    root = soup.select_one(".entry-content") or soup.select_one(".ck-content") or soup.find("article") or soup.find("main")
    if root is None:
        return []
    for element in root.select("nav, footer, aside, .related-posts, .related-content"):
        element.decompose()
    references = []
    for anchor in root.select("a[href]"):
        target = urljoin(page_url, anchor["href"])
        wrapper = urlsplit(target)
        # Read the documented destination parameter locally; never visit the
        # mail-security wrapper or retain its recipient/tracking parameters.
        if (wrapper.hostname or "").endswith(".safelinks.protection.outlook.com"):
            target = dict(parse_qsl(wrapper.query)).get("url", target)
        url = canonical_url(target)
        split = urlsplit(url)
        host = split.hostname or ""
        if split.scheme != "https" or not any(host == domain or host.endswith("." + domain) for domain in AUTHORITATIVE_DOMAINS):
            continue
        # Homepages and topic/navigation links do not establish event identity.
        if split.path.lower().endswith(".pdf") or any(part in split.path for part in ("news-release-detail/", "/news/20", "/press-release", "/litigation/", "/enforcement-litigation/")):
            references.append(url)
    return list(dict.fromkeys(references))


def source_priority(item: ContentItem) -> int:
    if item.source in MEDIA_SOURCES:
        return 0
    if item.metadata.get("document_kind") in {"enforcement_order", "inspection_report", "reporting_case"}:
        return 2
    if item.source in {"SEC Accounting & Auditing Enforcement", "中国证监会行政处罚", "财政部行政处罚"}:
        return 2
    if item.metadata.get("date_field") == "PDF Published" or "4.02" in item.metadata.get("item_codes", []):
        return 2
    host = urlsplit(item.url).hostname or ""
    return int(any(host == domain or host.endswith("." + domain) for domain in AUTHORITATIVE_DOMAINS))


def _stages(item: ContentItem) -> set[str]:
    return {stage for stage, pattern in _STAGES.items() if re.search(pattern, item.title, re.I)}


def _independent_reporting(item: ContentItem) -> bool:
    return bool(item.metadata.get("independent_reporting") or re.search(
        r"\b(?:exclusive|interview|new evidence)\b|独家|新证据|獨家|新證據", item.title, re.I
    ))


def _unambiguous_references(item: ContentItem) -> list[str]:
    references = list(dict.fromkeys(canonical_url(url) for url in item.metadata.get("primary_references", [])))
    documents = [url for url in references if urlsplit(url).path.lower().endswith(".pdf")]
    articles = [url for url in references if url not in documents]
    # One report plus its announcement is still one event. Several reports or
    # several announcements could be a roundup and cannot safely be collapsed.
    return references if len(documents) <= 1 and len(articles) <= 1 else []


def deduplicate_events(items: list[ContentItem], delivered_urls: set[str] | None = None) -> list[ContentItem]:
    """Merge explicit primary citations or identical headlines, never fuzzy cases.

    Different procedural stages remain independent updates. All alternate URLs
    are attached to the representative so successful delivery can remember them.
    """
    delivered = {canonical_url(url) for url in (delivered_urls or ())}
    parents = list(range(len(items)))

    def find(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def merge(left: int, right: int) -> None:
        if abs(items[left].published_at - items[right].published_at) > timedelta(days=7):
            return
        a, b = _stages(items[left]), _stages(items[right])
        if a != b and (a or b):
            return
        parents[find(left)] = find(right)

    by_url: dict[str, int] = {}
    by_title: dict[str, int] = {}
    skipped = set()
    for index, item in enumerate(items):
        url = canonical_url(item.url)
        if url in by_url:
            parents[find(index)] = find(by_url[url])
        by_url[url] = index
        title = re.sub(r"[\W_]+", " ", item.title.casefold()).strip()
        if len(title) >= 25 and title in by_title:
            other = by_title[title]
            if abs(item.published_at - items[other].published_at) <= timedelta(days=1) and (
                urlsplit(item.url).hostname == urlsplit(items[other].url).hostname
                or item.source in MEDIA_SOURCES or items[other].source in MEDIA_SOURCES
            ):
                merge(index, other)
        by_title[title] = index
        if url in delivered:
            skipped.add(index)
    by_reference: dict[str, int] = {}
    for index, item in enumerate(items):
        references = _unambiguous_references(item)
        # Several official citations may describe several events. Never let one
        # roundup article bridge and collapse distinct original decisions.
        if item.source not in MEDIA_SOURCES or not references or _independent_reporting(item):
            continue
        for reference in references:
            reference = canonical_url(reference)
            if reference in by_url:
                merge(index, by_url[reference])
            elif reference in delivered and item.source in MEDIA_SOURCES and not _stages(item):
                skipped.add(index)
            if reference in by_reference:
                merge(index, by_reference[reference])
            by_reference[reference] = index
    groups: dict[int, list[int]] = {}
    for index in range(len(items)):
        groups.setdefault(find(index), []).append(index)
    result = []
    for members in groups.values():
        if any(index in skipped for index in members):
            for index in members:
                items[index].metadata["duplicate_of_delivered_event"] = True
            continue
        winner = max(members, key=lambda index: (
            bool(items[index].article_text), source_priority(items[index]), items[index].score, items[index].published_at
        ))
        representative = items[winner]
        aliases = {items[index].url for index in members}
        for index in members:
            aliases.update(items[index].metadata.get("event_aliases", []))
            references = _unambiguous_references(items[index])
            if items[index].source in MEDIA_SOURCES and not _independent_reporting(items[index]) and not _stages(items[index]):
                aliases.update(canonical_url(url) for url in references)
            if index != winner:
                items[index].metadata["duplicate_of"] = representative.url
        representative.metadata["event_aliases"] = sorted(aliases)
        result.append(representative)
    return result
