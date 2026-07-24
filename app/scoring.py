"""High-precision deterministic topic classification and candidate ranking."""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timezone
from urllib.parse import urlsplit

from .models import ContentItem, ItemCategory
from .rules import (
    AUDIT_QUALITY_TERMS,
    AUDIT_TERMS,
    AUTHORITATIVE_DOMAINS,
    AUTHORITATIVE_SOURCE_BONUS,
    AUTHORITATIVE_SOURCE_PREFIXES,
    CATEGORY_LIMITS,
    DEDICATED_AUDIT_SOURCE_PREFIXES,
    DEDICATED_FRAUD_SOURCE_PREFIXES,
    DEDICATED_SOURCE_BONUS,
    ENFORCEMENT_ACTION_TERMS,
    FRAUD_TERMS,
    GENERIC_REGULATORY_EVENT_TERMS,
    LOW_VALUE_PROCEDURAL_TERMS,
    MAX_CANDIDATES,
    MAX_ITEMS_PER_SOURCE,
    MIN_RELEVANCE_SCORE,
    NON_PUBLIC_COMPANY_AUDIT_TERMS,
    OFF_TOPIC_PENALTY,
    OFF_TOPIC_TERMS,
    PUBLIC_COMPANY_CONTEXT_TERMS,
    REGULATORY_SOURCE_PREFIXES,
    REPORTING_CONTROL_TERMS,
    RESEARCH_TERMS,
)


def _matches(text: str, terms: set[str]) -> list[str]:
    """Match whole ASCII terms and substring-match CJK phrases."""
    hits: list[str] = []
    for term in terms:
        if term.isascii():
            if re.search(rf"(?<!\w){re.escape(term)}(?!\w)", text, flags=re.IGNORECASE):
                hits.append(term)
        elif term in text:
            hits.append(term)
    return sorted(hits)


def _source_starts_with(source: str, prefixes: tuple[str, ...]) -> bool:
    normalized = source.strip().casefold()
    return any(
        normalized == prefix.casefold()
        or normalized.startswith(f"{prefix.casefold()} ")
        for prefix in prefixes
    )


def _is_authoritative(item: ContentItem) -> bool:
    if _source_starts_with(item.source, AUTHORITATIVE_SOURCE_PREFIXES):
        return True
    hostname = (urlsplit(item.url).hostname or "").casefold()
    return any(
        hostname == domain or hostname.endswith(f".{domain}")
        for domain in AUTHORITATIVE_DOMAINS
    )


