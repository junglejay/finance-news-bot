"""Deterministic relevance scoring before AI interpretation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

from .models import ContentItem, ItemCategory


COMMODITY_ANCHORS = {
    "commodity",
    "commodities",
    "futures",
    "crude",
    "oil",
    "wti",
    "brent",
    "opec",
    "gold",
    "bullion",
    "refinery",
    "natural gas",
    "lng",
    "copper",
}
COMMODITY_CONTEXT_TERMS = {
    "supply",
    "demand",
    "inventory",
    "production cost",
    "output",
}
RISK_ANCHORS = {
    "fraud",
    "accounting",
    "financial statement",
    "audit",
    "auditing",
    "internal control",
    "material weakness",
    "restatement",
    "misstatement",
    "whistleblower",
}
RISK_CONTEXT_TERMS = {
    "enforcement",
    "charges",
    "disclosure",
}
RESEARCH_TERMS = {
    "commodity futures",
    "crude oil",
    "gold",
    "financial statement fraud",
    "internal control",
    "forensic accounting",
    "asset pricing",
    "quantitative finance",
}


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


def score_item(item: ContentItem, now: datetime | None = None) -> ContentItem:
    now = now or datetime.now(timezone.utc)
    text = f"{item.title}\n{item.summary}".lower()
    commodity_anchor_hits = _matches(text, COMMODITY_ANCHORS)
    commodity_context_hits = _matches(text, COMMODITY_CONTEXT_TERMS)
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
    elif risk_anchor_hits and len(risk_anchor_hits) >= len(commodity_anchor_hits):
        item.category = ItemCategory.RISK
        score += min(45, 12 * len(risk_anchor_hits))
        score += min(10, 2 * len(risk_context_hits))
        reasons.append("舞弊/内控：" + "、".join(risk_anchor_hits))
        if item.source.startswith(("SEC", "PCAOB", "CFTC")):
            score += 25
            reasons.append("第一方监管来源")
    elif commodity_anchor_hits:
        item.category = ItemCategory.COMMODITY
        score += min(45, 12 * len(commodity_anchor_hits))
        score += min(10, 2 * len(commodity_context_hits))
        reasons.append("商品/期货：" + "、".join(commodity_anchor_hits))
    else:
        item.category = ItemCategory.OTHER

    age_hours = max(0.0, (now - item.published_at).total_seconds() / 3600)
    recency_score = max(0.0, 20.0 - min(20.0, age_hours / 3))
    score += recency_score
    if recency_score >= 15:
        reasons.append("时效性高")

    item.score = round(score, 2)
    item.score_reasons = reasons
    return item


def select_candidates(items: list[ContentItem]) -> list[ContentItem]:
    """Return a compact, category-balanced input set for the model."""
    ranked = sorted(items, key=lambda item: (item.score, item.published_at), reverse=True)
    commodity = [item for item in ranked if item.category == ItemCategory.COMMODITY][:4]
    risk = [item for item in ranked if item.category == ItemCategory.RISK][:4]
    research = [item for item in ranked if item.category == ItemCategory.RESEARCH][:2]
    return commodity + risk + research
