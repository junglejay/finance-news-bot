from __future__ import annotations

from datetime import datetime, timezone

from app.models import ContentItem, ItemCategory
from app.scoring import score_item


def _item(title: str, summary: str) -> ContentItem:
    return ContentItem(
        source="Forbes",
        title=title,
        url="https://example.test/item",
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
