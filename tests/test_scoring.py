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
    assert any(reason.startswith("舞弊/内控：") for reason in item.score_reasons)


def test_authoritative_other_items_fill_candidate_pool_to_twelve() -> None:
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
                source="Federal Reserve Press Releases",
                url=f"https://www.federalreserve.gov/releases/{index}.htm",
            ),
            now=now,
        )
        for index in range(20)
    ]

    candidates = select_candidates(items)

    assert len(candidates) == MAX_CANDIDATES
    assert all("权威第一方来源" in item.score_reasons for item in candidates)
