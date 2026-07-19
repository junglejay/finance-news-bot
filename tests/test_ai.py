from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
import respx
from httpx import Response

from app.ai import BriefGenerationError, OpenAICompatibleBriefGenerator
from app.models import ContentItem, ItemCategory


@pytest.mark.asyncio
@respx.mock
async def test_openai_compatible_generator_validates_and_returns_structured_brief(settings) -> None:
    published = datetime(2026, 7, 17, 0, 0, tzinfo=timezone.utc)
    candidate = ContentItem(
        source="Forbes",
        category=ItemCategory.COMMODITY,
        title="Crude oil futures update",
        url="https://example.test/oil",
        summary="Oil supply update",
        published_at=published,
        score=75,
    )
    response = {
        "choices": [
            {
                "message": {
                    "content": """
                    {
                      "report_date": "2026-07-17",
                      "key_judgements": ["原油供给信息值得持续跟踪。"],
                      "commodity_items": [{
                        "title": "Crude oil futures update",
                        "source": "Forbes",
                        "url": "https://example.test/oil",
                        "published_at": "2026-07-17T00:00:00+00:00",
                        "what_happened": "来源披露了原油供给更新。",
                        "why_it_matters": "可能影响供需预期。",
                        "market_impact": "关注能源相关资产。"
                      }],
                      "risk_items": [],
                      "research_item": null,
                      "disclaimer": "本简报仅供研究参考，不构成投资建议。"
                    }
                    """
                }
            }
        ]
    }
    route = respx.post("https://api.deepseek.test/v1/chat/completions").mock(
        return_value=Response(200, json=response)
    )

    brief = await OpenAICompatibleBriefGenerator(settings).generate(date(2026, 7, 17), [candidate])

    assert route.called
    assert brief.commodity_items[0].url == candidate.url
    assert "原油" in brief.key_judgements[0]


@pytest.mark.asyncio
@respx.mock
async def test_openai_compatible_generator_rejects_hallucinated_source_url(settings) -> None:
    candidate = ContentItem(
        source="Forbes",
        category=ItemCategory.COMMODITY,
        title="Gold futures update",
        url="https://example.test/gold",
        summary="Gold futures",
        published_at=datetime(2026, 7, 17, tzinfo=timezone.utc),
    )
    response = {
        "choices": [
            {
                "message": {
                    "content": """
                    {"report_date":"2026-07-17","key_judgements":["x"],"commodity_items":[{
                    "title":"Gold futures update","source":"Forbes","url":"https://example.test/not-a-source",
                    "published_at":"2026-07-17T00:00:00+00:00","what_happened":"x","why_it_matters":"x","market_impact":"x"}],
                    "risk_items":[],"research_item":null}
                    """
                }
            }
        ]
    }
    respx.post("https://api.deepseek.test/v1/chat/completions").mock(return_value=Response(200, json=response))

    with pytest.raises(BriefGenerationError, match="unknown URL"):
        await OpenAICompatibleBriefGenerator(settings, attempts=1).generate(date(2026, 7, 17), [candidate])
