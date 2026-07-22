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


class ReportGenerationError(RuntimeError):
    pass


class ReportGenerator(Protocol):
    async def generate(self, report_date: date, candidates: list[ContentItem]) -> DeepReadingReport: ...


SYSTEM_PROMPT = """你是一名审慎的中文金融研究编辑。你要对输入中已公开读取到正文的原始文章做逐篇深度阅读，而不是写晨报、快讯或摘要卡片。

只能使用输入资料明确给出的事实；不得补充输入未支持的数字、指控、价格走势、公司事实或因果结论。必须用简体中文，保持客观，并清楚区分已披露事实、合理的研究推演和仍待核验的事项。正文内容必须用自己的话转述，绝不长句引用或大段复述原文。不要给出确定性的投资建议。

严格输出 JSON 对象，不使用 Markdown 代码块，也不输出任何额外文字。"""


MAX_AI_INPUT_CANDIDATES = 6
MAX_AI_ARTICLE_CHARS = 5_000
MIN_AI_OUTPUT_ARTICLES = 3
MAX_AI_OUTPUT_ARTICLES = 4


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


def _request_prompt(report_date: date, candidates: list[ContentItem]) -> str:
    schema = {
        "report_date": report_date.isoformat(),
        "analyses": [
            {
                "title": "必须逐字取自输入",
                "source": "必须逐字取自输入",
                "url": "必须逐字取自输入",
                "published_at": "必须逐字取自输入",
                "core_thesis": "用一段说明文章的核心命题",
                "fact_chain": ["按文章内容列出 3 至 6 个可追溯事实"],
                "detailed_reading": "2 至 4 个自然段，解释事实之间的关系、文章的意义与边界；只做转述和明确标示的推演",
                "transmission_or_risk": ["给出 1 至 4 条市场传导、监管风险或研究观察"],
                "limits_and_next_checks": ["可选：指出事实缺口、反证或下一步核验项"],
            }
        ],
        "disclaimer": "本文仅供研究参考，不构成投资建议。",
    }
    instructions = {
        "output_count": (
            "Return at least 3 and at most 4 distinct analyses. "
            "Do not return fewer than 3 analyses when the supplied source articles are available."
        ),
        "任务": (
            "从资本市场、公司治理、注册会计师、财务风险候选中，输出 1 至 4 篇最值得阅读的深度文章解读；"
            "重点关注审计、监管执法、财务舞弊、内部控制、AI、法律政策和执业准则。"
        ),
        "规则": [
            "每一篇 analyses 只能选择 full_text_available=true 的输入条目；没有可读取正文的条目不能进入输出。",
            "每一篇的 title、source、url、published_at 必须与一个输入条目完全一致。",
            "优先选择事实密度、研究价值最高的文章；不要为了凑数量而选题。",
            "fact_chain 只能包含输入正文或来源摘要能够支持的事实。",
            "detailed_reading 必须充分解释文章逻辑，不能退化为条目式简报，也不能复制原文表达。",
            "transmission_or_risk 可以做条件式研究推演，但必须说明其为观察而不是既成事实。",
            "不要把不同文章的事实混在同一篇分析中。",
            "无法得到完整正文的 Google Scholar Alert 和 Financial Times 线索不能进入深度阅读输出。",
        ],
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
        if len(readable_candidates) < MIN_AI_OUTPUT_ARTICLES:
            raise ReportGenerationError(
                f"at least {MIN_AI_OUTPUT_ARTICLES} publicly readable articles are required"
            )

        payload = {
            "model": self.settings.ai_model,
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": _request_prompt(report_date, readable_candidates)},
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
                if not MIN_AI_OUTPUT_ARTICLES <= len(report.analyses) <= MAX_AI_OUTPUT_ARTICLES:
                    raise ReportGenerationError(
                        f"AI gateway must return between {MIN_AI_OUTPUT_ARTICLES} and {MAX_AI_OUTPUT_ARTICLES} analyses"
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
