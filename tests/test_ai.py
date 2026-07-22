from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
import respx
from httpx import Response

from app.ai import (
    MAX_AI_ARTICLE_CHARS,
    BriefGenerationError,
    OpenAICompatibleBriefGenerator,
    _candidate_payload,
)
from app.models import ContentItem, ItemCategory


def _analysis(url: str) -> dict[str, object]:
    return {
        "title": "Crude oil futures update",
        "source": "Forbes",
        "url": url,
        "published_at": "2026-07-17T00:00:00+00:00",
        "core_thesis": "The article describes a change in the oil supply backdrop.",
        "fact_chain": ["The source describes an oil supply update."],
        "detailed_reading": (
            "The available article text links the supply update to current market conditions. "
            "It does not establish a price outcome, so the implication remains a research observation rather than a forecast."
        ),
        "transmission_or_risk": ["Researchers can monitor whether later inventory data confirms the stated supply change."],
        "limits_and_next_checks": ["Confirm the scale and timing in the linked source and subsequent official data."],
    }


@pytest.mark.asyncio
@respx.mock
async def test_openai_compatible_generator_validates_and_returns_deep_reading(settings) -> None:
    published = datetime(2026, 7, 17, 0, 0, tzinfo=timezone.utc)
    candidate = ContentItem(
        source="Forbes",
        category=ItemCategory.COMMODITY,
        title="Crude oil futures update",
        url="https://example.test/oil",
        summary="Oil supply update",
        article_text="The public article says an oil supply development may affect market conditions. " * 10,
        published_at=published,
        score=75,
    )
    candidates = [
        candidate,
        candidate.model_copy(update={"url": "https://example.test/oil-2"}),
        candidate.model_copy(update={"url": "https://example.test/oil-3"}),
    ]
    response = {
        "choices": [
            {
                "message": {
                    "content": str(
                        {
                            "report_date": "2026-07-17",
                            "analyses": [_analysis(item.url) for item in candidates],
                            "disclaimer": "For research only.",
                        }
                    ).replace("'", '"')
                }
            }
        ]
    }
    route = respx.post("https://api.deepseek.test/v1/chat/completions").mock(
        return_value=Response(200, json=response)
    )

    report = await OpenAICompatibleBriefGenerator(settings).generate(date(2026, 7, 17), candidates)

    assert route.called
    assert report.analyses[0].url == candidate.url
    assert "Deep" in report.to_markdown() or "深度" in report.to_markdown()


@pytest.mark.asyncio
@respx.mock
async def test_openai_compatible_generator_rejects_hallucinated_source_url(settings) -> None:
    candidate = ContentItem(
        source="Forbes",
        category=ItemCategory.COMMODITY,
        title="Crude oil futures update",
        url="https://example.test/oil",
        summary="Oil supply update",
        article_text="Public article text with enough material for an analysis. " * 10,
        published_at=datetime(2026, 7, 17, tzinfo=timezone.utc),
    )
    candidates = [
        candidate,
        candidate.model_copy(update={"url": "https://example.test/oil-2"}),
        candidate.model_copy(update={"url": "https://example.test/oil-3"}),
    ]
    analysis = _analysis("https://example.test/not-a-source")
    response = {
        "choices": [
            {
                "message": {
                    "content": __import__("json").dumps(
                        {
                            "report_date": "2026-07-17",
                            "analyses": [_analysis(candidates[0].url), _analysis(candidates[1].url), analysis],
                        }
                    )
                }
            }
        ]
    }
    respx.post("https://api.deepseek.test/v1/chat/completions").mock(return_value=Response(200, json=response))

    with pytest.raises(BriefGenerationError, match="unknown URL"):
        await OpenAICompatibleBriefGenerator(settings, attempts=1).generate(date(2026, 7, 17), candidates)


@pytest.mark.asyncio
async def test_generator_requires_publicly_readable_article(settings) -> None:
    candidate = ContentItem(
        source="Forbes",
        title="No body",
        url="https://example.test/no-body",
        published_at=datetime(2026, 7, 17, tzinfo=timezone.utc),
    )

    with pytest.raises(BriefGenerationError, match="publicly readable"):
        await OpenAICompatibleBriefGenerator(settings).generate(date(2026, 7, 17), [candidate])


