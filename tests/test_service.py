from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from app.dingtalk import DeliveryResult
from app.models import BriefItem, ContentItem, DailyBrief, ItemCategory, ResearchBrief
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
    async def generate(self, report_date: date, candidates: list[ContentItem]) -> DailyBrief:
        commodity = next(item for item in candidates if item.category == ItemCategory.COMMODITY)
        risk = next(item for item in candidates if item.category == ItemCategory.RISK)
        research = next(item for item in candidates if item.category == ItemCategory.RESEARCH)
        return DailyBrief(
            report_date=report_date,
            key_judgements=["商品供需与公司治理风险均有新信息。", "仅依据已收集资料。"],
            commodity_items=[
                BriefItem(
                    title=commodity.title,
                    source=commodity.source,
                    url=commodity.url,
                    published_at=commodity.published_at,
                    what_happened="原油供应信息更新。",
                    why_it_matters="可能改变供需预期。",
                    market_impact="关注能源相关资产。",
                )
            ],
            risk_items=[
                BriefItem(
                    title=risk.title,
                    source=risk.source,
                    url=risk.url,
                    published_at=risk.published_at,
                    what_happened="监管发布会计执法信息。",
                    why_it_matters="提示财务报告风险。",
                    market_impact="关注同业治理质量。",
                )
            ],
            research_item=ResearchBrief(
                title=research.title,
                source=research.source,
                url=research.url,
                published_at=research.published_at,
                research_question="研究油价与期货。",
                key_finding="提醒邮件仅提供论文题目。",
                practical_implication="阅读原文后验证。",
            ),
        )


class FixtureNotifier:
    def __init__(self) -> None:
        self.sent = 0
        self.faults = 0

    async def send_brief(self, brief: DailyBrief) -> DeliveryResult:
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
    assert notifier.sent == 1
