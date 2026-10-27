from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

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


@pytest.mark.asyncio
async def test_delivery_remembers_media_aliases_and_records_only_aggregate_metrics(settings, tmp_path):
    import json
    from app.history import DeliveryHistory
    now = datetime(2026, 10, 27, tzinfo=timezone.utc)
    primary_url = "https://www.sec.gov/newsroom/press-releases/financial-statement-fraud"
    media_url = "https://www.journalofaccountancy.com/news/fraud-coverage/"
    primary = ContentItem(
        source="SEC Press Releases", title="SEC charges Listed Company X with financial statement fraud",
        url=primary_url, published_at=now,
        article_text="The listed company overstated revenue in its financial statements through fictitious revenue. " * 10,
    )
    media = primary.model_copy(deep=True, update={"source": "Journal of Accountancy", "url": media_url,
                                               "metadata": {"primary_references": [primary_url]}})
    source = StaticSource([primary, media])
    source.name = primary.source
    metrics_path = tmp_path / "metrics.json"
    settings = replace(settings, source_metrics_file=str(metrics_path))
    history = DeliveryHistory(str(tmp_path / "history.json"))
    service = BriefService(settings, sources=[source], generator=FixtureGenerator(),
                           notifier=FixtureNotifier(), history=history)
    result = await service.run_once(now)
    assert result.status == "success"
    assert result.event_duplicate_count == 1
    assert {primary_url, media_url} <= history.delivered_urls(now)
    metrics = json.loads(metrics_path.read_text())
    counters = metrics["days"]["2026-10-27"][primary.source]
    assert counters["delivered_count"] == 1
    assert all(isinstance(value, int) for value in counters.values())


@pytest.mark.asyncio
async def test_service_passes_each_source_its_own_window(settings):
    calls = {}

    class RecordingSource(Source):
        def __init__(self, name):
            self.name = name

        async def fetch(self, since):
            calls[self.name] = since
            return []

    now = datetime(2026, 10, 20, 2, tzinfo=timezone.utc)
    service = BriefService(settings, sources=[
        RecordingSource("US DOJ Accounting Enforcement"),
        RecordingSource("HKEX Audit & Reporting"),
        RecordingSource("巨潮资讯年报问询与审计回复"),
    ], notifier=FixtureNotifier())
    result = await service.run_once(now)
    assert result.status == "no_update"
    assert calls == {
        "US DOJ Accounting Enforcement": now - timedelta(hours=168),
        "HKEX Audit & Reporting": now - timedelta(hours=24),
        "巨潮资讯年报问询与审计回复": now - timedelta(hours=24),
    }


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


@pytest.mark.asyncio
async def test_service_enforces_mainland_quota_across_same_day_reruns(
    settings,
    tmp_path,
) -> None:
    timestamp = datetime(2026, 7, 24, 0, 0, tzinfo=timezone.utc)
    settings = replace(
        settings,
        delivery_history_file=str(tmp_path / "delivered.json"),
    )
    items = [
        ContentItem(
            source="中国证监会行政处罚",
            title=f"Listed company financial statement fraud case {index}",
            url=f"https://example.test/mainland-case-{index}",
            summary="The issuer allegedly used fictitious revenue in its annual report.",
            published_at=timestamp,
        )
        for index in range(2)
    ]
    now = datetime(2026, 7, 24, 1, 0, tzinfo=timezone.utc)

    first_notifier = FixtureNotifier()
    first = BriefService(
        settings,
        [StaticSource(items)],
        FixtureGenerator(),
        first_notifier,
    )
    first_result = await first.run_once(now)

    second_notifier = FixtureNotifier()
    second = BriefService(
        settings,
        [StaticSource(items)],
        FixtureGenerator(),
        second_notifier,
    )
    second_result = await second.run_once(now)

    assert first_result.status == "success"
    assert first_result.candidate_count == 1
    assert second_result.status == "no_update"
    assert second_result.history_skipped_count == 1
    assert second_result.candidate_count == 0
    assert second_notifier.no_updates == 1
