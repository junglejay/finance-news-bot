from __future__ import annotations

from dataclasses import replace
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
        fraud = next(item for item in candidates if item.category == ItemCategory.FRAUD_ENFORCEMENT)
        return DeepReadingReport(
            report_date=report_date,
            analyses=[
                ArticleAnalysis(
                    title=fraud.title,
                    source=fraud.source,
                    url=fraud.url,
                    published_at=fraud.published_at,
                    core_thesis="The fixture contains a financial reporting enforcement action.",
                    fact_chain=["The fixture source supplied an accounting-fraud update."],
                    detailed_reading=(
                        "The test generator turns the selected article into a detailed reading rather than a short card. "
                        "It explains that the procedural stage and underlying evidence must be checked before drawing conclusions."
                    ),
                    transmission_or_risk=["Monitor later regulatory orders for confirmation."],
                )
            ],
        )


class FixtureNotifier:
    def __init__(self) -> None:
        self.sent = 0
        self.faults = 0
        self.no_updates = 0

    async def send_report(self, report: DeepReadingReport) -> DeliveryResult:
        self.sent += 1
        return DeliveryResult(200, {"errcode": 0})

    async def send_fault(self, message: str) -> DeliveryResult:
        self.faults += 1
        return DeliveryResult(200, {"errcode": 0})

    async def send_no_update(self, report_date: str) -> DeliveryResult:
        self.no_updates += 1
        return DeliveryResult(200, {"errcode": 0})


@pytest.mark.asyncio
async def test_service_runs_full_pipeline_in_memory(settings) -> None:
    timestamp = datetime(2026, 7, 17, 0, 0, tzinfo=timezone.utc)
    items = [
        ContentItem(
            source="General Business",
            category=ItemCategory.OTHER,
            title="Central bank rate update",
            url="https://example.test/rates",
            summary="Inflation and interest rates",
            published_at=timestamp,
        ),
        ContentItem(
            source="SEC Press Releases",
            category=ItemCategory.OTHER,
            title="SEC charges listed company with financial statement fraud",
            url="https://example.test/sec",
            summary="The issuer allegedly used fictitious revenue in its annual report.",
            published_at=timestamp,
        ),
        ContentItem(
            source="Google Scholar Alert",
            category=ItemCategory.RESEARCH,
            title="Unrelated commodity futures paper",
            url="https://example.test/paper",
            summary="Scholar Alert about oil.",
            published_at=timestamp,
        ),
    ]
    notifier = FixtureNotifier()
    service = BriefService(
        settings,
        [StaticSource(items), DuplicateSource([items[1]])],
        FixtureGenerator(),
        notifier,
    )

    result = await service.run_once(datetime(2026, 7, 17, 1, 0, tzinfo=timezone.utc))

    assert result.status == "success"
    assert result.collected_count == 3
    assert result.candidate_count == 1
    assert result.readable_count == 0
    assert notifier.sent == 1


@pytest.mark.asyncio
async def test_service_reports_no_update_without_calling_ai(settings) -> None:
    timestamp = datetime(2026, 7, 17, 0, 0, tzinfo=timezone.utc)
    notifier = FixtureNotifier()
    service = BriefService(
        settings,
        [
            StaticSource(
                [
                    ContentItem(
                        source="General Business",
                        title="Oil prices rise after inventory report",
                        url="https://example.test/oil",
                        summary="Commodity market update.",
                        published_at=timestamp,
                    )
                ]
            )
        ],
        FixtureGenerator(),
        notifier,
    )

    result = await service.run_once(datetime(2026, 7, 17, 1, tzinfo=timezone.utc))

    assert result.status == "no_update"
    assert result.candidate_count == 0
    assert notifier.no_updates == 1
    assert notifier.sent == 0
    assert notifier.faults == 0


@pytest.mark.asyncio
async def test_service_skips_urls_delivered_by_an_earlier_run(settings, tmp_path) -> None:
    timestamp = datetime(2026, 7, 17, 0, 0, tzinfo=timezone.utc)
    settings = replace(
        settings,
        delivery_history_file=str(tmp_path / "delivered.json"),
    )

    def relevant_item() -> ContentItem:
        return ContentItem(
            source="SEC Press Releases",
            title="SEC charges listed company with financial statement fraud",
            url="https://example.test/repeated",
            summary="The issuer allegedly used fictitious revenue in its annual report.",
            published_at=timestamp,
        )

    first_notifier = FixtureNotifier()
    first = BriefService(
        settings,
        [StaticSource([relevant_item()])],
        FixtureGenerator(),
        first_notifier,
    )
    first_result = await first.run_once(
        datetime(2026, 7, 17, 1, tzinfo=timezone.utc)
    )

    second_notifier = FixtureNotifier()
    second = BriefService(
        settings,
        [StaticSource([relevant_item()])],
        FixtureGenerator(),
        second_notifier,
    )
    second_result = await second.run_once(
        datetime(2026, 7, 18, 1, tzinfo=timezone.utc)
    )

    assert first_result.status == "success"
    assert second_result.status == "no_update"
    assert second_result.history_skipped_count == 1
    assert second_notifier.no_updates == 1
