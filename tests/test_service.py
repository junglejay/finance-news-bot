from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from app.dingtalk import DeliveryResult
from app.models import ArticleAnalysis, ContentItem, DeepReadingReport, ItemCategory
from app.service import BriefService
from app.sources import Source


class StaticSource(Source):
    name = "Fixture source"

    def __init__(self, items: list[ContentItem]) -> None:
        self.items = items

    async def fetch(self, since: datetime) -> list[ContentItem]:
        return self.items


class DuplicateSource(StaticSource):
    name = "Duplicate fixture source"


class FixtureGenerator:
    async def generate(self, report_date: date, candidates: list[ContentItem]) -> DeepReadingReport:
        commodity = next(item for item in candidates if item.category == ItemCategory.COMMODITY)
        return DeepReadingReport(
            report_date=report_date,
            analyses=[
                ArticleAnalysis(
                    title=commodity.title,
                    source=commodity.source,
                    url=commodity.url,
                    published_at=commodity.published_at,
                    core_thesis="The fixture contains a commodity development worth examining.",
                    fact_chain=["The fixture source supplied an oil-related update."],
                    detailed_reading=(
                        "The test generator turns the selected article into a detailed reading rather than a short card. "
                        "It explains that further evidence would be needed before drawing a market conclusion."
                    ),
                    transmission_or_risk=["Monitor later primary data for confirmation."],
                )
            ],
        )


class FixtureNotifier:
    def __init__(self) -> None:
        self.sent = 0
        self.faults = 0

    async def send_report(self, report: DeepReadingReport) -> DeliveryResult:
        self.sent += 1
        return DeliveryResult(200, {"errcode": 0})

    async def send_fault(self, message: str) -> DeliveryResult:
        self.faults += 1
        return DeliveryResult(200, {"errcode": 0})


@pytest.mark.asyncio
async def test_service_runs_full_pipeline_in_memory(settings) -> None:
    timestamp = datetime(2026, 7, 17, 0, 0, tzinfo=timezone.utc)
    items = [
        ContentItem(
            source="Forbes",
            category=ItemCategory.OTHER,
            title="Crude oil futures supply update",
            url="https://example.test/oil",
            summary="Oil supply",
            published_at=timestamp,
        ),
        ContentItem(
            source="SEC Press Releases",
            category=ItemCategory.RISK,
            title="SEC Charges company for accounting fraud",
            url="https://example.test/sec",
            summary="Financial statement fraud",
            published_at=timestamp,
        ),
        ContentItem(
            source="Google Scholar Alert",
            category=ItemCategory.RESEARCH,
            title="Commodity futures paper",
            url="https://example.test/paper",
            summary="Scholar Alert",
            published_at=timestamp,
        ),
    ]
    notifier = FixtureNotifier()
    service = BriefService(
        settings,
        [StaticSource(items), DuplicateSource([items[0]])],
        FixtureGenerator(),
        notifier,
    )

    result = await service.run_once(datetime(2026, 7, 17, 1, 0, tzinfo=timezone.utc))

    assert result.status == "success"
    assert result.collected_count == 3
    assert result.candidate_count == 3
    assert result.readable_count == 0
    assert notifier.sent == 1
