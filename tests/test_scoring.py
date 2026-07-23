from __future__ import annotations

from datetime import datetime, timezone

from app.models import ContentItem, ItemCategory
from app.scoring import MAX_CANDIDATES, select_candidates, score_item


def _item(
    title: str,
    summary: str,
    *,
    source: str = "Forbes",
    url: str = "https://example.test/item",
) -> ContentItem:
    return ContentItem(
        source=source,
        title=title,
        url=url,
        summary=summary,
        published_at=datetime(2026, 7, 19, tzinfo=timezone.utc),
    )


def test_generic_demand_and_cost_do_not_make_an_article_a_commodity() -> None:
    item = score_item(
        _item(
            "Why concert tickets have become expensive",
            "Dynamic pricing, consumer demand, and production cost are driving ticket prices.",
        ),
        now=datetime(2026, 7, 19, 1, tzinfo=timezone.utc),
    )

    assert item.category == ItemCategory.OTHER
    assert item.score_reasons == ["时效性高"]


def test_commodity_anchor_allows_supply_context_to_increase_score() -> None:
    item = score_item(
        _item("Crude oil futures rise", "Oil supply and inventory changes affect futures."),
        now=datetime(2026, 7, 19, 1, tzinfo=timezone.utc),
    )

    assert item.category == ItemCategory.COMMODITY
    assert "商品/期货：crude、futures、oil" in item.score_reasons
    assert item.score > 55


def test_word_boundary_prevents_oil_matching_boiling() -> None:
    item = score_item(
        _item("Boiling water safety guide", "Consumer demand for kitchen equipment is rising."),
        now=datetime(2026, 7, 19, 1, tzinfo=timezone.utc),
    )

    assert item.category == ItemCategory.OTHER


def test_extended_commodity_vocabulary_covers_metals_and_agriculture() -> None:
    item = score_item(
        _item("Lithium and wheat futures advance", "Inventories fell while exports increased."),
        now=datetime(2026, 7, 19, 1, tzinfo=timezone.utc),
    )

    assert item.category == ItemCategory.COMMODITY
    assert "商品/期货：futures、lithium、wheat" in item.score_reasons


def test_expanded_priority_topics_receive_dedicated_categories() -> None:
    now = datetime(2026, 7, 19, 1, tzinfo=timezone.utc)
    capital_markets = score_item(
        _item("IPO and stock market listing", "The capital markets transaction is underway."), now=now
    )
    governance_audit = score_item(
        _item("Auditor independence reviewed by audit committee", "Internal control over financial reporting was assessed."), now=now
    )
    policy_ai = score_item(
        _item("AI regulation proposed", "The legal policy framework targets AI governance."), now=now
    )

    assert capital_markets.category == ItemCategory.CAPITAL_MARKETS
    assert governance_audit.category == ItemCategory.GOVERNANCE_AUDIT
    assert policy_ai.category == ItemCategory.POLICY_AI


def test_macro_market_driver_category() -> None:
    item = score_item(
        _item(
            "Federal Reserve signals interest rate cut",
            "Inflation and Treasury yields remain central to monetary policy.",
            source="Federal Reserve Press Releases",
            url="https://www.federalreserve.gov/newsevents/pressreleases/example.htm",
        ),
        now=datetime(2026, 7, 19, 1, tzinfo=timezone.utc),
    )

    assert item.category == ItemCategory.MACRO
    assert any(reason.startswith("宏观驱动：") for reason in item.score_reasons)
    assert "权威第一方来源" in item.score_reasons


def test_expanded_risk_vocabulary() -> None:
    item = score_item(
        _item(
            "Company investigates bribery and FCPA compliance failures",
            "The board disclosed a corruption investigation.",
        ),
        now=datetime(2026, 7, 19, 1, tzinfo=timezone.utc),
    )

    assert item.category == ItemCategory.RISK
    assert any(reason.startswith("财务造假与监管执法：") for reason in item.score_reasons)


def test_regulatory_other_items_fill_candidate_pool_to_twelve() -> None:
    now = datetime(2026, 7, 19, 1, tzinfo=timezone.utc)
    items = [
        score_item(
            _item(
                "Crude oil supply update",
                "Oil inventories declined.",
                url="https://example.test/oil",
            ),
            now=now,
        )
    ]
    items.extend(
        score_item(
            _item(
                f"Official statistical release {index}",
                "A first-party statistical update.",
                source="SEC Press Releases",
                url=f"https://www.sec.gov/newsroom/press-releases/{index}",
            ),
            now=now,
        )
        for index in range(11)
    )

    candidates = select_candidates(items)

    assert len(candidates) == 12
    assert all(
        item.category != ItemCategory.OTHER or "权威第一方来源" in item.score_reasons
        for item in candidates
    )


