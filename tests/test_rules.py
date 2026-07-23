from __future__ import annotations

from app.models import ItemCategory
from app.rules import (
    AI_OUTPUT_SCHEMA_TEMPLATE,
    AI_RULES,
    AI_TASK_DESCRIPTION,
    CATEGORY_LIMITS,
    GOVERNANCE_AUDIT_ANCHORS,
    MAX_AI_INPUT_CANDIDATES,
    MAX_ARTICLE_CHARS,
    MAX_CANDIDATES,
    MIN_ARTICLE_CHARS,
    PUBLIC_ARTICLE_DOMAINS,
    RESEARCH_TERMS,
    RISK_ANCHORS,
    SYSTEM_PROMPT,
)


def test_keyword_dictionaries_are_non_empty() -> None:
    assert RISK_ANCHORS
    assert GOVERNANCE_AUDIT_ANCHORS
    assert RESEARCH_TERMS


def test_category_limits_cover_matchable_categories() -> None:
    # OTHER is the fallback bucket and intentionally has no quota.
    assert set(CATEGORY_LIMITS) == {
        ItemCategory.COMMODITY,
        ItemCategory.MACRO,
        ItemCategory.CAPITAL_MARKETS,
        ItemCategory.GOVERNANCE_AUDIT,
        ItemCategory.RISK,
        ItemCategory.POLICY_AI,
        ItemCategory.RESEARCH,
    }
    assert all(limit > 0 for limit in CATEGORY_LIMITS.values())
    assert sum(CATEGORY_LIMITS.values()) >= MAX_CANDIDATES


def test_article_thresholds_are_consistent() -> None:
    assert 0 < MIN_ARTICLE_CHARS < MAX_ARTICLE_CHARS


def test_ai_prompt_fragments_are_present() -> None:
    assert SYSTEM_PROMPT
    assert AI_TASK_DESCRIPTION
    assert AI_RULES
    assert AI_OUTPUT_SCHEMA_TEMPLATE
    assert MAX_AI_INPUT_CANDIDATES >= 1


def test_public_article_domains_allow_official_sources() -> None:
    assert "eia.gov" in PUBLIC_ARTICLE_DOMAINS
    assert "ecb.europa.eu" in PUBLIC_ARTICLE_DOMAINS
