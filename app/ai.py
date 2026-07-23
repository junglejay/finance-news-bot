"""OpenAI-compatible generation of traceable Chinese deep-reading reports."""

from __future__ import annotations

import asyncio
import json
import re
from datetime import date
from typing import Protocol

from openai import APIConnectionError, APIError, APIStatusError, AsyncOpenAI
from pydantic import ValidationError

from .config import Settings
from .models import ArticleAnalysis, ContentItem, DeepReadingReport
from .rules import (
    AI_OUTPUT_SCHEMA_TEMPLATE,
    AI_RULES,
    AI_TASK_DESCRIPTION,
    MAX_AI_ARTICLE_CHARS,
    MAX_AI_INPUT_CANDIDATES,
    MAX_AI_OUTPUT_ARTICLES,
    MIN_AI_OUTPUT_ARTICLES,
    SYSTEM_PROMPT,
)


class ReportGenerationError(RuntimeError):
    pass


class ReportGenerator(Protocol):
    async def generate(self, report_date: date, candidates: list[ContentItem]) -> DeepReadingReport: ...


def _output_count_bounds(available_articles: int) -> tuple[int, int]:
    """Return every available article when there are four or fewer."""
    if available_articles <= MAX_AI_OUTPUT_ARTICLES:
        return available_articles, available_articles
    return MIN_AI_OUTPUT_ARTICLES, MAX_AI_OUTPUT_ARTICLES


def _candidate_payload(candidates: list[ContentItem]) -> list[dict[str, object]]:
    return [
        {
            "source": item.source,
            "category": item.category.value,
            "title": item.title,
            "url": item.url,
            "published_at": item.published_at.isoformat(),
            "score": item.score,
            "score_reasons": item.score_reasons,
            "source_summary": item.summary[:2_000],
            "full_text_available": bool(item.article_text),
            "article_text": item.article_text[:MAX_AI_ARTICLE_CHARS],
        }
        for item in candidates
    ]


def _request_prompt(
    report_date: date,
    candidates: list[ContentItem],
    min_analyses: int,
    max_analyses: int,
) -> str:
    schema = {
        "report_date": report_date.isoformat(),
        "analyses": [AI_OUTPUT_SCHEMA_TEMPLATE],
        "disclaimer": "本文仅供研究参考，不构成投资建议。",
    }
    instructions = {
        "output_count": (
            f"Return between {min_analyses} and {max_analyses} distinct analyses. "
            "When both values are the same, return exactly that number of analyses."
        ),
        "任务": AI_TASK_DESCRIPTION,
        "规则": AI_RULES,
        "目标 JSON": schema,
        "输入资料": _candidate_payload(candidates),
    }
    return json.dumps(instructions, ensure_ascii=False)


def _extract_json(content: str) -> dict:
    trimmed = content.strip()
    if trimmed.startswith("```"):
        trimmed = re.sub(r"^```(?:json)?\s*|\s*```$", "", trimmed, flags=re.IGNORECASE)
    try:
        parsed = json.loads(trimmed)
    except json.JSONDecodeError as exc:
        raise ReportGenerationError("AI gateway did not return valid JSON") from exc
    if not isinstance(parsed, dict):
        raise ReportGenerationError("AI gateway returned JSON that is not an object")
    return parsed


def _validate_source_references(report: DeepReadingReport, candidates: list[ContentItem]) -> None:
    source_by_url = {item.url: item for item in candidates}
    seen_urls: set[str] = set()
    for analysis in report.analyses:
        source_item = source_by_url.get(analysis.url)
        if source_item is None:
            raise ReportGenerationError(f"report references an unknown URL: {analysis.url}")
        if not source_item.article_text:
            raise ReportGenerationError("report referenced an item whose full article was not available")
        if analysis.url in seen_urls:
            raise ReportGenerationError("report contains the same source article more than once")
        seen_urls.add(analysis.url)
        if analysis.title != source_item.title or analysis.source != source_item.source:
            raise ReportGenerationError("report altered a source title or attribution")
        if analysis.published_at.date() != source_item.published_at.date():
            raise ReportGenerationError("report altered a source publication date")


class OpenAICompatibleDeepReadingGenerator:
    def __init__(self, settings: Settings, attempts: int = 3) -> None:
        self.settings = settings
        self.attempts = attempts

    async def generate(self, report_date: date, candidates: list[ContentItem]) -> DeepReadingReport:
        if not self.settings.ai_api_key:
            raise ReportGenerationError("AI_API_KEY is not configured")
        readable_candidates = sorted(
            (item for item in candidates if item.article_text),
            key=lambda item: (item.score, item.published_at),
            reverse=True,
        )[:MAX_AI_INPUT_CANDIDATES]
        if not readable_candidates:
            raise ReportGenerationError("no publicly readable full articles were collected")
        min_analyses, max_analyses = _output_count_bounds(len(readable_candidates))

        payload = {
            "model": self.settings.ai_model,
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": _request_prompt(report_date, readable_candidates, min_analyses, max_analyses),
                },
            ],
        }
        last_error: Exception | None = None
        for attempt in range(1, self.attempts + 1):
            try:
                async with AsyncOpenAI(
                    api_key=self.settings.ai_api_key,
                    base_url=self.settings.ai_base_url,
                    timeout=60,
                    max_retries=0,
                ) as client:
                    response = await client.chat.completions.create(**payload)
                content = response.choices[0].message.content
                if not content:
                    raise ReportGenerationError("AI gateway returned an empty response")
                report = DeepReadingReport.model_validate(_extract_json(content))
                if report.report_date != report_date:
                    raise ReportGenerationError("AI gateway returned the wrong report date")
                if not min_analyses <= len(report.analyses) <= max_analyses:
                    raise ReportGenerationError(
                        f"AI gateway must return between {min_analyses} and {max_analyses} analyses"
                    )
                _validate_source_references(report, readable_candidates)
                return report
            except (
                APIConnectionError,
                APIStatusError,
                APIError,
                IndexError,
                TypeError,
                ValidationError,
                ReportGenerationError,
            ) as exc:
                last_error = exc
                if attempt < self.attempts:
                    await asyncio.sleep(attempt)
        raise ReportGenerationError(f"AI generation failed after {self.attempts} attempts: {last_error}")


# Backward-compatible aliases for callers that imported the old names.
BriefGenerationError = ReportGenerationError
BriefGenerator = ReportGenerator
OpenAICompatibleBriefGenerator = OpenAICompatibleDeepReadingGenerator