def test_candidate_pool_is_capped_at_sixteen() -> None:
    now = datetime(2026, 7, 19, 1, tzinfo=timezone.utc)
    items = [
        score_item(
            _item(
                f"Official statistical release {index}",
                "A first-party statistical update.",
                source="SEC Press Releases",
                url=f"https://www.sec.gov/newsroom/press-releases/{index}",
            ),
            now=now,
        )
        for index in range(20)
    ]

    candidates = select_candidates(items)

    assert len(candidates) == MAX_CANDIDATES
    assert all("权威第一方来源" in item.score_reasons for item in candidates)


def test_central_bank_other_no_longer_backfills_candidate_pool() -> None:
    now = datetime(2026, 7, 19, 1, tzinfo=timezone.utc)
    items = [
        score_item(
            _item(
                "Crude oil supply update",
                "Oil inventories declined.",
                url="https://example.test/oil",
            ),
            now=now,
        )
    ]
    items.extend(
        score_item(
            _item(
                f"Official statistical release {index}",
                "A first-party statistical update.",
                source="Federal Reserve Press Releases",
                url=f"https://www.federalreserve.gov/releases/{index}.htm",
            ),
            now=now,
        )
        for index in range(11)
    )

    candidates = select_candidates(items)

    # Central-bank/statistical-agency OTHER items no longer backfill the pool;
    # only the keyword-matched commodity article remains.
    assert len(candidates) == 1
    assert candidates[0].category == ItemCategory.COMMODITY


def test_fraud_scheme_vocabulary_routes_to_risk() -> None:
    item = score_item(
        _item(
            "Company booked fictitious revenue via channel stuffing",
            "Round-tripping inflated sales while related-party transactions were undisclosed.",
        ),
        now=datetime(2026, 7, 19, 1, tzinfo=timezone.utc),
    )

    assert item.category == ItemCategory.RISK
    assert any(reason.startswith("财务造假与监管执法：") for reason in item.score_reasons)


def test_regulatory_action_vocabulary_routes_to_risk() -> None:
    item = score_item(
        _item(
            "Regulator issues Wells notice and subpoena over disgorgement",
            "A cease-and-desist order and civil penalty followed the charges.",
        ),
        now=datetime(2026, 7, 19, 1, tzinfo=timezone.utc),
    )

    assert item.category == ItemCategory.RISK
    assert any(reason.startswith("财务造假与监管执法：") for reason in item.score_reasons)


def test_chinese_fraud_and_regulatory_terms_route_to_risk() -> None:
    item = score_item(
        _item(
            "某公司财务造假被立案调查",
            "证监会对其虚增收入行为作出行政处罚并下发警示函。",
        ),
        now=datetime(2026, 7, 19, 1, tzinfo=timezone.utc),
    )

    assert item.category == ItemCategory.RISK
    assert any(reason.startswith("财务造假与监管执法：") for reason in item.score_reasons)


def test_pure_audit_vocabulary_routes_to_governance_audit() -> None:
    item = score_item(
        _item(
            "Auditor independence and Sarbanes-Oxley 10-K review",
            "The audit committee assessed key audit matters.",
        ),
        now=datetime(2026, 7, 19, 1, tzinfo=timezone.utc),
    )

    assert item.category == ItemCategory.GOVERNANCE_AUDIT
    assert any(reason.startswith("上市公司审计与治理：") for reason in item.score_reasons)


def test_regulatory_source_bonus_is_consistent_across_risk_and_governance() -> None:
    now = datetime(2026, 7, 19, 1, tzinfo=timezone.utc)
    risk_item = score_item(
        _item(
            "Company restates financials after fraud",
            "Material weakness in internal control was cited.",
            source="SEC Press Releases",
            url="https://www.sec.gov/newsroom/press-releases/risk",
        ),
        now=now,
    )
    audit_item = score_item(
        _item(
            "PCAOB adopts new auditing standard",
            "Auditor independence and audit committee guidance updated.",
            source="PCAOB",
            url="https://pcaobus.org/news-events/news-releases/audit",
        ),
        now=now,
    )

    assert risk_item.category == ItemCategory.RISK
    assert audit_item.category == ItemCategory.GOVERNANCE_AUDIT
    # 两类监管来源都只加一次“第一方监管来源”，不再叠加“权威第一方来源”15 分
    assert "第一方监管来源" in risk_item.score_reasons
    assert "第一方监管来源" in audit_item.score_reasons
    assert "权威第一方来源" not in risk_item.score_reasons
    assert "权威第一方来源" not in audit_item.score_reasons
