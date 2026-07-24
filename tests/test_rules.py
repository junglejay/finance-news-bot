from __future__ import annotations

from app.models import ItemCategory
from app.rules import (
    AI_OUTPUT_SCHEMA_TEMPLATE,
    AI_RULES,
    AI_TASK_DESCRIPTION,
    AUDIT_TERMS,
    CATEGORY_LIMITS,
    FRAUD_TERMS,
    MAX_AI_INPUT_CANDIDATES,
    MAX_ARTICLE_CHARS,
    MAX_CANDIDATES,
    MIN_ARTICLE_CHARS,
    PUBLIC_ARTICLE_DOMAINS,
    REPORTING_CONTROL_TERMS,
    RESEARCH_TERMS,
    SYSTEM_PROMPT,
)


def test_focused_keyword_dictionaries_are_non_empty() -> None:
    assert FRAUD_TERMS
    assert AUDIT_TERMS
    assert REPORTING_CONTROL_TERMS
    assert RESEARCH_TERMS


def test_only_focused_categories_receive_candidate_quotas() -> None:
    assert set(CATEGORY_LIMITS) == {
        ItemCategory.FRAUD_ENFORCEMENT,
        ItemCategory.PUBLIC_COMPANY_AUDIT,
        ItemCategory.REPORTING_CONTROLS,
        ItemCategory.RESEARCH,
    }
    assert ItemCategory.COMMODITY not in CATEGORY_LIMITS
    assert ItemCategory.MACRO not in CATEGORY_LIMITS
    assert all(limit > 0 for limit in CATEGORY_LIMITS.values())
    assert sum(CATEGORY_LIMITS.values()) >= MAX_CANDIDATES


def test_article_thresholds_are_consistent() -> None:
    assert 0 < MIN_ARTICLE_CHARS < MAX_ARTICLE_CHARS


def test_ai_prompt_is_specific_to_audit_and_enforcement() -> None:
    assert "财务舞弊" in SYSTEM_PROMPT
    assert "指控" in SYSTEM_PROMPT
    assert "上市公司审计" in AI_TASK_DESCRIPTION
    assert any("程序阶段" in rule for rule in AI_RULES)
    assert AI_OUTPUT_SCHEMA_TEMPLATE
    assert MAX_AI_INPUT_CANDIDATES >= 1


def test_public_article_domains_cover_core_regulators() -> None:
    assert {
        "sec.gov",
        "pcaobus.org",
        "frc.org.uk",
        "iaasb.org",
        "csrc.gov.cn",
        "mof.gov.cn",
    } <= PUBLIC_ARTICLE_DOMAINS