@pytest.mark.asyncio
@respx.mock
async def test_generator_rejects_fewer_analyses_than_available_candidates(settings) -> None:
    candidate = ContentItem(
        source="Forbes",
        category=ItemCategory.COMMODITY,
        title="Crude oil futures update",
        url="https://example.test/oil",
        summary="Oil supply update",
        article_text="Public article text with enough material for an analysis. " * 10,
        published_at=datetime(2026, 7, 17, tzinfo=timezone.utc),
    )
    candidates = [
        candidate,
        candidate.model_copy(update={"url": "https://example.test/oil-2"}),
        candidate.model_copy(update={"url": "https://example.test/oil-3"}),
    ]
    response = {
        "choices": [
            {
                "message": {
                    "content": __import__("json").dumps(
                        {"report_date": "2026-07-17", "analyses": [_analysis(candidate.url)]}
                    )
                }
            }
        ]
    }
    respx.post("https://api.deepseek.test/v1/chat/completions").mock(return_value=Response(200, json=response))

    with pytest.raises(BriefGenerationError, match="between 3 and 3"):
        await OpenAICompatibleBriefGenerator(settings, attempts=1).generate(date(2026, 7, 17), candidates)


@pytest.mark.asyncio
@respx.mock
async def test_generator_returns_all_articles_when_fewer_than_four_are_readable(settings) -> None:
    candidate = ContentItem(
        source="Forbes",
        category=ItemCategory.COMMODITY,
        title="Crude oil futures update",
        url="https://example.test/oil",
        summary="Oil supply update",
        article_text="Public article text with enough material for an analysis. " * 10,
        published_at=datetime(2026, 7, 17, tzinfo=timezone.utc),
    )
    candidates = [candidate, candidate.model_copy(update={"url": "https://example.test/oil-2"})]
    response = {
        "choices": [
            {
                "message": {
                    "content": __import__("json").dumps(
                        {
                            "report_date": "2026-07-17",
                            "analyses": [_analysis(item.url) for item in candidates],
                        }
                    )
                }
            }
        ]
    }
    respx.post("https://api.deepseek.test/v1/chat/completions").mock(return_value=Response(200, json=response))

    report = await OpenAICompatibleBriefGenerator(settings).generate(date(2026, 7, 17), candidates)

    assert len(report.analyses) == 2


def test_candidate_payload_limits_article_text_length() -> None:
    candidate = ContentItem(
        source="Forbes",
        title="Long article",
        url="https://example.test/long",
        article_text="x" * (MAX_AI_ARTICLE_CHARS + 1),
        published_at=datetime(2026, 7, 17, tzinfo=timezone.utc),
    )

    payload = _candidate_payload([candidate])

    assert len(payload[0]["article_text"]) == MAX_AI_ARTICLE_CHARS


@pytest.mark.asyncio
@respx.mock
async def test_generator_sends_only_top_six_readable_candidates(settings) -> None:
    published = datetime(2026, 7, 17, tzinfo=timezone.utc)
    candidates = [
        ContentItem(
            source="Forbes",
            category=ItemCategory.COMMODITY,
            title=f"Crude oil futures update {score}",
            url=f"https://example.test/oil-{score}",
            article_text=(f"Public article {score}. " * 400),
            published_at=published,
            score=score,
        )
        for score in range(1, 8)
    ]
    highest = candidates[-1]
    analyses = []
    for candidate in candidates[-3:]:
        analysis = _analysis(candidate.url)
        analysis["title"] = candidate.title
        analyses.append(analysis)
    response = {
        "choices": [
            {"message": {"content": __import__("json").dumps({"report_date": "2026-07-17", "analyses": analyses})}}
        ]
    }
    route = respx.post("https://api.deepseek.test/v1/chat/completions").mock(
        return_value=Response(200, json=response)
    )

    report = await OpenAICompatibleBriefGenerator(settings).generate(date(2026, 7, 17), candidates)

    request_body = route.calls[0].request.content.decode()
    assert len(report.analyses) == 3
    assert highest.url in {analysis.url for analysis in report.analyses}
    assert "https://example.test/oil-1" not in request_body
    for score in range(2, 8):
        assert f"https://example.test/oil-{score}" in request_body