def score_item(item: ContentItem, now: datetime | None = None) -> ContentItem:
    """Classify only articles that satisfy a compound audit/reporting topic gate."""
    now = now or datetime.now(timezone.utc)
    # Some official feeds expose respondent names or generic headlines only.
    # When public full text has already been read, classify against it as well
    # so a relevant case is not discarded before the AI stage.
    text = f"{item.title}\n{item.summary}\n{item.article_text}".casefold()

    fraud_hits = _matches(text, FRAUD_TERMS)
    enforcement_hits = _matches(text, ENFORCEMENT_ACTION_TERMS)
    audit_hits = _matches(text, AUDIT_TERMS)
    audit_quality_hits = _matches(text, AUDIT_QUALITY_TERMS)
    reporting_hits = _matches(text, REPORTING_CONTROL_TERMS)
    issuer_hits = _matches(text, PUBLIC_COMPANY_CONTEXT_TERMS)
    research_hits = _matches(text, RESEARCH_TERMS)
    off_topic_hits = _matches(text, OFF_TOPIC_TERMS)
    procedural_hits = _matches(text, LOW_VALUE_PROCEDURAL_TERMS)
    non_public_audit_hits = _matches(text, NON_PUBLIC_COMPANY_AUDIT_TERMS)
    generic_event_hits = _matches(item.title.casefold(), GENERIC_REGULATORY_EVENT_TERMS)
    title_topic_hits = (
        _matches(item.title.casefold(), FRAUD_TERMS)
        + _matches(item.title.casefold(), AUDIT_TERMS)
        + _matches(item.title.casefold(), REPORTING_CONTROL_TERMS)
    )

    is_regulator = _source_starts_with(item.source, REGULATORY_SOURCE_PREFIXES)
    dedicated_fraud = _source_starts_with(item.source, DEDICATED_FRAUD_SOURCE_PREFIXES)
    dedicated_audit = _source_starts_with(item.source, DEDICATED_AUDIT_SOURCE_PREFIXES)
    audit_regulator = _source_starts_with(
        item.source,
        ("AFRC", "ASIC", "IAASB", "PCAOB", "UK FRC"),
    )
    cninfo_filing = _source_starts_with(
        item.source,
        ("巨潮资讯年报问询与审计回复",),
    )

    reasons: list[str] = []
    score = 0.0

    if generic_event_hits and not title_topic_hits:
        item.category = ItemCategory.OTHER
    elif non_public_audit_hits and not issuer_hits:
        item.category = ItemCategory.OTHER
    elif (dedicated_fraud or dedicated_audit) and procedural_hits:
        item.category = ItemCategory.OTHER
    elif cninfo_filing and item.category in {
        ItemCategory.PUBLIC_COMPANY_AUDIT,
        ItemCategory.REPORTING_CONTROLS,
    }:
        # A company/auditor reply may quote an exchange question containing
        # fraud or investigation language. Preserve the document's actual type
        # instead of presenting the quoted premise as an enforcement finding.
        item.category = item.category
    elif dedicated_fraud and item.category == ItemCategory.FRAUD_ENFORCEMENT:
        item.category = ItemCategory.FRAUD_ENFORCEMENT
    elif dedicated_audit and item.category == ItemCategory.PUBLIC_COMPANY_AUDIT:
        item.category = ItemCategory.PUBLIC_COMPANY_AUDIT
    elif (
        (item.category == ItemCategory.RESEARCH or item.source == "Google Scholar Alert")
        and research_hits
        and (fraud_hits or audit_hits or reporting_hits)
    ):
        item.category = ItemCategory.RESEARCH
    elif fraud_hits and (reporting_hits or issuer_hits or audit_hits):
        item.category = ItemCategory.FRAUD_ENFORCEMENT
    elif (
        audit_hits and (audit_quality_hits or reporting_hits or issuer_hits or audit_regulator)
    ) or (audit_regulator and audit_quality_hits):
        item.category = ItemCategory.PUBLIC_COMPANY_AUDIT
    elif reporting_hits and issuer_hits:
        item.category = ItemCategory.REPORTING_CONTROLS
    else:
        item.category = ItemCategory.OTHER

    if item.category == ItemCategory.FRAUD_ENFORCEMENT:
        score += 65
        score += min(24, 8 * len(fraud_hits))
        score += min(12, 3 * len(enforcement_hits))
        score += min(12, 4 * len(reporting_hits))
        if fraud_hits:
            reasons.append("财务造假：" + "、".join(fraud_hits))
        if enforcement_hits:
            reasons.append("执法进展：" + "、".join(enforcement_hits))
        if dedicated_fraud:
            reasons.append("会计审计执法专门栏目")
    elif item.category == ItemCategory.PUBLIC_COMPANY_AUDIT:
        score += 60
        score += min(24, 6 * len(audit_hits))
        score += min(20, 5 * len(audit_quality_hits))
        score += min(12, 3 * len(enforcement_hits))
        if audit_hits:
            reasons.append("上市公司审计：" + "、".join(audit_hits))
        if audit_quality_hits:
            reasons.append("审计质量：" + "、".join(audit_quality_hits))
        if dedicated_audit:
            reasons.append("审计监管专门栏目")
    elif item.category == ItemCategory.REPORTING_CONTROLS:
        score += 55
        score += min(24, 6 * len(reporting_hits))
        score += min(12, 4 * len(issuer_hits))
        reasons.append("财务报告与内控：" + "、".join(reporting_hits))
    elif item.category == ItemCategory.RESEARCH:
        score += 50
        score += min(24, 8 * len(research_hits))
        reasons.append("专题研究：" + "、".join(research_hits))

    if item.category != ItemCategory.OTHER and _is_authoritative(item):
        score += AUTHORITATIVE_SOURCE_BONUS
        reasons.append("第一方权威来源")
    if item.category != ItemCategory.OTHER and (dedicated_fraud or dedicated_audit):
        score += DEDICATED_SOURCE_BONUS

    if item.category != ItemCategory.OTHER and off_topic_hits:
        score -= OFF_TOPIC_PENALTY
        reasons.append("含非目标监管主题：" + "、".join(off_topic_hits))

    # Recency can order equally relevant items, but can never turn an unrelated
    # article into a candidate.
    if item.category != ItemCategory.OTHER:
        age_hours = max(0.0, (now - item.published_at).total_seconds() / 3600)
        recency_score = max(0.0, 15.0 - min(15.0, age_hours / 8))
        score += recency_score
        if recency_score >= 12:
            reasons.append("时效性高")

    item.score = round(max(0.0, score), 2)
    item.score_reasons = reasons
    return item


def select_candidates(items: list[ContentItem]) -> list[ContentItem]:
    """Select a focused, category-balanced pool without unrelated backfilling."""
    ranked = sorted(items, key=lambda item: (item.score, item.published_at), reverse=True)
    eligible = [
        item
        for item in ranked
        if item.category in CATEGORY_LIMITS and item.score >= MIN_RELEVANCE_SCORE
    ]

    selected: list[ContentItem] = []
    selected_ids: set[str] = set()
    source_counts: Counter[str] = Counter()

    def add(item: ContentItem) -> None:
        if len(selected) >= MAX_CANDIDATES:
            return
        if item.external_id in selected_ids:
            return
        if source_counts[item.source] >= MAX_ITEMS_PER_SOURCE:
            return
        selected.append(item)
        selected_ids.add(item.external_id)
        source_counts[item.source] += 1

    categorized = {
        category: [item for item in eligible if item.category == category]
        for category in CATEGORY_LIMITS
    }
    for index in range(max(CATEGORY_LIMITS.values())):
        for category, limit in CATEGORY_LIMITS.items():
            if index < limit and index < len(categorized[category]):
                add(categorized[category][index])

    # Use relevant overflow when one category is busy, while retaining the
    # per-source cap. There is intentionally no minimum-count padding.
    for item in eligible:
        add(item)

    return sorted(
        selected,
        key=lambda item: (item.score, item.published_at),
        reverse=True,
    )
