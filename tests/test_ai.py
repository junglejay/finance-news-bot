from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, datetime, timezone

import pytest
import respx
from httpx import Response

from app.ai import (
    MAX_AI_ARTICLE_CHARS,
    BriefGenerationError,
    DeepReadingGenerator,
    OpenAICompatibleBriefGenerator,
    _candidate_payload,
)
from app.models import ContentItem, DeepReadingReport, ItemCategory
from app.rules import SYSTEM_PROMPT


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


@pytest.fixture
def anthropic_settings(settings):
    return replace(
        settings,
        ai_protocol="anthropic",
        ai_api_key="anthropic-test-token",
        ai_base_url="https://api.deepseek.com/anthropic",
        ai_model="deepseek-flash[1m]",
    )


@pytest.fixture
def readable_candidate() -> ContentItem:
    return ContentItem(
        source="Forbes",
        title="Crude oil futures update",
        url="https://example.test/oil",
        article_text="Public article text with enough material for an analysis. " * 10,
        published_at=datetime(2026, 7, 17, tzinfo=timezone.utc),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("versioned_base", [False, True])
@pytest.mark.parametrize("model", ["deepseek-flash[1m]", "deepseek-flash"])
@respx.mock
async def test_anthropic_messages_protocol_and_text_blocks(
    anthropic_settings, readable_candidate, versioned_base, model
) -> None:
    anthropic_settings = replace(anthropic_settings, ai_model=model)
    if versioned_base:
        anthropic_settings = replace(anthropic_settings, ai_base_url=anthropic_settings.ai_base_url + "/v1")
    report_json = json.dumps(
        {"report_date": "2026-07-17", "analyses": [_analysis(readable_candidate.url)]}
    )
    route = respx.post("https://api.deepseek.com/anthropic/v1/messages").mock(
        return_value=Response(200, json={
            "content": [
                {"type": "thinking", "thinking": "This is not part of the report."},
                {"type": "text", "text": report_json[:40]},
                {"type": "text", "text": report_json[40:]},
            ],
            "stop_reason": "end_turn",
        })
    )

    report = await DeepReadingGenerator(anthropic_settings).generate(
        date(2026, 7, 17), [readable_candidate]
    )

    assert len(report.analyses) == 1
    request = route.calls[0].request
    assert request.headers["authorization"] == "Bearer anthropic-test-token"
    assert request.headers["x-api-key"] == "anthropic-test-token"
    assert request.headers["anthropic-version"] == "2023-06-01"
    body = json.loads(request.content)
    assert body["model"] == "deepseek-flash"
    assert body["max_tokens"] == 32_768
    assert body["output_config"] == {"effort": "low"}
    assert body["system"] == SYSTEM_PROMPT
    assert [message["role"] for message in body["messages"]] == ["user"]
    assert "response_format" not in body
    prompt = json.loads(body["messages"][0]["content"])
    assert prompt["输入资料"][0]["article_text"] == readable_candidate.article_text


@pytest.mark.asyncio
@respx.mock
async def test_other_anthropic_gateways_keep_their_model_and_request_parameters(anthropic_settings) -> None:
    settings = replace(anthropic_settings, ai_base_url="https://gateway.example/anthropic", ai_model="custom-model[1m]")
    route = respx.post("https://gateway.example/anthropic/v1/messages").respond(
        200, json={"content": [{"type": "text", "text": "{}"}], "stop_reason": "end_turn"}
    )

    assert await DeepReadingGenerator(settings)._request_content("{}") == "{}"

    request = route.calls[0].request
    body = json.loads(request.content)
    assert body["model"] == "custom-model[1m]"
    assert body["max_tokens"] == 16_384
    assert "output_config" not in body
    assert "x-api-key" not in request.headers


@pytest.mark.asyncio
@respx.mock
async def test_deepseek_truncation_error_includes_token_usage(anthropic_settings) -> None:
    respx.post("https://api.deepseek.com/anthropic/v1/messages").respond(200, json={
        "content": [{"type": "text", "text": "incomplete"}],
        "stop_reason": "max_tokens",
        "usage": {"output_tokens": 32_768},
    })

    with pytest.raises(BriefGenerationError, match="limit=32768, output_tokens=32768"):
        await DeepReadingGenerator(anthropic_settings)._request_content("{}")


@pytest.mark.asyncio
@respx.mock
async def test_anthropic_retries_http_error(anthropic_settings, readable_candidate) -> None:
    route = respx.post("https://api.deepseek.com/anthropic/v1/messages").mock(
        side_effect=[
            Response(429, json={"error": {"message": "rate limited"}}),
            Response(200, json={"content": [{"type": "text", "text": json.dumps({
                "report_date": "2026-07-17", "analyses": [_analysis(readable_candidate.url)]
            })}]}),
        ]
    )

    report = await DeepReadingGenerator(anthropic_settings, attempts=2).generate(
        date(2026, 7, 17), [readable_candidate]
    )

    assert route.call_count == 2
    assert report.analyses[0].url == readable_candidate.url


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["truncated", "unknown_url", "http_error"])
@respx.mock
async def test_anthropic_failures_are_reported(anthropic_settings, readable_candidate, failure) -> None:
    if failure == "http_error":
        response = Response(401, text="invalid token anthropic-test-token")
        expected = "HTTP 401"
    else:
        url = "https://example.test/unknown" if failure == "unknown_url" else readable_candidate.url
        response = Response(200, json={
            "content": [{"type": "text", "text": json.dumps({
                "report_date": "2026-07-17", "analyses": [_analysis(url)]
            })}],
            "stop_reason": "max_tokens" if failure == "truncated" else "end_turn",
        })
        expected = "unknown URL" if failure == "unknown_url" else "max_tokens"
    respx.post("https://api.deepseek.com/anthropic/v1/messages").mock(return_value=response)

    with pytest.raises(BriefGenerationError, match=expected) as error:
        await DeepReadingGenerator(anthropic_settings, attempts=1).generate(
            date(2026, 7, 17), [readable_candidate]
        )
    assert "anthropic-test-token" not in str(error.value)


def test_report_model_accepts_six_selected_articles() -> None:
    report = DeepReadingReport.model_validate(
        {
            "report_date": "2026-07-17",
            "analyses": [
                _analysis(f"https://example.test/article-{index}")
                for index in range(6)
            ],
        }
    )

    assert len(report.analyses) == 6


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
async def test_generator_sends_only_top_ten_readable_candidates(settings) -> None:
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
        for score in range(1, 13)
    ]
    highest = candidates[-1]
    analyses = []
    for candidate in candidates[-4:]:
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

    request_body = __import__("json").loads(route.calls[0].request.content)
    prompt = __import__("json").loads(request_body["messages"][1]["content"])
    input_urls = {entry["url"] for entry in prompt["输入资料"]}
    assert len(report.analyses) == 4
    assert highest.url in {analysis.url for analysis in report.analyses}
    assert "https://example.test/oil-1" not in input_urls
    assert "https://example.test/oil-2" not in input_urls
    for score in range(3, 13):
        assert f"https://example.test/oil-{score}" in input_urls
