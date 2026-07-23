"""Deterministic relevance scoring before AI interpretation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlsplit

from .models import ContentItem, ItemCategory
from .rules import (
    AUDIT_FRAUD_ANCHOR_CAP,
    AUDIT_FRAUD_ANCHOR_WEIGHT,
    AUDIT_FRAUD_CONTEXT_CAP,
    AUDIT_FRAUD_CONTEXT_WEIGHT,
    REGULATORY_SOURCE_BONUS,
    AUTHORITATIVE_DOMAINS,
    AUTHORITATIVE_SOURCE_PREFIXES,
    CAPITAL_MARKETS_ANCHORS,
    CATEGORY_LIMITS,
    COMMODITY_ANCHORS,
    COMMODITY_CONTEXT_TERMS,
    GOVERNANCE_AUDIT_ANCHORS,
    MACRO_ANCHORS,
    MACRO_CONTEXT_TERMS,
    MAX_CANDIDATES,
    MIN_CANDIDATES,
    POLICY_AI_ANCHORS,
    REGULATORY_SOURCE_PREFIXES,
    RESEARCH_TERMS,
    RISK_ANCHORS,
    RISK_CONTEXT_TERMS,
)


@dataclass(frozen=True, slots=True)
class ScoredItem:
    item: ContentItem
    score: float


def _matches(text: str, terms: set[str]) -> list[str]:
    """Match full terms only, so e.g. `oil` does not match `boiling`."""
    return sorted(
        term
        for term in terms
        if re.search(rf"(?<!\w){re.escape(term)}(?!\w)", text, flags=re.IGNORECASE)
    )


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
    return any(hostname == domain or hostname.endswith(f".{domain}") for domain in AUTHORITATIVE_DOMAINS)


def score_item(item: ContentItem, now: datetime | None = None) -> ContentItem:
    now = now or datetime.now(timezone.utc)
    text = f"{item.title}\n{item.summary}".lower()
    commodity_anchor_hits = _matches(text, COMMODITY_ANCHORS)
    commodity_context_hits = _matches(text, COMMODITY_CONTEXT_TERMS)
    capital_market_hits = _matches(text, CAPITAL_MARKETS_ANCHORS)
    governance_audit_hits = _matches(text, GOVERNANCE_AUDIT_ANCHORS)
    policy_ai_hits = _matches(text, POLICY_AI_ANCHORS)
    macro_anchor_hits = _matches(text, MACRO_ANCHORS)
    macro_context_hits = _matches(text, MACRO_CONTEXT_TERMS)
    risk_anchor_hits = _matches(text, RISK_ANCHORS)
    risk_context_hits = _matches(text, RISK_CONTEXT_TERMS)
    research_hits = _matches(text, RESEARCH_TERMS)
    reasons: list[str] = []
    score = 0.0

    if item.category == ItemCategory.RESEARCH or item.source == "Google Scholar Alert":
        item.category = ItemCategory.RESEARCH
        score += 35
        if research_hits:
            score += min(35, 8 * len(research_hits))
            reasons.append("研究主题：" + "、".join(research_hits))
    elif governance_audit_hits:
        item.category = ItemCategory.GOVERNANCE_AUDIT
        score += min(AUDIT_FRAUD_ANCHOR_CAP, AUDIT_FRAUD_ANCHOR_WEIGHT * len(governance_audit_hits))
        score += min(AUDIT_FRAUD_CONTEXT_CAP, AUDIT_FRAUD_CONTEXT_WEIGHT * len(risk_context_hits))
        reasons.append("治理、审计与财务风险：" + "、".join(governance_audit_hits))
        if _source_starts_with(item.source, REGULATORY_SOURCE_PREFIXES):
            score += REGULATORY_SOURCE_BONUS
            reasons.append("一手监管来源")
    elif capital_market_hits:
        item.category = ItemCategory.CAPITAL_MARKETS
        score += min(45, 12 * len(capital_market_hits))
        score += min(10, 2 * len(macro_context_hits))
        reasons.append("资本市场：" + "、".join(capital_market_hits))
    elif policy_ai_hits:
        item.category = ItemCategory.POLICY_AI
        score += min(45, 12 * len(policy_ai_hits))
        score += min(10, 2 * len(macro_context_hits))
        reasons.append("法律政策与 AI：" + "、".join(policy_ai_hits))
    elif risk_anchor_hits and len(risk_anchor_hits) >= max(
        len(commodity_anchor_hits), len(macro_anchor_hits)
    ):
        item.category = ItemCategory.RISK
        score += min(AUDIT_FRAUD_ANCHOR_CAP, AUDIT_FRAUD_ANCHOR_WEIGHT * len(risk_anchor_hits))
        score += min(AUDIT_FRAUD_CONTEXT_CAP, AUDIT_FRAUD_CONTEXT_WEIGHT * len(risk_context_hits))
        reasons.append("舞弊/内控：" + "、".join(risk_anchor_hits))
        if _source_starts_with(item.source, REGULATORY_SOURCE_PREFIXES):
            score += REGULATORY_SOURCE_BONUS
            reasons.append("第一方监管来源")
    elif commodity_anchor_hits and len(commodity_anchor_hits) >= len(macro_anchor_hits):
        item.category = ItemCategory.COMMODITY
        score += min(45, 12 * len(commodity_anchor_hits))
        score += min(10, 2 * len(commodity_context_hits))
        reasons.append("商品/期货：" + "、".join(commodity_anchor_hits))
    elif macro_anchor_hits:
        item.category = ItemCategory.MACRO
        score += min(45, 12 * len(macro_anchor_hits))
        score += min(10, 2 * len(macro_context_hits))
        reasons.append("宏观驱动：" + "、".join(macro_anchor_hits))
    else:
        item.category = ItemCategory.OTHER

    if _is_authoritative(item) and "第一方监管来源" not in reasons:
        score += 15
        reasons.append("权威第一方来源")

    age_hours = max(0.0, (now - item.published_at).total_seconds() / 3600)
    recency_score = max(0.0, 20.0 - min(20.0, age_hours / 3))
    score += recency_score
    if recency_score >= 15:
        reasons.append("时效性高")

    item.score = round(score, 2)
    item.score_reasons = reasons
    return item


def select_candidates(items: list[ContentItem]) -> list[ContentItem]:
    """Return up to 16 balanced candidates, with first-party sources as fallback."""
    ranked = sorted(items, key=lambda item: (item.score, item.published_at), reverse=True)
    selected: list[ContentItem] = []
    selected_ids: set[str] = set()

    def add(item: ContentItem) -> None:
        if len(selected) < MAX_CANDIDATES and item.external_id not in selected_ids:
            selected.append(item)
            selected_ids.add(item.external_id)

    # Rotate through category quotas so newer priority topics receive a place
    # even when another category has enough items to fill MAX_CANDIDATES alone.
    categorized = {
        category: [item for item in ranked if item.category == category]
        for category in CATEGORY_LIMITS
    }
    for index in range(max(CATEGORY_LIMITS.values())):
        for category, limit in CATEGORY_LIMITS.items():
            if index < limit and index < len(categorized[category]):
                add(categorized[category][index])
            if len(selected) >= MAX_CANDIDATES:
                break
        if len(selected) >= MAX_CANDIDATES:
            break

    # Regulatory agencies (CFTC/PCAOB/SEC) can matter before they use the
    # vocabulary above, so preserve their releases even when deterministic
    # scoring leaves them as OTHER. Central banks and statistical agencies
    # (Fed/ECB/BIS/...) only enter when they match at least one keyword, to
    # avoid padding the pool with unrelated statistical releases.
    for item in ranked:
        if _is_authoritative(item) and (
            item.category != ItemCategory.OTHER
            or _source_starts_with(item.source, REGULATORY_SOURCE_PREFIXES)
        ):
            add(item)

    # If one category is unusually busy, use its overflow to reach the target
    # instead of padding the set with unrelated, non-authoritative business news.
    if len(selected) < MIN_CANDIDATES:
        for item in ranked:
            if item.category != ItemCategory.OTHER:
                add(item)
            if len(selected) >= MIN_CANDIDATES:
                break

    return sorted(
        selected,
        key=lambda item: (item.score, item.published_at),
        reverse=True,
    )
